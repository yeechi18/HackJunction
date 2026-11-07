"""Seller confirmation, feedback and explicit simulated publication with reconciliation."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from company.agents.publishing import prepare_publish_plan
from company.config import Settings
from company.persistence.assets import Asset
from company.persistence.database import Database
from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import Command, InputSnapshot, Job, Message, Order, OrderEvent, now
from company.persistence.production import DeliverableVersion
from company.persistence.publication import (
    FeedbackRequest,
    PublishAttempt,
    SellerConfirmation,
)
from company.providers.factory import build_provider
from company.providers.mimo import ProviderError
from company.providers.publishers.base import PublisherResult
from company.providers.publishers.simulated import SimulatedPublisher
from company.providers.storage import LocalAssetStorage
from company.providers.usage import (
    attach_call,
    observed_call,
    record_call,
    tracked_provider,
    unwrapped_provider,
)
from company.schemas.publication import (
    AttemptView,
    ConfirmationInput,
    ConfirmationQueued,
    ConfirmationView,
    FeedbackInput,
    FeedbackQueued,
    PublicationView,
    PublishPlan,
    PublishResult,
)
from company.services.production import CommitGuard


class PublicationError(ProviderError):
    """Operator-safe publication diagnostic."""


class PublicationPending(PublicationError):
    """The external result remains unknown; the next run must reconcile first."""


def frozen_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FrozenDelivery:
    delivery_id: UUID
    version_id: UUID
    input_snapshot_id: UUID
    input_revision: int
    content_hash: str
    public_snapshot: dict
    assets: list[dict]


async def locked_order(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise PublicationError("Order not found.")
    return order


async def current_delivery(session: AsyncSession, order: Order, version_id: UUID) -> FrozenDelivery:
    delivery = await session.scalar(
        select(DeliverySnapshot)
        .where(
            DeliverySnapshot.order_id == order.id,
        )
        .order_by(DeliverySnapshot.version_number.desc(), DeliverySnapshot.delivered_at.desc())
        .limit(1)
    )
    snapshot = await session.scalar(
        select(InputSnapshot).where(
            InputSnapshot.order_id == order.id,
            InputSnapshot.revision == order.revision,
        )
    )
    version = await session.get(DeliverableVersion, version_id)
    if (
        delivery is None
        or delivery.version_id != version_id
        or snapshot is None
        or delivery.input_snapshot_id != snapshot.id
        or delivery.input_revision != order.revision
        or version is None
        or version.order_id != order.id
        or version.input_snapshot_id != snapshot.id
        or version.status != "delivered"
        or not version.full_passed
    ):
        raise PublicationError(
            "Only the latest delivered version for current inputs may be confirmed."
        )
    expected_hash = frozen_hash(
        {"version_id": str(version_id), "input_snapshot_id": str(snapshot.id), **delivery.snapshot}
    )
    if expected_hash != delivery.content_hash:
        raise PublicationError("The delivery no longer matches its immutable content hash.")
    public = json.loads(json.dumps(delivery.snapshot, ensure_ascii=False))
    listing = {key: version.draft[key] for key in ("title", "bullets", "description")}
    if public.get("listing") != listing or public.get("faqs") != version.draft.get("faqs", []):
        raise PublicationError("The current version differs from its delivered content.")
    images = public.get("images", [])
    if not images:
        raise PublicationError("Delivered image hashes are required before publication.")
    assets = []
    for image in images:
        asset = await session.get(Asset, UUID(image["id"]))
        if (
            asset is None
            or asset.order_id != order.id
            or asset.version_id != version_id
            or asset.delivered_at is None
            or asset.sha256 != image["sha256"]
            or asset.is_demo
            or asset.kind == "reference"
        ):
            raise PublicationError(
                "A delivered asset no longer matches the confirmed image manifest."
            )
        assets.append(
            {"id": str(asset.id), "storage_key": asset.storage_key, "sha256": asset.sha256}
        )
    return FrozenDelivery(
        delivery.id, version_id, snapshot.id, order.revision, delivery.content_hash, public, assets
    )


def _verify_assets(settings: Settings, assets: list[dict]) -> None:
    storage = LocalAssetStorage(settings.asset_dir)
    for asset in assets:
        try:
            path = storage.resolve(asset["storage_key"])
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            raise PublicationError("A confirmed image is unavailable.") from None
        if actual != asset["sha256"]:
            raise PublicationError("A confirmed image changed; publication was blocked.")


def confirmation_snapshot(
    order: Order, delivery: FrozenDelivery, price: str, currency: str, sku: str
) -> dict:
    return {
        **delivery.public_snapshot,
        "order_id": str(order.id),
        "version_id": str(delivery.version_id),
        "delivery_id": str(delivery.delivery_id),
        "delivery_hash": delivery.content_hash,
        "input_snapshot_id": str(delivery.input_snapshot_id),
        "input_revision": delivery.input_revision,
        "listing_price": price,
        "currency": currency,
        "seller_sku": sku,
        "mode": order.publish_mode,
    }


async def previous_command(
    session: AsyncSession, order_id: UUID, key: str, kind: str, body
) -> Command | None:
    if key.startswith("system:"):
        raise HTTPException(422, "Idempotency keys starting with system: are reserved.")
    command = await session.scalar(
        select(Command).where(
            Command.order_id == order_id,
            Command.idempotency_key == key,
        )
    )
    if command is not None and (command.kind != kind or command.request_hash != frozen_hash(body)):
        raise HTTPException(409, "Idempotency key was already used for different input.")
    return command


async def queue_confirmation(
    session: AsyncSession,
    settings: Settings,
    order_id: UUID,
    body: ConfirmationInput,
    key: str,
) -> ConfirmationQueued:
    order = await locked_order(session, order_id)
    prior = await previous_command(
        session, order_id, key, "CONFIRM_PUBLISH", body.model_dump(mode="json")
    )
    if prior is not None:
        confirmation = await session.scalar(
            select(SellerConfirmation).where(SellerConfirmation.command_id == prior.id)
        )
        return ConfirmationQueued(
            command_id=prior.id,
            order_id=order_id,
            revision=prior.expected_revision,
            confirmation_id=confirmation.id,
        )
    if (
        order.phase != "AWAITING_SELLER"
        or order.pending_command_id is not None
        or body.expected_revision != order.revision
    ):
        raise PublicationError("The current delivered version is not awaiting seller confirmation.")
    if order.publish_mode != "simulated" or settings.publish_mode != "simulated":
        raise PublicationError(
            "Amazon production and sandbox are not connected; no simulated fallback is performed."
        )
    prepared = await current_delivery(session, order, body.version_id)
    await session.commit()  # Release ORM locks before reading full image files.
    await asyncio.to_thread(_verify_assets, settings, prepared.assets)
    order = await locked_order(session, order_id)
    prior = await previous_command(
        session, order_id, key, "CONFIRM_PUBLISH", body.model_dump(mode="json")
    )
    if prior is not None:
        confirmation = await session.scalar(
            select(SellerConfirmation).where(SellerConfirmation.command_id == prior.id)
        )
        return ConfirmationQueued(
            command_id=prior.id,
            order_id=order_id,
            revision=prior.expected_revision,
            confirmation_id=confirmation.id,
        )
    current = await current_delivery(session, order, body.version_id)
    if (
        order.phase != "AWAITING_SELLER"
        or order.pending_command_id is not None
        or order.revision != body.expected_revision
        or current != prepared
    ):
        raise PublicationError("Delivery or order inputs changed before confirmation was saved.")
    await session.execute(
        update(SellerConfirmation)
        .where(
            SellerConfirmation.order_id == order_id,
            SellerConfirmation.invalidated_at.is_(None),
        )
        .values(invalidated_at=now())
    )
    command_id, confirmation_id = uuid4(), uuid4()
    snapshot = confirmation_snapshot(
        order, current, body.listing_price, body.currency, body.seller_sku
    )
    command = Command(
        id=command_id,
        order_id=order_id,
        kind="CONFIRM_PUBLISH",
        expected_revision=order.revision,
        idempotency_key=key,
        request_hash=frozen_hash(body.model_dump(mode="json")),
        payload={"confirmation_id": str(confirmation_id)},
    )
    session.add(command)
    await session.flush()
    session.add(
        SellerConfirmation(
            id=confirmation_id,
            order_id=order_id,
            command_id=command_id,
            delivery_id=current.delivery_id,
            version_id=body.version_id,
            input_snapshot_id=current.input_snapshot_id,
            input_revision=order.revision,
            mode=order.publish_mode,
            snapshot=snapshot,
            snapshot_hash=frozen_hash(snapshot),
            listing_price=Decimal(body.listing_price),
            currency=body.currency,
            seller_sku=body.seller_sku,
        )
    )
    session.add(Job(order_id=order_id, command_id=command_id, kind="RESUME"))
    order.pending_command_id = command_id
    session.add(
        OrderEvent(
            order_id=order_id,
            kind="seller_confirmation_queued",
            message="成品版本与售价确认已入队，尚未发布；当前使用明确标记的模拟发布模式。",
        )
    )
    await session.commit()
    return ConfirmationQueued(
        command_id=command_id,
        order_id=order_id,
        revision=order.revision,
        confirmation_id=confirmation_id,
    )


async def feedback_base(session: AsyncSession, order: Order, version_id: UUID) -> FrozenDelivery:
    """A blocked confirmed-fact correction can be completed against the same frozen base.

    Confirmation still uses current_delivery directly and rejects this historical base.
    """
    try:
        return await current_delivery(session, order, version_id)
    except PublicationError:
        latest = await session.scalar(
            select(DeliverySnapshot)
            .where(
                DeliverySnapshot.order_id == order.id,
            )
            .order_by(DeliverySnapshot.version_number.desc())
            .limit(1)
        )
        request = await session.scalar(
            select(FeedbackRequest)
            .join(Command)
            .where(
                FeedbackRequest.order_id == order.id,
                FeedbackRequest.version_id == version_id,
                FeedbackRequest.resulting_revision == order.revision,
                FeedbackRequest.status == "waiting_for_seller",
                Command.status == "applied",
            )
            .order_by(FeedbackRequest.created_at.desc())
            .limit(1)
        )
        current = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == order.id,
                InputSnapshot.revision == order.revision,
            )
        )
        if (
            latest is None
            or latest.version_id != version_id
            or request is None
            or request.delivery_id != latest.id
            or current is None
            or current.source != "seller_feedback"
            or order.revision <= latest.input_revision
        ):
            raise PublicationError(
                "Feedback must target the latest delivered base for current input."
            ) from None
        historical = SimpleNamespace(
            id=order.id, revision=latest.input_revision
        )
        return await current_delivery(session, historical, version_id)


async def queue_feedback(
    session: AsyncSession,
    order_id: UUID,
    body: FeedbackInput,
    key: str,
) -> FeedbackQueued:
    order = await locked_order(session, order_id)
    prior = await previous_command(session, order_id, key, "FEEDBACK", body.model_dump(mode="json"))
    if prior is not None:
        feedback = await session.scalar(
            select(FeedbackRequest).where(FeedbackRequest.command_id == prior.id)
        )
        return FeedbackQueued(
            command_id=prior.id,
            order_id=order_id,
            revision=prior.expected_revision,
            feedback_id=feedback.id,
        )
    if (
        order.phase != "AWAITING_SELLER"
        or order.pending_command_id is not None
        or order.revision != body.expected_revision
    ):
        raise PublicationError(
            "Feedback requires the current delivered version with no pending operation."
        )
    delivery = await feedback_base(session, order, body.version_id)
    old_revision = order.revision
    fact_updates = (
        [fact.model_dump() for fact in body.fact_updates] if body.fact_updates is not None else None
    )
    if fact_updates is not None:
        from company.services.orders import conflicts

        if conflicts(fact_updates):
            raise HTTPException(
                422, "Select one confirmed value for each conflicting product fact."
            )
        previous = await session.get(InputSnapshot, delivery.input_snapshot_id)
        order.revision += 1
        session.add(
            InputSnapshot(
                order_id=order_id,
                revision=order.revision,
                facts=fact_updates,
                notes=previous.notes,
                source="seller_feedback",
            )
        )
    await session.execute(
        update(SellerConfirmation)
        .where(
            SellerConfirmation.order_id == order_id,
            SellerConfirmation.invalidated_at.is_(None),
        )
        .values(invalidated_at=now())
    )
    command_id, feedback_id = uuid4(), uuid4()
    session.add(
        Command(
            id=command_id,
            order_id=order_id,
            kind="FEEDBACK",
            expected_revision=order.revision,
            idempotency_key=key,
            request_hash=frozen_hash(body.model_dump(mode="json")),
            payload={"feedback_id": str(feedback_id), "version_id": str(body.version_id)},
        )
    )
    await session.flush()
    session.add(
        FeedbackRequest(
            id=feedback_id,
            order_id=order_id,
            command_id=command_id,
            version_id=body.version_id,
            delivery_id=delivery.delivery_id,
            input_revision=old_revision,
            resulting_revision=order.revision,
            content=body.content,
            fact_updates=fact_updates,
        )
    )
    session.add(Job(order_id=order_id, command_id=command_id, kind="RESUME"))
    session.add(
        Message(
            order_id=order_id, role="seller", content=body.content, input_revision=order.revision
        )
    )
    order.pending_command_id, order.updated_at = command_id, now()
    session.add(
        OrderEvent(
            order_id=order_id,
            kind="seller_feedback_queued",
            message="修改意见已保存，等待获客角色转交；既有成品保持历史版本。",
        )
    )
    await session.commit()
    return FeedbackQueued(
        command_id=command_id, order_id=order_id, revision=order.revision, feedback_id=feedback_id
    )


@dataclass(frozen=True)
class AttemptState:
    attempt_id: UUID
    confirmation_id: UUID
    operation_key: str
    mode: str
    status: str
    snapshot: dict
    snapshot_hash: str
    assets: list[dict]
    plan: dict | None
    listing_url: str | None


def attempt_result(attempt: PublishAttempt, *, cache_hit=False) -> PublishResult:
    return PublishResult(
        confirmation_id=attempt.confirmation_id,
        attempt_id=attempt.id,
        mode=attempt.mode,
        status=attempt.status,
        listing_url=attempt.listing_url,
        cache_hit=cache_hit,
    )


async def load_attempt(
    session: AsyncSession,
    order_id: UUID,
    command_id: UUID,
    *,
    commit_guard=None,
) -> AttemptState | PublishResult:
    if commit_guard is not None:
        await commit_guard(session)
    order = await locked_order(session, order_id)
    command = await session.get(Command, command_id)
    confirmation = await session.scalar(
        select(SellerConfirmation).where(
            SellerConfirmation.order_id == order_id,
            SellerConfirmation.command_id == command_id,
        )
    )
    if (
        command is None
        or command.order_id != order_id
        or command.kind != "CONFIRM_PUBLISH"
        or confirmation is None
    ):
        raise PublicationError("Publication command does not match a seller confirmation.")
    attempt = await session.scalar(
        select(PublishAttempt)
        .where(
            PublishAttempt.confirmation_id == confirmation.id,
        )
        .with_for_update()
    )
    if attempt is not None and attempt.status == "SIMULATED":
        return attempt_result(attempt, cache_hit=True)
    if (
        confirmation.invalidated_at is not None
        or command.status in {"failed", "stale", "cancelled"}
        or order.revision != confirmation.input_revision
        or command.expected_revision != order.revision
        or order.phase not in {"AWAITING_SELLER", "READY_TO_PUBLISH", "PUBLISHING"}
    ):
        raise PublicationError("Seller confirmation is no longer current and cannot be published.")
    current = await current_delivery(session, order, confirmation.version_id)
    snapshot = confirmation_snapshot(
        order,
        current,
        f"{confirmation.listing_price:.2f}",
        confirmation.currency,
        confirmation.seller_sku,
    )
    if (
        current.delivery_id != confirmation.delivery_id
        or snapshot != confirmation.snapshot
        or frozen_hash(snapshot) != confirmation.snapshot_hash
    ):
        raise PublicationError(
            "The publish snapshot differs from the version and price the seller confirmed."
        )
    if attempt is None:
        attempt = PublishAttempt(
            order_id=order_id,
            command_id=command_id,
            confirmation_id=confirmation.id,
            operation_key=f"confirmation:{confirmation.id}",
            mode=confirmation.mode,
            snapshot=json.loads(json.dumps(snapshot)),
            snapshot_hash=confirmation.snapshot_hash,
        )
        session.add(attempt)
        await session.flush()
    if attempt.snapshot != snapshot or attempt.snapshot_hash != confirmation.snapshot_hash:
        raise PublicationError("Publication attempt is bound to a different immutable snapshot.")
    return AttemptState(
        attempt.id,
        confirmation.id,
        attempt.operation_key,
        attempt.mode,
        attempt.status,
        snapshot,
        attempt.snapshot_hash,
        current.assets,
        attempt.plan,
        attempt.listing_url,
    )


async def mark_status(
    database: Database,
    attempt_id: UUID,
    status: str,
    message: str | None = None,
    *,
    commit_guard=None,
    external: PublisherResult | None = None,
    call_usage=None,
) -> None:
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        attempt = await session.get(PublishAttempt, attempt_id, with_for_update=True)
        if attempt is None or attempt.status == "SIMULATED":
            return
        attempt.status, attempt.error_summary, attempt.updated_at = status, message, now()
        record_call(attempt, "plan_usage", call_usage)
        if external is not None:
            attempt.external_result = external.as_dict()


def expected_plan(state: AttemptState) -> dict:
    return {
        "version_id": state.snapshot["version_id"],
        "confirmation_hash": state.snapshot_hash,
        **{
            key: state.snapshot[key]
            for key in ("delivery_hash", "listing_price", "currency", "seller_sku", "marketplace")
        },
    }


async def save_plan(
    database: Database,
    order_id: UUID,
    command_id: UUID,
    planned,
    *,
    commit_guard=None,
    call_usage=None,
) -> AttemptState:
    async with database.sessions() as session, session.begin():
        state = await load_attempt(session, order_id, command_id, commit_guard=commit_guard)
        if isinstance(state, PublishResult):
            raise PublicationError(
                "Publication completed while planning; retry to read its receipt."
            )
        attempt = await session.get(PublishAttempt, state.attempt_id)
        payload = planned.model_dump(mode="json")
        record_call(attempt, "plan_usage", call_usage or observed_call(planned))
        attempt.cost_usd = None
        plan = {key: payload[key] for key in PublishPlan.model_fields}
        if plan != expected_plan(state):
            attempt.status, attempt.error_summary = (
                "FAILED",
                "Model plan changed a confirmed field; no submission occurred.",
            )
            attempt.updated_at = now()
            # Persist the observed usage, but never save a mutated plan as executable.
            await session.flush()
            invalid = True
        else:
            attempt.plan, attempt.status, attempt.error_summary = plan, "PREPARED", None
            attempt.updated_at = now()
            invalid = False
    if invalid:
        raise PublicationError(
            "Model plan changed a seller-confirmed field; publication was blocked."
        )
    async with database.sessions() as session, session.begin():
        return await load_attempt(session, order_id, command_id, commit_guard=commit_guard)


async def finish_publication(
    database: Database,
    order_id: UUID,
    command_id: UUID,
    result: PublisherResult,
    *,
    commit_guard=None,
) -> PublishResult:
    if result.status != "SIMULATED" or result.mode != "simulated":
        raise PublicationPending("Publication result is not a verified local simulation receipt.")
    async with database.sessions() as session, session.begin():
        state = await load_attempt(session, order_id, command_id, commit_guard=commit_guard)
        if isinstance(state, PublishResult):
            return state
        attempt = await session.get(PublishAttempt, state.attempt_id)
        order = await session.get(Order, order_id)
        command = await session.get(Command, command_id)
        attempt.status, attempt.external_result = "SIMULATED", result.as_dict()
        attempt.listing_url, attempt.error_summary, attempt.updated_at = (
            result.listing_url,
            None,
            now(),
        )
        order.phase, order.updated_at, order.pending_command_id = "COMPLETED", now(), None
        command.status = "applied"
        session.add(
            OrderEvent(
                order_id=order_id,
                kind="simulated_publication_completed",
                message="模拟订单完成：已生成本地演示刊登记录，未发布到 Amazon，未发生真实成交。",
                payload={
                    "mode": "simulated",
                    "attempt_id": str(attempt.id),
                    "listing_url": result.listing_url,
                },
            )
        )
        session.add(
            Message(
                order_id=order_id,
                role="assistant",
                input_revision=order.revision,
                content=(
                    "模拟发布已完成，可查看演示商品页。"
                    if result.listing_url
                    else "模拟发布已完成，私有刊登记录保存在本订单中。"
                )
                + "这不是 Amazon 真实上架，也不代表真实成交。",
            )
        )
        await session.flush()
        return attempt_result(attempt)


async def publish_confirmed(
    settings: Settings,
    order_id: UUID,
    command_id: UUID,
    *,
    database: Database | None = None,
    provider=None,
    publisher=None,
    commit_guard: CommitGuard | None = None,
) -> PublishResult:
    """Submit only the frozen seller approval. UNKNOWN always reconciles before any retry."""
    owns_database, owns_provider = database is None, False
    db = database or Database(settings)
    try:
        async with db.sessions() as session, session.begin():
            state = await load_attempt(session, order_id, command_id, commit_guard=commit_guard)
        if isinstance(state, PublishResult):
            return state
        if state.mode != "simulated" or settings.publish_mode != "simulated":
            await mark_status(
                db,
                state.attempt_id,
                "BLOCKED",
                "Requested Amazon mode is not connected; no fallback occurred.",
                commit_guard=commit_guard,
            )
            raise PublicationError(
                "Requested Amazon mode is not implemented; publication was blocked."
            )
        gateway = publisher or SimulatedPublisher(settings, db)
        if gateway.mode != "simulated":
            raise PublicationError(
                "Publisher mode does not match the explicitly confirmed simulation."
            )
        await asyncio.to_thread(_verify_assets, settings, state.assets)

        # A previous process may have died after the durable SUBMITTING marker.
        if state.status in {"SUBMITTING", "UNKNOWN"}:
            try:
                reconciled = await gateway.get_status(operation_key=state.operation_key)
            except Exception:
                reconciled = PublisherResult(status="UNKNOWN")
            if reconciled.status == "SIMULATED" and reconciled.mode == "simulated":
                return await finish_publication(
                    db, order_id, command_id, reconciled, commit_guard=commit_guard
                )
            if reconciled.status != "NOT_FOUND":
                await mark_status(
                    db,
                    state.attempt_id,
                    "UNKNOWN",
                    "Reconciliation has not established the result.",
                    commit_guard=commit_guard,
                    external=reconciled,
                )
                raise PublicationPending(
                    "Publication result remains unknown; reconciliation is required."
                )

        if state.plan is None:
            if provider is None:
                provider, owns_provider = build_provider(settings), True
            provider = tracked_provider(provider)
            provider.last_call = None
            traced_provider = unwrapped_provider(provider)
            if hasattr(traced_provider, "trace_context"):
                traced_provider.trace_context = {
                    "order_id": str(order_id),
                    "command_id": str(command_id),
                    "role": "publishing",
                    "step_key": "publish_plan",
                }
            try:
                planned = await prepare_publish_plan(
                    {**state.snapshot, "confirmation_hash": state.snapshot_hash}, provider
                )
            except Exception as error:
                call_usage = attach_call(error, provider.last_call)
                message = (
                    str(error)
                    if isinstance(error, ProviderError)
                    else "Publish planning failed before submission."
                )
                await mark_status(
                    db,
                    state.attempt_id,
                    "FAILED",
                    message,
                    commit_guard=commit_guard,
                    call_usage=call_usage,
                )
                raise PublicationError(message, call_usage=call_usage) from None
            state = await save_plan(
                db,
                order_id,
                command_id,
                planned,
                commit_guard=commit_guard,
                call_usage=provider.last_call,
            )
        if state.plan != expected_plan(state):
            raise PublicationError("Stored plan is not identical to the seller confirmation.")
        validation = await gateway.validate(state.snapshot)
        if not validation.valid:
            await mark_status(
                db,
                state.attempt_id,
                "FAILED",
                "Publisher validation rejected the frozen snapshot.",
                commit_guard=commit_guard,
            )
            raise PublicationError("Publisher validation failed; no submission occurred.")
        await asyncio.to_thread(_verify_assets, settings, state.assets)
        async with db.sessions() as session, session.begin():
            current = await load_attempt(session, order_id, command_id, commit_guard=commit_guard)
            if isinstance(current, PublishResult):
                return current
            if current.snapshot_hash != state.snapshot_hash:
                raise PublicationError("Seller confirmation changed before publication.")
            attempt = await session.get(PublishAttempt, state.attempt_id)
            attempt.status, attempt.updated_at, attempt.error_summary = "SUBMITTING", now(), None
            attempt.submit_count += 1
            (await session.get(Order, order_id)).phase = "PUBLISHING"
        try:
            submitted = await gateway.submit(state.snapshot, operation_key=state.operation_key)
        except Exception:
            await mark_status(
                db,
                state.attempt_id,
                "UNKNOWN",
                "Submission result is unknown; reconcile before retrying.",
                commit_guard=commit_guard,
            )
            raise PublicationPending(
                "Submission result is unknown; reconciliation is required."
            ) from None
        if submitted.status != "SIMULATED" or submitted.mode != "simulated":
            await mark_status(
                db,
                state.attempt_id,
                "UNKNOWN",
                "Submission is not yet a verified simulation receipt.",
                commit_guard=commit_guard,
                external=submitted,
            )
            raise PublicationPending("Publication result requires reconciliation.")
        await asyncio.to_thread(_verify_assets, settings, state.assets)
        return await finish_publication(
            db, order_id, command_id, submitted, commit_guard=commit_guard
        )
    finally:
        if owns_provider:
            await provider.close()
        if owns_database:
            await db.close()


async def publication_view(session: AsyncSession, order_id: UUID) -> PublicationView:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "Order not found.")
    confirmation = await session.scalar(
        select(SellerConfirmation)
        .where(
            SellerConfirmation.order_id == order_id,
        )
        .order_by(SellerConfirmation.created_at.desc())
        .limit(1)
    )
    attempt = (
        await session.scalar(
            select(PublishAttempt).where(
                PublishAttempt.confirmation_id == confirmation.id,
            )
        )
        if confirmation
        else None
    )
    valid = bool(
        confirmation
        and confirmation.invalidated_at is None
        and confirmation.input_revision == order.revision
    )
    return PublicationView(
        mode=attempt.mode if attempt else order.publish_mode,
        status=(attempt.status if attempt else "CONFIRMED_QUEUED")
        if valid
        else "INVALIDATED"
        if confirmation
        else "NOT_CONFIRMED",
        listing_url=attempt.listing_url if attempt and valid else None,
        notice="本轮仅支持明确标记的模拟发布；不代表 Amazon 真实上架、付款或成交。",
        confirmation=ConfirmationView(
            id=confirmation.id,
            version_id=confirmation.version_id,
            listing_price=f"{confirmation.listing_price:.2f}",
            currency=confirmation.currency,
            seller_sku=confirmation.seller_sku,
            snapshot_hash=confirmation.snapshot_hash,
            valid=valid,
            confirmed_at=confirmation.created_at,
        )
        if confirmation
        else None,
        attempt=AttemptView(
            id=attempt.id,
            status=attempt.status,
            submit_count=attempt.submit_count,
            updated_at=attempt.updated_at,
        )
        if attempt
        else None,
    )
