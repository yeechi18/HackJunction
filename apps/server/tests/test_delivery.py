"""Delivery gates and frozen packages, with explicit DB fixtures rather than real AI calls."""

import io
import json
from contextlib import AsyncExitStack
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from company.api.deliverables import router as delivery_router
from company.cli.init_db import initialize_database
from company.config import Settings
from company.main import create_app
from company.persistence.assets import Asset
from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import (
    AuthSession,
    Command,
    HumanDecision,
    InputSnapshot,
    Message,
    Order,
    OrderEvent,
)
from company.persistence.production import DeliverableVersion, ResearchPackage, ReviewReport
from company.providers.storage import LocalAssetStorage
from company.schemas.orders import CreateOrder, ProductFact
from company.services.access import create_session, token_hash
from company.services.delivery import DeliveryError, release_version, veto_version
from company.services.orders import create_order
from company.services.production import digest

pytestmark = pytest.mark.integration


def small_image(color="#364c65"):
    image = Image.new("RGB", (80, 80), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
async def delivery_context(postgres_test_settings: Settings, tmp_path):
    settings = postgres_test_settings.model_copy(
        update={
            "asset_dir": tmp_path / "assets",
            "session_secret": SecretStr("delivery-test-session-secret-over-thirty-two-bytes"),
        }
    )
    await initialize_database(settings)
    app = create_app(settings)
    if "/api/v1/orders/{order_id}/deliverables" not in app.openapi()["paths"]:
        app.include_router(delivery_router, prefix="/api/v1")
        app.openapi_schema = None
    order_ids, tokens = [], []
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))

        async def candidate():
            async with app.state.database.sessions() as session:
                created = await create_order(
                    session,
                    settings,
                    CreateOrder(
                        product_details="Known test product specifications.",
                        product_name="Fictional desk clips",
                        facts=[
                            ProductFact(key="material", value="silicone", confirmed=True),
                            ProductFact(key="pack_quantity", value="6", confirmed=True),
                            ProductFact(
                                key="dimensions", value="25 x 25 x 15", unit="mm", confirmed=True
                            ),
                            ProductFact(
                                key="use_cases", value="indoor desk cables", confirmed=True
                            ),
                        ],
                    ),
                )
            order_ids.append(created.id)
            async with app.state.database.sessions() as session, session.begin():
                order = await session.get(Order, created.id)
                snapshot = await session.scalar(
                    select(InputSnapshot).where(
                        InputSnapshot.order_id == order.id,
                        InputSnapshot.revision == 1,
                    )
                )
                start = await session.scalar(
                    select(Command).where(
                        Command.order_id == order.id,
                        Command.kind == "START",
                    )
                )
                research = ResearchPackage(
                    id=uuid4(),
                    order_id=order.id,
                    command_id=start.id,
                    input_snapshot_id=snapshot.id,
                    input_revision=1,
                    evidence_hash="e" * 64,
                    evidence_snapshot=[],
                    operation_key=str(uuid4()),
                    result={
                        "pricing_notes": "Fictional supplied observation range.",
                        "suggested_listing_price_range": {
                            "low": "9.99",
                            "high": "12.99",
                            "currency": "USD",
                            "is_demo": True,
                        },
                        "usage": {"total_tokens": 123},
                        "diagnostics": "private research data",
                    },
                )
                session.add(research)
                await session.flush()
                version = DeliverableVersion(
                    id=uuid4(),
                    order_id=order.id,
                    command_id=start.id,
                    version_number=1,
                    round_index=0,
                    parent_version_id=None,
                    input_snapshot_id=snapshot.id,
                    research_package_id=research.id,
                    draft={
                        "title": "Desktop Cable Clips",
                        "bullets": ["Organize desk cables."] * 5,
                        "description": "For indoor desk cable organization.",
                        "faqs": [{"question": "How many clips?", "answer": "Six clips."}],
                        "claims": [
                            {
                                "field_path": "faqs[0].answer",
                                "text": "Six clips.",
                                "fact_ids": ["fact:pack_quantity"],
                            }
                        ],
                        "image_plan": [
                            {
                                "purpose": "main",
                                "layout": "Preserve the product reference.",
                                "text_overlay": "",
                                "fact_ids": [],
                            },
                            {
                                "purpose": "infographic",
                                "layout": "Product details.",
                                "text_overlay": "",
                                "fact_ids": [],
                            },
                        ],
                    },
                    assets=[],
                    status="review_passed",
                    text_passed=True,
                    full_passed=True,
                    operation_key=str(uuid4()),
                )
                session.add(version)
                await session.flush()
                images = []
                storage = LocalAssetStorage(settings.asset_dir)
                for index, kind in enumerate(["main_candidate", "infographic"]):
                    stored = storage.put_image(small_image(["#364c65", "#ddc9aa"][index]))
                    image = Asset(
                        id=uuid4(),
                        order_id=order.id,
                        version_id=version.id,
                        kind=kind,
                        method="fixture_template",
                        storage_key=stored.storage_key,
                        sha256=stored.sha256,
                        media_type="image/png",
                        byte_size=stored.byte_size,
                        width=stored.width,
                        height=stored.height,
                        is_demo=False,
                        image_metadata={},
                        delivered_at=None,
                    )
                    session.add(image)
                    images.append(image)
                version.assets = [str(image.id) for image in images]
                image_hash = digest(
                    [(str(storage.resolve(image.storage_key)), image.sha256) for image in images]
                )
                review = ReviewReport(
                    id=uuid4(),
                    order_id=order.id,
                    command_id=start.id,
                    version_id=version.id,
                    result={
                        "passed": True,
                        "requires_images": False,
                        "issues": [],
                        "usage": {"total_tokens": 123},
                        "model": "offline-review-fixture",
                    },
                    text_passed=True,
                    full_passed=True,
                    scope="text_and_images",
                    operation_key=f"{start.id}:review:{version.id}:{image_hash[:20]}",
                )
                session.add(review)
                command = Command(
                    id=uuid4(),
                    order_id=order.id,
                    kind="RELEASE",
                    expected_revision=1,
                    idempotency_key=str(uuid4()),
                    request_hash="0" * 64,
                    payload={"version_id": str(version.id)},
                    status="queued",
                )
                session.add(command)
                await session.flush()
                session.add(
                    HumanDecision(
                        order_id=order.id,
                        command_id=command.id,
                        action="release",
                        version_id=version.id,
                        status="requested",
                    )
                )
                order.phase, order.pending_command_id = "AWAITING_RELEASE", command.id
                result = SimpleNamespace(
                    order_id=order.id,
                    version_id=version.id,
                    command_id=command.id,
                    snapshot_id=snapshot.id,
                    review_id=review.id,
                    asset_ids=[image.id for image in images],
                )
            return result

        async def client(order_id=None, *, owner=False):
            customer = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=settings.public_app_url,
                )
            )
            async with app.state.database.sessions() as session:
                token, _ = await create_session(
                    session, settings, "owner" if owner else "seller", order_id
                )
                await session.commit()
            tokens.append(token)
            customer.cookies.set("owner_session" if owner else "seller_session", token)
            return customer

        yield settings, app, candidate, client
        async with app.state.database.sessions() as session:
            if order_ids:
                await session.execute(delete(Order).where(Order.id.in_(order_ids)))
            if tokens:
                await session.execute(
                    delete(AuthSession).where(
                        AuthSession.token_hash.in_([token_hash(token) for token in tokens])
                    )
                )
            await session.commit()


