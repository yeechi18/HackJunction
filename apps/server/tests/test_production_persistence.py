"""M4 real-Postgres persistence tests with injected, offline providers."""

from contextlib import AsyncExitStack
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pwdlib import PasswordHash
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from company.api.production import router
from company.cli.init_db import initialize_database
from company.main import create_app
from company.persistence.database import Database
from company.persistence.models import AuthSession, Command, InputSnapshot, Order, now
from company.persistence.production import (
    DeliverableVersion,
    Evidence,
    ProductionStepReceipt,
    ResearchPackage,
)
from company.providers.mimo import ProviderError
from company.services.access import token_hash
from company.services.production import generation_step, research_step, review_step

pytestmark = pytest.mark.integration


def listing(quantity=4):
    return {
        "title": f"{quantity}-Pack Silicone Cable Clips",
        "bullets": [
            "For cables up to 6 mm in diameter.",
            "Organize the desktop.",
            "Keep cables close.",
            "For indoor use.",
            "Check the cable size before use.",
        ],
        "description": "A desktop cable organizer.",
        "faqs": [],
        "claims": [
            {
                "field_path": "title",
                "text": f"{quantity}-Pack Silicone Cable Clips",
                "fact_ids": ["fact:pack_quantity", "fact:material"],
            },
            {"field_path": "bullets[0]", "text": "6 mm", "fact_ids": ["fact:cable_diameter"]},
        ],
        "image_plan": [
            {
                "purpose": "main",
                "layout": "Use the seller's reference image.",
                "text_overlay": "",
                "fact_ids": ["fact:material"],
            }
        ],
    }


class ProductionStub:
    def __init__(self, *, wrong_first=False, hook=None, fail=False):
        self.calls = []
        self.generations = 0
        self.wrong_first, self.hook, self.fail = wrong_first, hook, fail

    async def generate_json(self, schema, messages, **kwargs):
        self.calls.append(schema.__name__)
        if self.fail:
            raise ProviderError("Injected provider failure; no remote request was made.")
        if self.hook:
            hook, self.hook = self.hook, None
            await hook()
        if schema.__name__ == "ResearchDraft":
            output = {
                "buyer_questions": [
                    {
                        "question": "What cable diameter fits?",
                        "applicable": True,
                        "evidence_ids": [],
                        "missing_facts": [],
                        "follow_up_questions": [],
                        "target_fields": ["bullets", "faqs"],
                    }
                ],
                "market_notes": [],
                "missing_facts": [],
                "follow_up_questions": [],
            }
        elif schema.__name__ == "ListingDraft":
            self.generations += 1
            output = listing(6 if self.wrong_first and self.generations == 1 else 4)
        else:
            output = {"passed": True, "issues": [], "buyer_question_coverage": []}
        return schema.model_validate(output), SimpleNamespace(
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            model="injected-production-model",
            elapsed_seconds=0.25,
        )


@pytest.fixture
async def production_context(postgres_test_settings):
    settings = postgres_test_settings.model_copy(
        update={
            "session_secret": SecretStr("test-production-secret-at-least-thirty-two-bytes"),
            "admin_password_hash": SecretStr(
                PasswordHash.recommended().hash("test-production-owner")
            ),
        }
    )
    await initialize_database(settings)
    database = Database(settings)
    order_id, command_id, snapshot_id = uuid4(), uuid4(), uuid4()
    async with database.sessions() as session, session.begin():
        session.add(
            Order(
                id=order_id,
                seller_name="M4 Test",
                product_name="Demo cable clips",
                phase="RESEARCH",
                revision=1,
                is_demo=True,
            )
        )
        await session.flush()
        session.add(
            InputSnapshot(
                id=snapshot_id,
                order_id=order_id,
                revision=1,
                source="seller_form",
                facts=[
                    {"key": "material", "value": "silicone", "confirmed": True},
                    {"key": "package_quantity", "value": "4", "confirmed": True},
                    {"key": "cable_diameter", "value": "6", "unit": "mm", "confirmed": True},
                ],
                notes=None,
            )
        )
        session.add(
            Command(
                id=command_id,
                order_id=order_id,
                kind="ACCEPT",
                expected_revision=1,
                idempotency_key=f"test:{uuid4()}",
                request_hash="x" * 64,
                payload={},
                status="applied",
            )
        )
    owner_tokens = []
    try:
        yield settings, database, order_id, command_id, snapshot_id, owner_tokens
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(Order).where(Order.id == order_id))
            if owner_tokens:
                await session.execute(
                    delete(AuthSession).where(
                        AuthSession.token_hash.in_([token_hash(value) for value in owner_tokens])
                    )
                )
        await database.close()


