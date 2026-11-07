"""Publication uses real PostgreSQL, real frozen M5 packages and offline planning stubs."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_delivery import delivery_context as delivery_context
from test_delivery import small_image

from company.api.publication import router
from company.persistence.assets import Asset
from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import Command, InputSnapshot, Order
from company.persistence.production import DeliverableVersion
from company.persistence.publication import (
    FeedbackRequest,
    PublishAttempt,
    SellerConfirmation,
    SimulatedListing,
)
from company.providers.publishers.simulated import SimulatedPublisher
from company.providers.storage import LocalAssetStorage
from company.services.delivery import release_version
from company.services.publication import (
    PublicationError,
    PublicationPending,
    frozen_hash,
    publish_confirmed,
)

pytestmark = pytest.mark.integration


class PlanProvider:
    def __init__(self, *, changed_price=False):
        self.calls = 0
        self.changed_price = changed_price

    async def generate_json(self, schema, messages, **kwargs):
        self.calls += 1
        snapshot = json.loads(messages[-1]["content"])
        plan = {key: snapshot[key] for key in schema.model_fields}
        if self.changed_price:
            plan["listing_price"] = "0.01"
        return schema.model_validate(plan), SimpleNamespace(
            usage={"input_tokens": 15, "output_tokens": 7, "total_tokens": 22},
            model="offline-publish-planner",
            elapsed_seconds=0.1,
        )


async def prepare(delivery_context):
    settings, app, candidate, clients = delivery_context
    if "/api/v1/orders/{order_id}/confirmations" not in app.openapi()["paths"]:
        app.include_router(router, prefix="/api/v1")
        app.openapi_schema = None
    item = await candidate()
    await release_version(
        app.state.database, settings, item.order_id, item.version_id, item.command_id
    )
    seller = await clients(item.order_id)
    csrf = (await seller.get("/api/v1/order-access/session")).json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": f"confirm-{uuid4()}"}
    body = {
        "expected_revision": 1,
        "version_id": str(item.version_id),
        "listing_price": "12.99",
        "currency": "USD",
        "seller_sku": "DEMO-CLIPS-6",
    }
    return settings, app, item, seller, headers, body


async def confirm(prepared):
    _, _, item, seller, headers, body = prepared
    response = await seller.post(
        f"/api/v1/orders/{item.order_id}/confirmations", json=body, headers=headers
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_confirm_queue_idempotence_and_simulated_completion_are_clear(delivery_context):
    prepared = await prepare(delivery_context)
    settings, app, item, seller, headers, body = prepared
    queued = await confirm(prepared)
    path = f"/api/v1/orders/{item.order_id}"
    replay = await seller.post(path + "/confirmations", json=body, headers=headers)
    assert replay.status_code == 202 and replay.json()["command_id"] == queued["command_id"]
    assert (
        await seller.post(
            path + "/confirmations", json={**body, "listing_price": "13.99"}, headers=headers
        )
    ).status_code == 409
    async with app.state.database.sessions() as session:
        assert (await session.get(Order, item.order_id)).phase == "AWAITING_SELLER"
        confirmation = await session.get(SellerConfirmation, queued["confirmation_id"])
        assert confirmation.snapshot_hash == frozen_hash(confirmation.snapshot)
        assert (
            "contact" not in confirmation.snapshot
            and confirmation.snapshot["listing_price"] == "12.99"
        )
    provider = PlanProvider()
    result = await publish_confirmed(
        settings,
        item.order_id,
        queued["command_id"],
        database=app.state.database,
        provider=provider,
    )
    assert result.mode == "simulated" and result.status == "SIMULATED"
    assert "/demo-listings/" in result.listing_url
    again = await publish_confirmed(
        settings,
        item.order_id,
        queued["command_id"],
        database=app.state.database,
        provider=provider,
    )
    assert again.cache_hit and again.attempt_id == result.attempt_id and provider.calls == 1
    state = (await seller.get(path + "/publication")).json()
    assert state["mode"] == "simulated" and state["status"] == "SIMULATED"
    assert "模拟" in state["notice"] and "真实" in state["notice"]
    async with app.state.database.sessions() as session:
        assert (await session.get(Order, item.order_id)).phase == "COMPLETED"
        attempt = await session.get(PublishAttempt, result.attempt_id)
        assert attempt.submit_count == 1 and attempt.plan_usage["total_tokens"] == 22
        assert attempt.cost_usd is None and attempt.model == "offline-publish-planner"
        assert (await session.get(Command, queued["command_id"])).status == "applied"


async def test_unknown_after_submit_reconciles_without_second_model_or_submit(delivery_context):
    prepared = await prepare(delivery_context)
    settings, app, item, _, _, _ = prepared
    queued = await confirm(prepared)

    class TimedOutAfterCommit(SimulatedPublisher):
        def __init__(self, settings, database):
            super().__init__(settings, database)
            self.submits = self.lookups = 0

        async def submit(self, snapshot, *, operation_key):
            self.submits += 1
            await super().submit(snapshot, operation_key=operation_key)
            raise TimeoutError("Injected after durable external effect")

        async def get_status(self, *, operation_key):
            self.lookups += 1
            return await super().get_status(operation_key=operation_key)

    gateway = TimedOutAfterCommit(settings, app.state.database)
    provider = PlanProvider()
    with pytest.raises(PublicationPending):
        await publish_confirmed(
            settings,
            item.order_id,
            queued["command_id"],
            database=app.state.database,
            provider=provider,
            publisher=gateway,
        )
    async with app.state.database.sessions() as session:
        attempt = await session.scalar(
            select(PublishAttempt).where(PublishAttempt.order_id == item.order_id)
        )
        simulation = await session.scalar(
            select(SimulatedListing).where(SimulatedListing.order_id == item.order_id)
        )
        assert attempt.status == "UNKNOWN"
        public_id = simulation.public_id
    assert (await prepared[3].get(f"/api/v1/demo-listings/{public_id}")).status_code == 404
    result = await publish_confirmed(
        settings,
        item.order_id,
        queued["command_id"],
        database=app.state.database,
        provider=provider,
        publisher=gateway,
    )
    assert (
        result.status == "SIMULATED" and gateway.lookups == gateway.submits == provider.calls == 1
    )


async def test_model_cannot_change_price_and_unknown_cost_is_not_invented(delivery_context):
    prepared = await prepare(delivery_context)
    settings, app, item, _, _, _ = prepared
    queued = await confirm(prepared)
    with pytest.raises(PublicationError, match="confirmed field"):
        await publish_confirmed(
            settings,
            item.order_id,
            queued["command_id"],
            database=app.state.database,
            provider=PlanProvider(changed_price=True),
        )
    async with app.state.database.sessions() as session:
        attempt = await session.scalar(
            select(PublishAttempt).where(PublishAttempt.order_id == item.order_id)
        )
        assert attempt.plan is None and attempt.status == "FAILED" and attempt.submit_count == 0
        assert attempt.plan_usage["total_tokens"] == 22 and attempt.cost_usd is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SimulatedListing)
                .where(SimulatedListing.order_id == item.order_id)
            )
            == 0
        )


@pytest.mark.parametrize("invalid", ["revision", "old_version", "asset_hash"])
async def test_stale_or_changed_confirmation_is_rejected(delivery_context, invalid):
    prepared = await prepare(delivery_context)
    _, app, item, seller, headers, body = prepared
    if invalid == "revision":
        body = {**body, "expected_revision": 99}
    elif invalid == "old_version":
        async with app.state.database.sessions() as session, session.begin():
            source = await session.get(DeliverableVersion, item.version_id)
            old = await session.scalar(
                select(DeliverySnapshot).where(DeliverySnapshot.order_id == item.order_id)
            )
            new_id = uuid4()
            session.add(
                DeliverableVersion(
                    id=new_id,
                    order_id=item.order_id,
                    command_id=item.command_id,
                    version_number=2,
                    round_index=1,
                    parent_version_id=source.id,
                    input_snapshot_id=source.input_snapshot_id,
                    research_package_id=source.research_package_id,
                    draft=source.draft,
                    assets=source.assets,
                    status="delivered",
                    text_passed=True,
                    full_passed=True,
                    operation_key=str(uuid4()),
                )
            )
            await session.flush()
            session.add(
                DeliverySnapshot(
                    order_id=item.order_id,
                    version_id=new_id,
                    release_command_id=item.command_id,
                    input_snapshot_id=old.input_snapshot_id,
                    input_revision=1,
                    version_number=2,
                    snapshot=old.snapshot,
                    content_hash="x" * 64,
                    package_key=f"{uuid4().hex}.zip",
                    package_sha256="x" * 64,
                    package_byte_size=1,
                    is_demo=True,
                )
            )
    else:
        async with app.state.database.sessions() as session:
            asset = await session.get(Asset, item.asset_ids[0])
            LocalAssetStorage(prepared[0].asset_dir).resolve(asset.storage_key).write_bytes(
                small_image("red")
            )
    response = await seller.post(
        f"/api/v1/orders/{item.order_id}/confirmations", json=body, headers=headers
    )
    assert response.status_code == 409
    async with app.state.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SellerConfirmation)
                .where(SellerConfirmation.order_id == item.order_id)
            )
            == 0
        )


async def test_feedback_updates_full_facts_invalidates_old_approval_and_preserves_history(
    delivery_context,
):
    prepared = await prepare(delivery_context)
    settings, app, item, seller, headers, _ = prepared
    queued = await confirm(prepared)
    feedback_path = f"/api/v1/orders/{item.order_id}/feedback"
    async with app.state.database.sessions() as session:
        source = await session.get(InputSnapshot, item.snapshot_id)
        updated = deepcopy(source.facts)
    next(fact for fact in updated if fact["key"] == "pack_quantity")["value"] = "4"
    body = {
        "expected_revision": 1,
        "version_id": str(item.version_id),
        "content": "请改为4个装，保留尺寸。",
        "fact_updates": updated,
    }
    feedback_headers = {**headers, "Idempotency-Key": "seller-feedback-change-count"}
    assert (
        await seller.post(feedback_path, json=body, headers=feedback_headers)
    ).status_code == 409
    # Explicit fixture: a prior confirmation failed before submission and is no longer pending.
    async with app.state.database.sessions() as session, session.begin():
        (await session.get(Order, item.order_id)).pending_command_id = None
        (await session.get(Command, queued["command_id"])).status = "failed"
    response = await seller.post(feedback_path, json=body, headers=feedback_headers)
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["revision"] == 2
    replay = await seller.post(feedback_path, json=body, headers=feedback_headers)
    assert replay.status_code == 202 and replay.json()["feedback_id"] == result["feedback_id"]
    async with app.state.database.sessions() as session:
        original = await session.get(InputSnapshot, item.snapshot_id)
        latest = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == item.order_id, InputSnapshot.revision == 2
            )
        )
        assert (
            next(fact["value"] for fact in original.facts if fact["key"] == "pack_quantity") == "6"
        )
        assert next(fact["value"] for fact in latest.facts if fact["key"] == "pack_quantity") == "4"
        assert (
            next(fact["value"] for fact in latest.facts if fact["key"] == "dimensions")
            == "25 x 25 x 15"
        )
        confirmation = await session.get(SellerConfirmation, queued["confirmation_id"])
        assert confirmation.invalidated_at is not None
        feedback = await session.get(FeedbackRequest, result["feedback_id"])
        assert (
            feedback.classification is None
            and feedback.candidate_count == 0
            and feedback.candidate_limit == 3
        )
    with pytest.raises(PublicationError):
        await publish_confirmed(
            settings,
            item.order_id,
            queued["command_id"],
            database=app.state.database,
            provider=PlanProvider(),
        )
    assert (await seller.get(f"/api/v1/orders/{item.order_id}/deliverables")).json()["items"][0][
        "version_id"
    ] == str(item.version_id)


async def test_scope_csrf_public_demo_and_safe_snapshot(delivery_context):
    prepared = await prepare(delivery_context)
    settings, app, item, seller, headers, body = prepared
    _, _, candidate, clients = delivery_context
    other = await candidate()
    wrong_seller = await clients(other.order_id)
    path = f"/api/v1/orders/{item.order_id}"
    assert (await wrong_seller.get(path + "/publication")).status_code == 404
    assert (
        await seller.post(
            path + "/confirmations", json=body, headers={"Idempotency-Key": "no-csrf"}
        )
    ).status_code == 403
    queued = await confirm(prepared)
    result = await publish_confirmed(
        settings,
        item.order_id,
        queued["command_id"],
        database=app.state.database,
        provider=PlanProvider(),
    )
    public_id = result.listing_url.rsplit("/", 1)[1]
    public = await wrong_seller.get(f"/api/v1/demo-listings/{public_id}")
    assert public.status_code == 200, public.text
    view = public.json()
    assert view["status"] == "SIMULATED" and view["mode"] == "simulated"
    serialized = json.dumps(view)
    assert "private research" not in serialized and "contact" not in serialized
    assert "seller_name" not in serialized and "usage" not in serialized
    assert (await wrong_seller.get(view["images"][0]["url"])).status_code == 200
    async with app.state.database.sessions() as session, session.begin():
        (await session.get(Order, item.order_id)).is_demo = False
    assert (await wrong_seller.get(f"/api/v1/demo-listings/{public_id}")).status_code == 404
    assert (await wrong_seller.get(view["images"][0]["url"])).status_code == 404


@pytest.mark.parametrize("mode", ["production", "sandbox"])
async def test_unimplemented_modes_never_silently_fall_back(delivery_context, mode):
    prepared = await prepare(delivery_context)
    _, app, item, seller, headers, body = prepared
    async with app.state.database.sessions() as session, session.begin():
        (await session.get(Order, item.order_id)).publish_mode = mode
    response = await seller.post(
        f"/api/v1/orders/{item.order_id}/confirmations", json=body, headers=headers
    )
    assert response.status_code == 409 and "fallback" in response.json()["detail"]
    async with app.state.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SimulatedListing)
                .where(SimulatedListing.order_id == item.order_id)
            )
            == 0
        )