async def test_release_freezes_public_snapshot_and_replays_without_duplicate_delivery(
    delivery_context,
):
    settings, app, candidate, client = delivery_context
    item = await candidate()
    seller = await client(item.order_id)
    owner = await client(owner=True)
    wrong = await candidate()
    other = await client(wrong.order_id)
    path = f"/api/v1/orders/{item.order_id}"
    assert (await seller.get(path + "/deliverables")).json()["items"] == []
    query = path + f"/download?version_id={item.version_id}"
    assert (await seller.get(query)).status_code == 404
    assert (await owner.get(query)).status_code == 404
    assert (await other.get(query)).status_code == 404
    delivery = await release_version(
        app.state.database, settings, item.order_id, item.version_id, item.command_id
    )
    repeated = await release_version(
        app.state.database, settings, item.order_id, item.version_id, item.command_id
    )
    assert delivery.id == repeated.id and delivery.content_hash == repeated.content_hash
    assert len(list((settings.asset_dir / "packages").glob("*.zip"))) == 1
    listed = (await seller.get(path + "/deliverables")).json()["items"]
    assert len(listed) == 1 and listed[0]["version_id"] == str(item.version_id)
    assert "claims" not in json.dumps(listed) and "usage" not in json.dumps(listed)
    response = await seller.get(delivery.download_url)
    assert response.status_code == 200
    assert (await other.get(delivery.download_url)).status_code == 404
    wrong_download = await owner.get(
        f"/api/v1/orders/{wrong.order_id}/download?version_id={item.version_id}"
    )
    assert wrong_download.status_code == 404
    with ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert set(names) == {
            "listing.json",
            "listing.txt",
            "faq.md",
            "pricing-notes.md",
            "manifest.json",
            "images/image-01.png",
            "images/image-02.png",
        }
        texts = " ".join(
            archive.read(name).decode() for name in names if not name.startswith("images/")
        )
        assert "private" not in texts and "usage" not in texts and "claims" not in texts
        assert "9.99 - 12.99 USD" in archive.read("pricing-notes.md").decode()
    async with app.state.database.sessions() as session:
        order = await session.get(Order, item.order_id)
        version = await session.get(DeliverableVersion, item.version_id)
        assert order.phase == "AWAITING_SELLER" and order.pending_command_id is None
        assert version.status == "delivered"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.order_id == item.order_id, Message.role == "assistant")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OrderEvent)
                .where(OrderEvent.order_id == item.order_id, OrderEvent.kind == "version_delivered")
            )
            == 1
        )
        receipt = await session.scalar(
            select(DeliverySnapshot).where(DeliverySnapshot.id == delivery.id)
        )
        frozen = deepcopy(receipt.snapshot)
        version.draft = {
            **version.draft,
            "title": "Changed internal source must not alter delivery",
        }
        await session.commit()
    assert (await seller.get(path + "/deliverables")).json()["items"][0]["listing"]["title"] == (
        frozen["listing"]["title"]
    )
    assert (await seller.get(delivery.download_url)).content == response.content