async def test_receipts_cache_and_multiple_rounds_preserve_immutable_versions(production_context):
    _, database, order_id, command_id, snapshot_id, _ = production_context
    provider = ProductionStub(wrong_first=True)
    research = await research_step(order_id, command_id, provider, database=database)
    assert research.result["suggested_listing_price_range"] is None
    again = await research_step(order_id, command_id, provider, database=database)
    assert again.cache_hit and again.artifact_id == research.artifact_id
    first = await generation_step(order_id, command_id, 0, provider, database=database)
    first_review = await review_step(
        order_id, command_id, first.version_id, provider, database=database
    )
    assert not first_review.result["text_passed"]
    second = await generation_step(order_id, command_id, 1, provider, database=database)
    second_review = await review_step(
        order_id, command_id, second.version_id, provider, database=database
    )
    assert second_review.result["text_passed"]
    assert not second_review.result["passed"]
    count = len(provider.calls)
    replay = await generation_step(order_id, command_id, 0, provider, database=database)
    replay_review = await review_step(
        order_id, command_id, second.version_id, provider, database=database
    )
    assert replay.version_id == first.version_id and replay.cache_hit
    assert replay_review.artifact_id == second_review.artifact_id and replay_review.cache_hit
    assert len(provider.calls) == count
    async with database.sessions() as session:
        versions = list(
            await session.scalars(
                select(DeliverableVersion)
                .where(DeliverableVersion.order_id == order_id)
                .order_by(DeliverableVersion.version_number)
            )
        )
        assert [version.version_number for version in versions] == [1, 2]
        assert versions[1].parent_version_id == versions[0].id
        assert versions[0].draft["title"].startswith("6-Pack")
        assert versions[1].draft["title"].startswith("4-Pack")
        assert all("6 mm" in version.draft["bullets"][0] for version in versions)
        assert all(version.input_snapshot_id == snapshot_id for version in versions)
        assert versions[1].assets == [] and not versions[1].full_passed
        receipts = list(
            await session.scalars(
                select(ProductionStepReceipt).where(ProductionStepReceipt.order_id == order_id)
            )
        )
        assert len(receipts) == 5 and all(row.status == "succeeded" for row in receipts)
        assert all(row.cost_usd is None and row.usage["total_tokens"] == 15 for row in receipts)
        assert (
            await session.get(Order, order_id)
        ).phase == "RESEARCH"  # Graph alone advances phase.


async def test_changed_evidence_during_model_rejects_result_but_records_usage(production_context):
    _, database, order_id, command_id, _, _ = production_context

    async def add_evidence():
        async with database.sessions() as session, session.begin():
            session.add(
                Evidence(
                    order_id=order_id,
                    kind="manual_observation",
                    source="test source",
                    observed_at=now(),
                    scope="this fictional SKU",
                    content="New competitor observation",
                    is_demo=True,
                    data={"price": "9.99", "currency": "USD"},
                )
            )

    provider = ProductionStub(hook=add_evidence)
    with pytest.raises(ProviderError, match="changed"):
        await research_step(order_id, command_id, provider, database=database)
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ResearchPackage)
                .where(ResearchPackage.order_id == order_id)
            )
            == 0
        )
        receipt = await session.scalar(
            select(ProductionStepReceipt).where(ProductionStepReceipt.order_id == order_id)
        )
        assert receipt.status == "stale" and receipt.usage["total_tokens"] == 15
    # A retry that never committed an artifact can re-read the new immutable evidence snapshot.
    retry = await research_step(order_id, command_id, provider, database=database)
    assert retry.result["suggested_listing_price_range"]["low"] == "9.99"
    async with database.sessions() as session:
        package = await session.get(ResearchPackage, retry.artifact_id)
        assert package.evidence_snapshot[0]["content"] == "New competitor observation"


async def test_provider_failure_is_visible_without_creating_fake_artifact(production_context):
    _, database, order_id, command_id, _, _ = production_context
    with pytest.raises(ProviderError, match="Injected"):
        await research_step(order_id, command_id, ProductionStub(fail=True), database=database)
    async with database.sessions() as session:
        receipt = await session.scalar(
            select(ProductionStepReceipt).where(ProductionStepReceipt.order_id == order_id)
        )
        assert receipt.status == "failed" and receipt.artifact_id is None
        assert receipt.cost_usd is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DeliverableVersion)
                .where(DeliverableVersion.order_id == order_id)
            )
            == 0
        )


async def test_manual_evidence_and_artifacts_are_owner_only(production_context):
    settings, database, order_id, command_id, _, owner_tokens = production_context
    app = create_app(settings)
    if "/api/v1/internal/orders/{order_id}/production" not in app.openapi()["paths"]:
        app.include_router(router, prefix="/api/v1")
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=settings.public_app_url
            )
        )
        path = f"/api/v1/internal/orders/{order_id}"
        assert (await client.get(f"{path}/production")).status_code == 401
        login = await client.post(
            "/api/v1/internal/auth/login",
            json={"username": "owner", "password": "test-production-owner"},
        )
        assert login.status_code == 200
        owner_tokens.append(client.cookies.get("owner_session"))
        body = {
            "source": "Fictional manual source",
            "content": "Comparable desk clips",
            "is_demo": True,
            "data": {"price": "9.99", "currency": "USD"},
        }
        assert (await client.post(f"{path}/evidence", json=body)).status_code == 403
        response = await client.post(
            f"{path}/evidence", json=body, headers={"X-CSRF-Token": login.json()["csrf_token"]}
        )
        assert response.status_code == 201, response.text
        assert response.json()["provider"] == "manual"
        assert len((await client.get(f"{path}/evidence")).json()["items"]) == 1
        await research_step(order_id, command_id, ProductionStub(), database=database)
        await generation_step(order_id, command_id, 0, ProductionStub(), database=database)
        production = await client.get(f"{path}/production")
        assert production.status_code == 200, production.text
        result = production.json()
        assert len(result["research"]) == 1 and len(result["versions"]) == 1
        assert len(result["runs"]) == 2 and result["reviews"] == []
        assert result["versions"][0]["status"] == "draft"
        assert result["runs"][0]["cost_usd"] is None
