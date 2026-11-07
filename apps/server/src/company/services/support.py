"""Order support and explicit operational records, without external messaging or graph calls."""

import hashlib
import json
from collections import Counter
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.persistence.agent_runs import AgentRun
from company.persistence.models import Command, Job, Message, Order, OrderEvent, now
from company.persistence.production import DeliverableVersion, ProductionStepReceipt, ReviewReport
from company.persistence.publication import FeedbackRequest, PublishAttempt
from company.persistence.support import (
    RetryPermission,
    SupportEntry,
    SupportTicket,
)
from company.schemas.orders import MessageView
from company.schemas.support import (
    OrderMetrics,
    RetryInput,
    RetryQueued,
    SupportReplyInput,
    SupportRequestInput,
    SupportTicketList,
    SupportTicketView,
    TokenTotals,
)

INTAKE_PHASES = {"INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"}


def request_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def order_record(session: AsyncSession, order_id: UUID, *, lock=False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if lock:
        query = query.with_for_update()
    order = await session.scalar(query)
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


async def ai_reply_paused(session: AsyncSession, order_id: UUID) -> bool:
    return (
        await session.scalar(
            select(SupportTicket.id)
            .where(
                SupportTicket.order_id == order_id,
                SupportTicket.status == "open",
            )
            .limit(1)
        )
        is not None
    )


async def ticket_view(session: AsyncSession, ticket: SupportTicket) -> SupportTicketView:
    messages = await session.scalars(
        select(Message)
        .join(SupportEntry, SupportEntry.message_id == Message.id)
        .where(
            SupportEntry.ticket_id == ticket.id,
        )
        .order_by(SupportEntry.created_at, SupportEntry.id)
    )
    return SupportTicketView(
        id=ticket.id,
        order_id=ticket.order_id,
        status=ticket.status,
        requested_phase=ticket.requested_phase,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
        ai_reply_paused=ticket.status == "open",
        messages=[
            MessageView(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


async def support_list(
    session: AsyncSession,
    *,
    order_id: UUID | None = None,
    status: str = "all",
) -> SupportTicketList:
    query = select(SupportTicket)
    if order_id is not None:
        await order_record(session, order_id)
        query = query.where(SupportTicket.order_id == order_id)
    if status != "all":
        query = query.where(SupportTicket.status == status)
    records = list(
        await session.scalars(query.order_by(SupportTicket.created_at.desc()).limit(100))
    )
    return SupportTicketList(
        items=[await ticket_view(session, record) for record in records],
        ai_reply_paused=await ai_reply_paused(session, order_id) if order_id else None,
    )


async def prior_entry(
    session: AsyncSession, order_id: UUID, actor: str, key: str, digest: str
) -> SupportEntry | None:
    entry = await session.scalar(
        select(SupportEntry).where(
            SupportEntry.order_id == order_id,
            SupportEntry.actor == actor,
            SupportEntry.idempotency_key == key,
        )
    )
    if entry is not None and entry.request_hash != digest:
        raise HTTPException(409, "Idempotency key was already used for different support content.")
    return entry


async def request_support(
    session: AsyncSession,
    order_id: UUID,
    body: SupportRequestInput,
    key: str | None = None,
) -> SupportTicketView:
    order = await order_record(session, order_id, lock=True)
    digest = request_hash(body.model_dump())
    if key:
        prior = await prior_entry(session, order_id, "seller", key, digest)
        if prior is not None:
            return await ticket_view(session, await session.get(SupportTicket, prior.ticket_id))
    ticket = await session.scalar(
        select(SupportTicket).where(
            SupportTicket.order_id == order_id, SupportTicket.status == "open"
        )
    )
    if ticket is None:
        ticket = SupportTicket(id=uuid4(), order_id=order_id, requested_phase=order.phase)
        session.add(ticket)
        await session.flush()
        session.add(
            OrderEvent(
                order_id=order_id,
                kind="support_requested",
                message="已请求人工客服，自动获客回复暂停；生产流程保持原状态。",
            )
        )
    key = key or f"request:{ticket.id}:{digest[:24]}"
    if await prior_entry(session, order_id, "seller", key, digest) is not None:
        return await ticket_view(session, ticket)
    message = Message(
        order_id=order_id, role="seller", content=body.content, input_revision=order.revision
    )
    session.add(message)
    await session.flush()
    session.add(
        SupportEntry(
            ticket_id=ticket.id,
            order_id=order_id,
            actor="seller",
            message_id=message.id,
            idempotency_key=key,
            request_hash=digest,
        )
    )
    await session.commit()
    return await ticket_view(session, ticket)


async def owner_reply(
    session: AsyncSession,
    ticket_id: UUID,
    body: SupportReplyInput,
    key: str | None = None,
) -> SupportTicketView:
    ticket = await session.get(SupportTicket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Support ticket not found.")
    order = await order_record(session, ticket.order_id, lock=True)
    digest = request_hash(body.model_dump())
    key = key or f"reply:{ticket.id}:{digest[:24]}"
    prior = await prior_entry(session, order.id, "owner", key, digest)
    if prior is not None:
        if prior.ticket_id != ticket.id:
            raise HTTPException(409, "Idempotency key belongs to another support ticket.")
        return await ticket_view(session, ticket)
    # Refresh after the order lock so concurrent resolutions cannot both enqueue a wakeup.
    await session.refresh(ticket)
    if ticket.status != "open":
        raise HTTPException(409, "This support ticket is already resolved.")
    message = Message(
        order_id=order.id, role="human", content=body.content, input_revision=order.revision
    )
    session.add(message)
    await session.flush()
    session.add(
        SupportEntry(
            ticket_id=ticket.id,
            order_id=order.id,
            actor="owner",
            message_id=message.id,
            idempotency_key=key,
            request_hash=digest,
        )
    )
    if body.resolve:
        ticket.status, ticket.resolved_at = "resolved", now()
        active_job = await session.scalar(
            select(Job.id)
            .where(
                Job.order_id == order.id,
                Job.status.in_(["queued", "running"]),
            )
            .limit(1)
        )
        resume = (
            order.phase in INTAKE_PHASES and order.pending_command_id is None and active_job is None
        )
        if resume:
            command = Command(
                order_id=order.id,
                kind="MESSAGE",
                expected_revision=order.revision,
                idempotency_key=f"system:support-resume:{ticket.id}",
                request_hash=digest,
                payload={
                    "content": "人工客服已结束，请依据本单已确认资料恢复资料审查。",
                    "support_ticket_id": str(ticket.id),
                },
            )
            session.add(command)
            await session.flush()
            session.add(Job(order_id=order.id, command_id=command.id, kind="RESUME"))
            order.pending_command_id = command.id
        session.add(
            OrderEvent(
                order_id=order.id,
                kind="support_resolved",
                message="人工客服已结束，已安排恢复自动资料审查。"
                if resume
                else "人工客服已结束，订单阶段保持原状态。",
                payload={"ticket_id": str(ticket.id), "intake_resume_queued": resume},
            )
        )
    await session.commit()
    return await ticket_view(session, ticket)


async def quality_budget_exhausted(session: AsyncSession, order_id: UUID, command_id: UUID) -> bool:
    feedback = await session.scalar(
        select(FeedbackRequest).where(FeedbackRequest.command_id == command_id)
    )
    if feedback is not None and feedback.candidate_count >= feedback.candidate_limit:
        return True
    events = await session.scalars(
        select(OrderEvent).where(
            OrderEvent.order_id == order_id,
            OrderEvent.kind.in_(
                [
                    "production_revision_budget_exhausted",
                    "feedback_budget_exhausted",
                    "feedback_revision_budget_exhausted",
                ]
            ),
        )
    )
    for event in events:
        if event.payload.get("command_id") == str(command_id):
            return True
        try:
            version_id = UUID(str(event.payload.get("version_id", "")))
        except ValueError:
            continue
        version = await session.get(DeliverableVersion, version_id)
        if (
            version is not None
            and version.order_id == order_id
            and version.command_id == command_id
        ):
            return True
    return False


async def queue_retry(
    session: AsyncSession,
    order_id: UUID,
    body: RetryInput,
    key: str,
) -> RetryQueued:
    order = await order_record(session, order_id, lock=True)
    digest = request_hash(body.model_dump(mode="json"))
    previous = await session.scalar(
        select(RetryPermission).where(
            RetryPermission.order_id == order_id, RetryPermission.idempotency_key == key
        )
    )
    if previous is not None:
        if previous.request_hash != digest:
            raise HTTPException(409, "Idempotency key was already used for a different retry.")
        return RetryQueued(
            retry_id=previous.id,
            command_id=previous.command_id,
            job_id=previous.job_id,
            revision=previous.expected_revision,
        )
    command = await session.get(Command, body.command_id)
    if command is None or command.order_id != order_id:
        raise HTTPException(404, "Command not found for this order.")
    if (
        order.revision != body.expected_revision
        or command.expected_revision != order.revision
        or command.status != "failed"
        or order.phase in {"COMPLETED", "REJECTED"}
        or order.pending_command_id not in {None, command.id}
    ):
        raise HTTPException(
            409, "Only a current failed command without another pending operation may be retried."
        )
    if await quality_budget_exhausted(session, order_id, command.id):
        raise HTTPException(
            409,
            "Quality candidate budget is exhausted; provide new information or a new request.",
        )
    active = await session.scalar(
        select(Job.id)
        .where(Job.order_id == order_id, Job.status.in_(["queued", "running"]))
        .limit(1)
    )
    if active is not None:
        raise HTTPException(409, "Another order job is already queued or running.")
    job = await session.scalar(
        select(Job)
        .where(Job.order_id == order_id, Job.command_id == command.id, Job.status == "failed")
        .order_by(Job.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if job is None:
        raise HTTPException(409, "The command has no failed job eligible for a controlled retry.")
    old_attempts = job.attempts
    # Grant one scheduler opportunity; retain existing logical step receipts and business budgets.
    job.attempts = max(0, old_attempts - 1)
    job.lease_generation += 1
    job.lease_until, job.available_at, job.status = None, now(), "queued"
    command.status, order.pending_command_id = "queued", command.id
    permission = RetryPermission(
        order_id=order_id,
        command_id=command.id,
        job_id=job.id,
        expected_revision=order.revision,
        old_attempts=old_attempts,
        new_attempts=job.attempts,
        reason=body.reason,
        idempotency_key=key,
        request_hash=digest,
    )
    session.add(permission)
    session.add(
        OrderEvent(
            order_id=order_id,
            kind="owner_retry_authorized",
            visibility="internal",
            message="负责人授权恢复技术失败任务，保留成功步骤凭据和业务返修预算。",
            payload={
                "command_id": str(command.id),
                "job_id": str(job.id),
                "old_attempts": old_attempts,
                "new_attempts": job.attempts,
            },
        )
    )
    await session.commit()
    return RetryQueued(
        retry_id=permission.id, command_id=command.id, job_id=job.id, revision=order.revision
    )


def model_call_count(entry: dict) -> int:
    value = entry["usage"].get("_model_calls", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 1


def usage_by_mode(entries: list[dict]) -> tuple[str, dict[str, int], dict[str, TokenTotals]]:
    """Classify provider records; offline zero tokens must not mask unknown live usage."""
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        mode = (
            "offline-fixture"
            if entry["model"] == "offline-fixture"
            else "live"
            if entry["model"]
            else "unknown"
        )
        groups.setdefault(mode, []).append(entry)
    calls = {
        mode: sum(model_call_count(entry) for entry in group) for mode, group in groups.items()
    }
    tokens = {}
    for mode, group in groups.items():
        totals = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            values = [entry["usage"].get(key) for entry in group]
            known = [
                value
                for value in values
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            ]
            totals[key] = sum(known) if known else None
        tokens[mode] = TokenTotals(**totals)
    mode = "none" if not groups else "mixed" if len(groups) > 1 else next(iter(groups))
    return "offline" if mode == "offline-fixture" else mode, calls, tokens


async def order_metrics(session: AsyncSession, order_id: UUID) -> OrderMetrics:
    order = await order_record(session, order_id)
    intake = list(await session.scalars(select(AgentRun).where(AgentRun.order_id == order_id)))
    production = list(
        await session.scalars(
            select(ProductionStepReceipt).where(ProductionStepReceipt.order_id == order_id)
        )
    )
    publication = list(
        await session.scalars(select(PublishAttempt).where(PublishAttempt.order_id == order_id))
    )
    entries = (
        [{"role": row.role, "model": row.model, "usage": row.usage, "cost": None} for row in intake]
        + [
            {"role": row.role, "model": row.model, "usage": row.usage, "cost": row.cost_usd}
            for row in production
        ]
        + [
            {
                "role": "publishing",
                "model": row.model,
                "usage": row.plan_usage,
                "cost": row.cost_usd,
            }
            for row in publication
        ]
    )
    reported = [entry for entry in entries if entry["model"] is not None or entry["usage"]]
    model_mode, calls_by_mode, tokens_by_mode = usage_by_mode(reported)
    sums = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [entry["usage"].get(key) for entry in reported]
        valid = [
            value
            for value in values
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ]
        sums[key] = sum(valid) if valid else None
    known_costs = [entry["cost"] for entry in reported if entry["cost"] is not None]
    versions = list(
        await session.scalars(
            select(DeliverableVersion).where(DeliverableVersion.order_id == order_id)
        )
    )
    failed_reviews = await session.scalar(
        select(func.count())
        .select_from(ReviewReport)
        .where(
            ReviewReport.order_id == order_id,
            ReviewReport.full_passed.is_(False),
            ReviewReport.text_passed.is_(False),
        )
    )
    feedback_count = await session.scalar(
        select(func.count())
        .select_from(FeedbackRequest)
        .where(FeedbackRequest.order_id == order_id)
    )
    open_tickets = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.order_id == order_id, SupportTicket.status == "open")
    )
    return OrderMetrics(
        order_id=order_id,
        publish_mode=order.publish_mode,
        recorded_model_calls=sum(model_call_count(entry) for entry in reported),
        calls_by_role=dict(
            sum(
                (Counter({entry["role"]: model_call_count(entry)}) for entry in reported),
                Counter(),
            )
        ),
        model_mode=model_mode,
        calls_by_mode=calls_by_mode,
        tokens_by_mode=tokens_by_mode,
        attempted_steps=len(entries),
        steps_without_reported_usage=sum(
            not any(
                isinstance(entry["usage"].get(key), int)
                and not isinstance(entry["usage"].get(key), bool)
                and entry["usage"].get(key) >= 0
                for key in ("input_tokens", "output_tokens", "total_tokens")
            )
            for entry in entries
        ),
        tokens=TokenTotals(**sums),
        known_cost_usd=sum(known_costs, Decimal(0)) if known_costs else None,
        total_cost_usd=sum(known_costs, Decimal(0))
        if entries and len(known_costs) == len(entries)
        else None,
        versions=len(versions),
        revision_candidates=max(0, len(versions) - 1),
        automatic_rework_candidates=sum(version.round_index > 0 for version in versions),
        failed_reviews=failed_reviews,
        feedback_requests=feedback_count,
        open_support_tickets=open_tickets,
        explanations={
            "scope": "仅当前订单的模型用量及版本记录；模拟发布不等于真实成交。",
            "model_mode": (
                "offline-fixture为本地测试响应，已知API token为0，不计作真实MiMo调用；"
                "模式按持久化model标签分类，未返回model的调用列为unknown。"
            ),
            "model_calls": (
                "按已保存模型/用量结果的执行凭据计数；未返回用量的尝试不虚算为已计费调用。"
            ),
            "tokens": (
                "只累计实际返回并持久化的token字段；未知为null，部分记录缺失时总数仅表示已知部分。"
            ),
            "cost": (
                "known_cost为已记录部分；任一调用费用未知时total_cost为null，"
                "不将免费额度或缺失费用当0。"
            ),
            "revisions": (
                "revision_candidates为初版以外候选版本数；"
                "automatic_rework仅round_index大于0的版本。"
            ),
        },
    )
