"""M4 graph acceptance: typed offline model, actual PostgreSQL and persistent checkpoints."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import SecretStr
from sqlalchemy import delete, select

from company.cli.init_db import initialize_database
from company.main import create_app
from company.persistence.database import Database, checkpoint_conninfo
from company.persistence.models import Command, InputSnapshot, Job, Order, now
from company.persistence.production import DeliverableVersion, ProductionStepReceipt
from company.schemas.commands import DecisionInput
from company.schemas.orders import CreateOrder
from company.services.orders import create_order, enqueue_decision
from company.services.workflow import process_one_job

pytestmark = pytest.mark.integration


class TypedProductionProvider:
    def __init__(self, *, wrong_first=True, always_wrong=False, hook=None):
        self.calls = []
        self.generations = 0
        self.wrong_first, self.always_wrong, self.hook = wrong_first, always_wrong, hook

    async def generate_json(self, schema, messages, **kwargs):
        self.calls.append(schema.__name__)
        if schema.__name__ == "_IntakeDraft":
            output = {
                "ready_for_acceptance": True,
                "missing_fields": [],
                "conflicts": [],
                "questions": [],
                "summary": "资料已整理，请负责人决定是否接单。",
            }
        elif schema.__name__ == "ResearchDraft":
            output = {
                "buyer_questions": [
                    {
                        "question": "How many clips?",
                        "applicable": True,
                        "evidence_ids": [],
                        "missing_facts": [],
                        "follow_up_questions": [],
                        "target_fields": ["title"],
                    }
                ],
                "market_notes": [],
                "missing_facts": [],
                "follow_up_questions": [],
            }
        elif schema.__name__ == "ListingDraft":
            self.generations += 1
            quantity = 6 if self.always_wrong or (self.wrong_first and self.generations == 1) else 4
            output = {
                "title": f"{quantity}-Pack Silicone Cable Clips",
                "bullets": [
                    "For cables up to 6 mm in diameter.",
                    "Keep cables nearby.",
                    "Organize the desktop.",
                    "For indoor use.",
                    "Check cable size first.",
                ],
                "description": "A desktop cable organizer.",
                "faqs": [],
                "claims": [
                    {
                        "field_path": "title",
                        "text": f"{quantity}-Pack Silicone Cable Clips",
                        "fact_ids": ["fact:pack_quantity", "fact:material"],
                    },
                    {
                        "field_path": "bullets[0]",
                        "text": "6 mm",
                        "fact_ids": ["fact:cable_diameter"],
                    },
                ],
                "image_plan": [
                    {
                        "purpose": "main",
                        "layout": "Use the supplied product reference.",
                        "text_overlay": "",
                        "fact_ids": ["fact:material"],
                    }
                ],
            }
            if self.hook:
                hook, self.hook = self.hook, None
                await hook()
        elif schema.__name__ == "ReviewDraft":
            output = {"passed": True, "issues": [], "buyer_question_coverage": []}
        else:
            raise AssertionError(f"Unexpected production schema: {schema.__name__}")
        return schema.model_validate(output), SimpleNamespace(
            usage={"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
            model="offline-multi-role",
            elapsed_seconds=0.1,
        )


def facts():
    return [
        {"key": "material", "value": "silicone", "confirmed": True},
        {"key": "pack_quantity", "value": "4", "confirmed": True},
        {"key": "dimensions", "value": "25 x 25 x 15", "unit": "mm", "confirmed": True},
        {"key": "cable_diameter", "value": "6", "unit": "mm", "confirmed": True},
        {"key": "use_cases", "value": "indoor desk cable organization", "confirmed": True},
    ]


@pytest.fixture
async def context(postgres_test_settings):
    settings = postgres_test_settings.model_copy(
        update={"session_secret": SecretStr("m4-workflow-test-secret-of-at-least-thirty-two-bytes")}
    )
    await initialize_database(settings)
    database = Database(settings)
    identifiers = []

    async def new_order():
        async with database.sessions() as session:
            created = await create_order(
                session,
                settings,
                CreateOrder(
                    product_details="Known test product specifications.",
                    product_name="虚构理线夹",
                    facts=facts(),
                ),
            )
        identifiers.append(created.id)
        return created.id

    try:
        yield settings, database, new_order
    finally:
        # Delete only each test's own graph thread and order, never reset the shared test DB.
        async with AsyncPostgresSaver.from_conn_string(checkpoint_conninfo(settings)) as saver:
            for identifier in identifiers:
                await saver.adelete_thread(str(identifier))
        async with database.sessions() as session, session.begin():
            await session.execute(delete(Order).where(Order.id.in_(identifiers)))
        await database.close()


async def accept(database, order_id):
    async with database.sessions() as session:
        return await enqueue_decision(
            session,
            order_id,
            DecisionInput(action="accept", expected_revision=1),
            "m4-workflow-accept",
        )


async def versions(database, order_id):
    async with database.sessions() as session:
        return list(
            await session.scalars(
                select(DeliverableVersion)
                .where(DeliverableVersion.order_id == order_id)
                .order_by(DeliverableVersion.version_number)
            )
        )


async def test_default_graph_accepts_then_repairs_and_waits_without_full_image_pass(context):
    settings, database, create = context
    order_id = await create()
    provider = TypedProductionProvider()
    assert await process_one_job(
        settings, provider=provider, order_id=order_id, include_images=False
    )
    queued = await accept(database, order_id)
    assert await process_one_job(
        settings, provider=provider, order_id=order_id, include_images=False
    )
    generated = await versions(database, order_id)
    assert [version.version_number for version in generated] == [1, 2]
    assert generated[0].draft["title"].startswith("6-Pack")
    assert generated[1].draft["title"].startswith("4-Pack")
    assert all("6 mm" in version.draft["bullets"][0] for version in generated)
    assert generated[1].text_passed and not generated[1].full_passed
    async with database.sessions() as session:
        order = await session.get(Order, order_id)
        assert order.phase == "AWAITING_RELEASE" and order.pending_command_id is None
        assert (await session.get(Command, queued.command_id)).status == "applied"
        assert (
            len(
                list(
                    await session.scalars(
                        select(ProductionStepReceipt).where(
                            ProductionStepReceipt.order_id == order_id
                        )
                    )
                )
            )
            == 5
        )
        with pytest.raises(HTTPException) as rejection:
            await enqueue_decision(
                session,
                order_id,
                DecisionInput(action="release", expected_revision=1, version_id=generated[1].id),
                "premature-release",
            )
        assert rejection.value.status_code == 409
    before = len(provider.calls)
    assert not await process_one_job(
        settings, provider=provider, order_id=order_id, include_images=False
    )
    async with database.sessions() as session, session.begin():
        session.add(Job(order_id=order_id, command_id=queued.command_id, kind="RESUME"))
    assert await process_one_job(
        settings, provider=provider, order_id=order_id, include_images=False
    )
    assert len(provider.calls) == before
    assert len(await versions(database, order_id)) == 2


async def test_three_candidate_budget_stops_without_reset_or_automatic_retry(context):
    settings, database, create = context
    order_id = await create()
    provider = TypedProductionProvider(always_wrong=True)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    queued = await accept(database, order_id)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    assert provider.generations == 3
    assert len(await versions(database, order_id)) == 3
    async with database.sessions() as session:
        assert (await session.get(Order, order_id)).phase == "REVIEW"
        assert (await session.get(Command, queued.command_id)).status == "failed"
        job = await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        assert job.status == "failed"
    assert not await process_one_job(
        settings, provider=provider, order_id=order_id, include_images=False
    )
    assert provider.generations == 3


async def test_generation_receipt_survives_checkpoint_write_failure(context, monkeypatch):
    settings, database, create = context
    order_id = await create()
    provider = TypedProductionProvider(wrong_first=False)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    queued = await accept(database, order_id)
    original = AsyncPostgresSaver.aput
    fault = {"injected": False}

    async def fail_after_version(self, config, checkpoint, metadata, new_versions):
        state = checkpoint.get("channel_values", {})
        if not fault["injected"] and state.get("phase") == "REVIEW" and state.get("version_id"):
            fault["injected"] = True
            raise RuntimeError("Injected checkpoint failure after durable generation")
        return await original(self, config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(AsyncPostgresSaver, "aput", fail_after_version)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    assert fault["injected"] and len(await versions(database, order_id)) == 1
    async with database.sessions() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        assert job.status == "queued"
        job.available_at = now()
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    assert provider.generations == 1
    assert len(await versions(database, order_id)) == 1
    async with database.sessions() as session:
        assert (await session.get(Order, order_id)).phase == "AWAITING_RELEASE"


async def test_changed_revision_during_generation_does_not_override_new_phase(context):
    settings, database, create = context
    order_id = await create()

    async def change_inputs():
        async with database.sessions() as session, session.begin():
            order = await session.get(Order, order_id)
            order.revision, order.phase = 2, "NEEDS_INFO"
            session.add(
                InputSnapshot(
                    order_id=order_id,
                    revision=2,
                    facts=facts(),
                    notes="Changed while provider was running",
                    source="seller_supplement",
                )
            )

    provider = TypedProductionProvider(hook=change_inputs)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    queued = await accept(database, order_id)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    assert await versions(database, order_id) == []
    async with database.sessions() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        job.available_at = now()
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    async with database.sessions() as session:
        order = await session.get(Order, order_id)
        assert order.revision == 2 and order.phase == "NEEDS_INFO"
        assert (await session.get(Command, queued.command_id)).status == "stale"
    assert provider.generations == 1


async def test_manual_start_is_removed_and_acceptance_still_starts_production(context):
    settings, database, create = context
    order_id = await create()
    app = create_app(settings)
    assert "/api/v1/internal/orders/{order_id}/production/start" not in app.openapi()["paths"]
    provider = TypedProductionProvider(wrong_first=False)
    await process_one_job(settings, provider=provider, order_id=order_id)
    await accept(database, order_id)
    await process_one_job(settings, provider=provider, order_id=order_id, include_images=False)
    async with database.sessions() as session:
        assert (await session.get(Order, order_id)).phase == "AWAITING_RELEASE"
