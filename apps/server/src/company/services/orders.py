"""Order input history, read models and atomic wake-up commands."""

import hashlib
import json
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.domain.brands import known_brand, missing_delivery_fields, required_keys
from company.domain.facts import canonical_key
from company.domain.states import allowed_actions
from company.persistence.agent_runs import AgentRun
from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import (
    Command,
    HumanDecision,
    InputSnapshot,
    Job,
    Message,
    Order,
    OrderAccess,
    OrderEvent,
    SellerAccount,
    now,
)
from company.persistence.production import DeliverableVersion, ProductionStepReceipt
from company.schemas.commands import DecisionInput, QueuedCommand
from company.schemas.orders import (
    CommandView,
    CreatedOrder,
    CreateOrder,
    DecisionView,
    InternalOrder,
    MessageInput,
    MessageView,
    OrderSummary,
    SnapshotView,
    SupplementInput,
)
from company.services.access import token_hash


def event(database: AsyncSession, order_id: UUID, kind: str, message: str) -> None:
    database.add(OrderEvent(order_id=order_id, kind=kind, message=message))


async def load_order(database: AsyncSession, order_id: UUID, *, lock: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if lock:
        query = query.with_for_update()
    order = await database.scalar(query)
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


async def latest_snapshot(database: AsyncSession, order_id: UUID) -> InputSnapshot:
    snapshot = await database.scalar(
        select(InputSnapshot)
        .where(InputSnapshot.order_id == order_id)
        .order_by(InputSnapshot.revision.desc())
        .limit(1)
    )
    if snapshot is None:
        raise HTTPException(409, "Order input snapshot is unavailable.")
    return snapshot


def conflicts(facts: list[dict]) -> list[str]:
    candidates: dict[str, set[tuple[str, str | None]]] = {}
    for fact in facts:
        candidates.setdefault(canonical_key(fact["key"]), set()).add(
            (fact["value"], fact.get("unit"))
        )
    return sorted(key for key, values in candidates.items() if len(values) > 1)


async def summary(database: AsyncSession, order: Order, *, internal: bool = False) -> OrderSummary:
    from company.services.support import ai_reply_paused

    snapshot = await latest_snapshot(database, order.id)
    missing_fields = missing_delivery_fields(order.product_name, snapshot.facts)
    labels = {"pack_quantity": "包装数量", "net_volume": "单瓶净含量及单位", "use_cases": "用途"}
    delivery_blockers = [f"正式交付前需确认：{labels.get(key, key)}" for key in missing_fields]
    active_job = await database.scalar(
        select(Job)
        .where(Job.order_id == order.id, Job.status.in_(["queued", "running"]))
        .order_by(Job.created_at)
        .limit(1)
    )
    failed_job = await database.scalar(
        select(Job)
        .join(Command, Command.id == Job.command_id)
        .where(
            Job.order_id == order.id,
            Job.status == "failed",
            Command.expected_revision == order.revision,
            Command.status == "failed",
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    workflow_error = None
    if failed_job and not active_job:
        failed_step = await database.scalar(
            select(ProductionStepReceipt)
            .where(
                ProductionStepReceipt.command_id == failed_job.command_id,
                ProductionStepReceipt.status == "failed",
            )
            .order_by(ProductionStepReceipt.created_at.desc())
            .limit(1)
        )
        workflow_error = (
            failed_step.error_summary if failed_step else "任务已中断，请负责人查看记录后授权恢复。"
        )
    phase_status = {
        "INTAKE": "queued",
        "NEEDS_INFO": "waiting_for_seller",
        "NEEDS_IMAGE": "waiting_for_seller",
        "AWAITING_ACCEPTANCE": "waiting_for_owner",
        "AWAITING_RELEASE": "waiting_for_owner",
        "AWAITING_SELLER": "waiting_for_seller",
        "RESEARCH": "research_pending",
        "REJECTED": "finished",
        "COMPLETED": "finished",
    }
    workflow_status = (
        active_job.status
        if active_job
        else "failed"
        if failed_job
        else phase_status.get(order.phase, "processing")
    )
    actions = allowed_actions(
        order.phase, internal=internal, pending=order.pending_command_id is not None
    )
    if internal and order.phase == "AWAITING_RELEASE" and actions:
        version = await database.scalar(
            select(DeliverableVersion)
            .where(DeliverableVersion.order_id == order.id)
            .order_by(DeliverableVersion.version_number.desc())
            .limit(1)
        )
        current = version is not None and version.input_snapshot_id == snapshot.id
        if not current or version.status in {"vetoed", "delivered"}:
            actions = []
        elif not (
            version.status == "review_passed" and version.text_passed and version.full_passed
        ):
            actions = [action for action in actions if action != "release"]
        if missing_fields or (
            version and len(version.assets) != len(version.draft.get("image_plan", []))
        ):
            actions = [action for action in actions if action != "release"]
        if version and len(version.assets) != len(version.draft.get("image_plan", [])):
            delivery_blockers.append("图片计划与实际图片数量不一致，需重新制作并审校。")
    if not internal and order.phase == "AWAITING_SELLER" and actions:
        from company.services.publication import PublicationError, current_delivery, feedback_base

        delivered = await database.scalar(
            select(DeliverySnapshot)
            .where(
                DeliverySnapshot.order_id == order.id,
            )
            .order_by(DeliverySnapshot.version_number.desc())
            .limit(1)
        )
        actions = []
        if delivered is not None:
            try:
                await feedback_base(database, order, delivered.version_id)
                actions.append("feedback")
            except PublicationError:
                pass
            try:
                await current_delivery(database, order, delivered.version_id)
                if order.publish_mode == "simulated":
                    actions.append("confirm_publish")
            except PublicationError:
                pass
    intake = await database.scalar(
        select(AgentRun)
        .where(
            AgentRun.order_id == order.id,
            AgentRun.input_revision == order.revision,
            AgentRun.role == "acquisition",
            AgentRun.status == "succeeded",
        )
        .order_by(AgentRun.finished_at.desc())
        .limit(1)
    )
    return OrderSummary(
        id=order.id,
        phase=order.phase,
        revision=order.revision,
        seller_name=(await database.get(SellerAccount, order.seller_account_id)).display_name
        if order.seller_account_id
        else "未关联账号",
        product_name=order.product_name,
        required_fact_keys=list(required_keys({"product_name": order.product_name})),
        missing_delivery_fields=missing_fields,
        delivery_blockers=delivery_blockers,
        product_details=order.product_details,
        domestic_product_url=order.domestic_product_url,
        facts=snapshot.facts,
        intake_analysis=intake.result
        if intake and (intake.result or {}).get("strategy_version") == "evidence-intake-v1"
        else None,
        notes=snapshot.notes,
        created_at=order.created_at,
        updated_at=order.updated_at,
        pending_command_id=order.pending_command_id,
        conflicting_fact_keys=conflicts(snapshot.facts),
        workflow_status=workflow_status,
        workflow_error=workflow_error,
        allowed_actions=actions,
        ai_reply_paused=await ai_reply_paused(database, order.id),
    )


async def internal_detail(database: AsyncSession, order: Order) -> InternalOrder:
    public = await summary(database, order, internal=True)
    snapshots = (
        await database.scalars(
            select(InputSnapshot)
            .where(InputSnapshot.order_id == order.id)
            .order_by(InputSnapshot.revision)
        )
    ).all()
    commands = (
        await database.scalars(
            select(Command).where(Command.order_id == order.id).order_by(Command.created_at)
        )
    ).all()
    decisions = (
        await database.scalars(
            select(HumanDecision)
            .where(HumanDecision.order_id == order.id)
            .order_by(HumanDecision.created_at)
        )
    ).all()
    runs = (
        await database.scalars(
            select(AgentRun).where(AgentRun.order_id == order.id).order_by(AgentRun.created_at)
        )
    ).all()
    return InternalOrder(
        **public.model_dump(),
        contact=(await database.get(SellerAccount, order.seller_account_id)).email
        if order.seller_account_id
        else None,
        input_snapshots=[
            SnapshotView(
                id=item.id,
                revision=item.revision,
                facts=item.facts,
                notes=item.notes,
                source=item.source,
                created_at=item.created_at,
            )
            for item in snapshots
        ],
        commands=[
            CommandView(
                id=item.id,
                kind=item.kind,
                status=item.status,
                expected_revision=item.expected_revision,
                created_at=item.created_at,
            )
            for item in commands
        ],
        decisions=[
            DecisionView(
                id=item.id,
                action=item.action,
                status=item.status,
                version_id=item.version_id,
                command_id=item.command_id,
                created_at=item.created_at,
            )
            for item in decisions
        ],
        agent_runs=[
            {
                "id": str(item.id),
                "role": item.role,
                "status": item.status,
                "input_revision": item.input_revision,
                "model": item.model,
                "usage": item.usage,
                "elapsed_seconds": item.elapsed_seconds,
                "error_summary": item.error_summary,
                "result": item.result,
                "created_at": item.created_at.isoformat(),
            }
            for item in runs
        ],
    )


async def create_order(
    database: AsyncSession,
    settings: Settings,
    body: CreateOrder,
    *,
    seller_account_id: UUID | None = None,
) -> CreatedOrder:
    order = Order(
        seller_account_id=seller_account_id,
        product_name=body.product_name,
        product_details=body.product_details,
        domestic_product_url=body.domestic_product_url,
        publish_mode=settings.publish_mode,
    )
    database.add(order)
    await database.flush()
    saved_facts = [fact.model_dump() for fact in body.facts]
    profile = known_brand(body.product_name)
    if profile:
        for fact in profile["facts"]:
            if not any(f["key"] == fact["key"] for f in saved_facts):
                saved_facts.append(fact)
    token = secrets.token_urlsafe(32)
    database.add_all(
        [
            InputSnapshot(
                order_id=order.id,
                revision=1,
                facts=saved_facts,
                notes=body.notes,
                source="seller_form",
            ),
            OrderAccess(
                token_hash=token_hash(token),
                order_id=order.id,
                expires_at=now() + timedelta(days=settings.order_link_days),
            ),
        ]
    )
    command = Command(
        order_id=order.id,
        kind="START",
        expected_revision=1,
        idempotency_key="system:start",
        request_hash=hashlib.sha256(str(order.id).encode()).hexdigest(),
        payload={},
    )
    database.add(command)
    await database.flush()
    database.add(Job(order_id=order.id, command_id=command.id, kind="START"))
    event(database, order.id, "order_created", "资料已保存，等待获客工作流处理；尚未完成资料审查。")
    await database.commit()
    return CreatedOrder(
        id=order.id,
        phase=order.phase,
        revision=order.revision,
        private_url=f"{settings.public_app_url.rstrip('/')}/o/{order.id}#token={token}",
    )


def check_edit(order: Order, revision: int, action: str) -> None:
    if order.revision != revision:
        raise HTTPException(409, "Order revision changed; refresh before submitting.")
    if action not in allowed_actions(
        order.phase, internal=False, pending=order.pending_command_id is not None
    ):
        raise HTTPException(409, "This action is not available in the current order phase.")


async def add_supplement(
    database: AsyncSession, order_id: UUID, body: SupplementInput
) -> OrderSummary:
    order = await load_order(database, order_id, lock=True)
    check_edit(order, body.expected_revision, "supplement")
    if order.phase == "AWAITING_RELEASE":
        order.phase = "INTAKE"
    previous = await latest_snapshot(database, order_id)
    # A seller explicitly submits a COMPLETE new snapshot. Older values remain
    # immutable in history; duplicate different values in this snapshot remain conflicts.
    facts = (
        [fact.model_dump() for fact in body.facts]
        if body.facts is not None
        else [dict(fact) for fact in previous.facts]
    )
    order.revision += 1
    order.updated_at = now()
    database.add(
        InputSnapshot(
            order_id=order_id,
            revision=order.revision,
            facts=facts,
            notes=body.notes if body.notes is not None else previous.notes,
            source="seller_supplement",
        )
    )
    command = Command(
        order_id=order_id,
        kind="SUPPLEMENT",
        expected_revision=order.revision,
        idempotency_key=f"system:supplement:{order.revision}",
        request_hash=hashlib.sha256(body.model_dump_json().encode()).hexdigest(),
        payload=body.model_dump(mode="json"),
    )
    database.add(command)
    await database.flush()
    database.add(Job(order_id=order_id, command_id=command.id, kind="RESUME"))
    event(database, order_id, "supplement_received", "补充资料已保存，等待工作流重新审查。")
    await database.commit()
    return await summary(database, order)


async def add_message(database: AsyncSession, order_id: UUID, body: MessageInput) -> MessageView:
    order = await load_order(database, order_id, lock=True)
    check_edit(order, body.expected_revision, "message")
    previous = await latest_snapshot(database, order_id)
    order.revision += 1
    order.updated_at = now()
    database.add(
        InputSnapshot(
            order_id=order_id,
            revision=order.revision,
            facts=previous.facts,
            notes=previous.notes,
            source="seller_message",
        )
    )
    message = Message(
        order_id=order_id, role="seller", content=body.content, input_revision=order.revision
    )
    database.add(message)
    event(database, order_id, "seller_message_received", "卖家消息已保存，尚无自动回复。")
    # A new message is a new input revision, but does not silently rewrite facts.
    command = Command(
        order_id=order_id,
        kind="MESSAGE",
        expected_revision=order.revision,
        idempotency_key=f"system:message:{secrets.token_hex(16)}",
        request_hash=hashlib.sha256(body.model_dump_json().encode()).hexdigest(),
        payload={"content": body.content},
    )
    database.add(command)
    await database.flush()
    database.add(Job(order_id=order_id, command_id=command.id, kind="RESUME"))
    await database.commit()
    return MessageView(
        id=message.id, role=message.role, content=message.content, created_at=message.created_at
    )


async def enqueue_decision(
    database: AsyncSession, order_id: UUID, body: DecisionInput, idempotency_key: str
) -> QueuedCommand:
    order = await load_order(database, order_id, lock=True)
    request = body.model_dump(mode="json")
    # Preserve the request hash of pre-M5 accept/reject commands for safe replay.
    if request["reason"] is None:
        request.pop("reason")
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    previous = await database.scalar(
        select(Command).where(
            Command.order_id == order_id, Command.idempotency_key == idempotency_key
        )
    )
    if previous:
        if previous.request_hash != digest:
            raise HTTPException(409, "Idempotency key was already used for different input.")
        return QueuedCommand(
            command_id=previous.id, order_id=order_id, revision=previous.expected_revision
        )
    if order.revision != body.expected_revision:
        raise HTTPException(409, "Order revision changed; refresh before submitting.")
    if body.action not in allowed_actions(
        order.phase, internal=True, pending=order.pending_command_id is not None
    ):
        raise HTTPException(409, "This decision is unavailable in the current order state.")
    if body.action in {"accept", "reject"}:
        if body.version_id is not None:
            raise HTTPException(409, "Acceptance decisions cannot target a production version.")
    else:
        version = await database.scalar(
            select(DeliverableVersion)
            .where(DeliverableVersion.order_id == order_id)
            .order_by(DeliverableVersion.version_number.desc())
            .limit(1)
        )
        snapshot = await latest_snapshot(database, order_id)
        if body.action == "release":
            if missing_delivery_fields(order.product_name, snapshot.facts):
                raise HTTPException(
                    409, "Required SKU facts must be confirmed before formal delivery."
                )
            if version and len(version.assets) != len(version.draft.get("image_plan", [])):
                raise HTTPException(
                    409, "Every planned image must exist and pass review before delivery."
                )
        if (
            body.version_id is None
            or version is None
            or version.id != body.version_id
            or version.input_snapshot_id != snapshot.id
            or version.status in {"vetoed", "delivered"}
        ):
            raise HTTPException(409, "Select this order's latest candidate for the current input.")
        if body.action == "release" and not (
            version.status == "review_passed" and version.text_passed and version.full_passed
        ):
            raise HTTPException(409, "The complete text and image review must pass before release.")
        if body.action == "veto" and not (body.reason and body.reason.strip()):
            raise HTTPException(409, "A specific veto reason is required.")
    command = Command(
        order_id=order_id,
        kind=body.action.upper(),
        expected_revision=body.expected_revision,
        idempotency_key=idempotency_key,
        request_hash=digest,
        payload=body.model_dump(mode="json"),
    )
    database.add(command)
    await database.flush()
    database.add_all(
        [
            HumanDecision(
                order_id=order_id,
                command_id=command.id,
                action=body.action,
                version_id=body.version_id,
            ),
            Job(order_id=order_id, command_id=command.id, kind="RESUME"),
        ]
    )
    order.pending_command_id = command.id
    event(database, order_id, "decision_queued", "负责人操作已排队，等待工作流处理；尚未生效。")
    await database.commit()
    return QueuedCommand(command_id=command.id, order_id=order_id, revision=order.revision)


async def refresh_intake_sources(
    database: AsyncSession, order_id: UUID, *, reason: str = "reference_updated"
) -> None:
    """A changed reference set invalidates an in-flight review without changing old snapshots."""
    order = await load_order(database, order_id, lock=True)
    if order.phase not in {"INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"}:
        await database.rollback()
        return
    # Before the first job starts, it will already read the newest reference set.
    if order.phase == "INTAKE":
        pending = await database.scalar(
            select(Command)
            .where(
                Command.order_id == order_id,
                Command.kind == "START",
                Command.expected_revision == order.revision,
            )
            .limit(1)
        )
        if pending:
            queued = await database.scalar(
                select(Job.id).where(Job.command_id == pending.id, Job.status == "queued").limit(1)
            )
            started = await database.scalar(
                select(AgentRun.id).where(AgentRun.command_id == pending.id).limit(1)
            )
            if queued and started is None:
                await database.rollback()
                return
    previous = await latest_snapshot(database, order_id)
    order.revision += 1
    order.phase, order.updated_at = "INTAKE", now()
    database.add(
        InputSnapshot(
            order_id=order_id,
            revision=order.revision,
            facts=previous.facts,
            notes=previous.notes,
            source="intake_strategy_update"
            if reason == "strategy_updated"
            else "seller_reference_update",
        )
    )
    command = Command(
        order_id=order_id,
        kind="SUPPLEMENT",
        expected_revision=order.revision,
        idempotency_key=f"system:reference-review:{order.revision}",
        request_hash=hashlib.sha256(f"reference:{order.revision}".encode()).hexdigest(),
        payload={},
    )
    database.add(command)
    await database.flush()
    database.add(Job(order_id=order_id, command_id=command.id, kind="RESUME"))
    order.pending_command_id = command.id
    event(
        database,
        order_id,
        "intake_review_queued",
        "已安排按新版初审策略重新分析现有商品资料。"
        if reason == "strategy_updated"
        else "参考素材已更新，顾问将结合新图重新分析。",
    )
    await database.commit()