@pytest.mark.parametrize(
    "invalid",
    [
        "full_pass",
        "latest_review",
        "no_images",
        "changed_image",
        "old_review_images",
        "stale_input",
        "wrong_phase",
    ],
)
async def test_release_rejects_incomplete_or_stale_bundles(delivery_context, invalid):
    settings, app, candidate, client = delivery_context
    item = await candidate()
    async with app.state.database.sessions() as session, session.begin():
        version = await session.get(DeliverableVersion, item.version_id)
        order = await session.get(Order, item.order_id)
        review = await session.get(ReviewReport, item.review_id)
        if invalid == "full_pass":
            version.full_passed = False
        elif invalid == "latest_review":
            review.scope = "text_review_only"
        elif invalid == "no_images":
            version.assets = []
        elif invalid == "changed_image":
            image = await session.get(Asset, item.asset_ids[0])
            LocalAssetStorage(settings.asset_dir).resolve(image.storage_key).write_bytes(
                small_image("red")
            )
        elif invalid == "old_review_images":
            review.operation_key = "an-old-image-set"
        elif invalid == "stale_input":
            source = await session.get(InputSnapshot, item.snapshot_id)
            session.add(
                InputSnapshot(
                    order_id=order.id,
                    revision=2,
                    facts=source.facts,
                    notes=None,
                    source="seller_supplement",
                )
            )
            order.revision = 2
        else:
            order.phase = "REVIEW"
    with pytest.raises(DeliveryError) as error:
        await release_version(
            app.state.database, settings, item.order_id, item.version_id, item.command_id
        )
    assert error.value.status_code == 409
    seller = await client(item.order_id)
    assert (await seller.get(f"/api/v1/orders/{item.order_id}/deliverables")).json()["items"] == []
    absent_download = await seller.get(
        f"/api/v1/orders/{item.order_id}/download?version_id={item.version_id}"
    )
    assert absent_download.status_code == 404


async def test_veto_records_reason_without_exposing_candidate_or_advancing_generation(
    delivery_context,
):
    settings, app, candidate, client = delivery_context
    item = await candidate()
    veto_id = uuid4()
    async with app.state.database.sessions() as session, session.begin():
        session.add(
            Command(
                id=veto_id,
                order_id=item.order_id,
                kind="VETO",
                expected_revision=1,
                idempotency_key=str(uuid4()),
                request_hash="0" * 64,
                payload={"version_id": str(item.version_id)},
                status="queued",
            )
        )
        await session.flush()
        session.add(
            HumanDecision(
                order_id=item.order_id,
                command_id=veto_id,
                action="veto",
                version_id=item.version_id,
                status="requested",
            )
        )
        order = await session.get(Order, item.order_id)
        order.pending_command_id = veto_id
    reason = "需要重新安排图片中的包装说明。"
    result = await veto_version(
        app.state.database, settings, item.order_id, item.version_id, veto_id, reason=reason
    )
    repeat = await veto_version(
        app.state.database,
        settings,
        item.order_id,
        item.version_id,
        veto_id,
        reason="different repeated input must not rewrite history",
    )
    assert result.reason == repeat.reason == reason
    async with app.state.database.sessions() as session:
        assert (await session.get(Order, item.order_id)).phase == "AWAITING_RELEASE"
        assert (await session.get(DeliverableVersion, item.version_id)).status == "vetoed"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OrderEvent)
                .where(OrderEvent.order_id == item.order_id, OrderEvent.kind == "version_vetoed")
            )
            == 1
        )
    with pytest.raises(DeliveryError):
        await release_version(
            app.state.database, settings, item.order_id, item.version_id, item.command_id
        )
    seller = await client(item.order_id)
    assert (await seller.get(f"/api/v1/orders/{item.order_id}/deliverables")).json()["items"] == []


async def test_changed_commit_guard_leaves_no_public_delivery(delivery_context):
    settings, app, candidate, client = delivery_context
    item = await candidate()
    calls = 0

    async def expired_guard(session):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DeliveryError("Execution lease changed before release commit.")

    with pytest.raises(DeliveryError):
        await release_version(
            app.state.database,
            settings,
            item.order_id,
            item.version_id,
            item.command_id,
            commit_guard=expired_guard,
        )
    seller = await client(item.order_id)
    assert (await seller.get(f"/api/v1/orders/{item.order_id}/deliverables")).json()["items"] == []
    async with app.state.database.sessions() as session:
        assert (await session.get(Order, item.order_id)).phase == "AWAITING_RELEASE"
        assert (await session.get(Command, item.command_id)).status == "queued"
    # An unreferenced file may remain, but it is not exposed by either download route.
    assert len(list((settings.asset_dir / "packages").glob("*.zip"))) == 1
