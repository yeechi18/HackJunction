"""Durable company workflow: jobs wake one graph; receipts own business effects."""

import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command as GraphCommand
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.agents.acquisition import inspect_intake
from company.config import Settings
from company.domain.facts import canonical_key
from company.persistence.agent_runs import AgentRun
from company.persistence.assets import Asset
from company.persistence.database import Database, checkpoint_conninfo
from company.persistence.models import (
    Command,
    HumanDecision,
    InputSnapshot,
    Job,
    Message,
    Order,
    OrderEvent,
    now,
)
from company.persistence.production import DeliverableVersion, ReviewReport
from company.persistence.publication import FeedbackRequest, PublishAttempt
from company.providers.factory import build_provider
from company.providers.mimo import ProviderError
from company.providers.usage import attach_call, record_call
from company.services.delivery import DeliveryError, release_version, veto_version
from company.services.feedback_workflow import classify_feedback
from company.services.image_workflow import render_version_images
from company.services.intake_sources import load_intake_sources
from company.services.model_budget import BudgetedProvider, OrderBudgetExceeded
from company.services.production import (
    StepResult,
    accepted_revision,
    generation_step,
    research_step,
    review_step,
)
from company.services.publication import PublicationPending, publish_confirmed
from company.services.support import ai_reply_paused
from company.tools.image_layout import MissingReferenceImage
from company.workflows.state import IntakeState

WAITING_PHASES = {"INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"}
FINISHED_COMMANDS = {"applied", "stale", "cancelled", "failed"}
MAX_JOB_ATTEMPTS = 3  # Initial attempt plus at most two automatic retries.
MAX_PRODUCTION_CANDIDATES = 3


class WorkflowError(RuntimeError):
    """Sanitized workflow failure, without database or provider request payloads."""


@dataclass(frozen=True)
class JobLease:
    job_id: UUID
    order_id: UUID
    command_id: UUID
    generation: int
    attempts: int


def _route(phase: str) -> str:
    return (
        "wait"
        if phase in WAITING_PHASES | {"NEEDS_IMAGE", "AWAITING_RELEASE", "AWAITING_SELLER"}
        else "end"
    )


class WorkflowContext:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        lease: JobLease,
        provider=None,
        *,
        include_production: bool = True,
        include_images: bool = True,
        publisher=None,
    ):
        self.settings = settings
        self.database = database
        self.lease = lease
        self.provider = (
            BudgetedProvider(provider, database, settings, lease.order_id) if provider else None
        )
        self.owns_provider = provider is None
        self.include_production = include_production
        self.include_images = include_images
        self.publisher = publisher

    def _provider(self, role=None, step_key=None):
        if self.provider is None:
            self.provider = BudgetedProvider(
                build_provider(self.settings), self.database, self.settings, self.lease.order_id
            )
        if role and hasattr(self.provider.inner, "trace_context"):
            self.provider.inner.trace_context = {
                "order_id": str(self.lease.order_id),
                "command_id": str(self.lease.command_id),
                "role": role,
                "step_key": step_key or role,
            }
        self.provider.last_call = None
        return self.provider

    async def close(self) -> None:
        if self.owns_provider and self.provider is not None:
            await self.provider.close()

    async def _owned_job(self, session: AsyncSession) -> Job:
        job = await session.scalar(select(Job).where(Job.id == self.lease.job_id).with_for_update())
        if job is None or job.status != "running" or job.lease_generation != self.lease.generation:
            raise WorkflowError("Execution lease changed; this result cannot be committed.")
        return job

    async def _records(self, session: AsyncSession, state: IntakeState) -> tuple[Order, Command]:
        order = await session.scalar(
            select(Order).where(Order.id == self.lease.order_id).with_for_update()
        )
        command = await session.get(Command, UUID(state["command_id"]))
        if order is None or command is None or command.order_id != order.id:
            raise WorkflowError("The workflow command no longer matches this order.")
        return order, command

    @staticmethod
    async def _stale(session: AsyncSession, order: Order, command: Command, message: str) -> None:
        if command.status not in FINISHED_COMMANDS:
            command.status = "stale"
            session.add(OrderEvent(order_id=order.id, kind="command_stale", message=message))
            for decision in await session.scalars(
                select(HumanDecision).where(HumanDecision.command_id == command.id)
            ):
                decision.status = "stale"
        if order.pending_command_id == command.id:
            order.pending_command_id = None

    async def dispatch(self, state: IntakeState) -> dict:
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            if command.status == "failed":
                await self._stale(session, order, command, "旧命令重试预算已用尽，未自动重新执行。")
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            version_id = ""
            production_round = 0
            if command.kind in {"START", "SUPPLEMENT", "MESSAGE"}:
                route = "intake"
            elif command.kind in {"ACCEPT", "REJECT"}:
                route = "decision"
            elif command.kind == "PRODUCE":
                if (
                    self.include_production
                    and order.phase == "RESEARCH"
                    and order.revision == command.expected_revision
                    and await accepted_revision(session, order)
                ):
                    route = "research"
                else:
                    await self._stale(
                        session, order, command, "当前阶段不能启动生产；接单或输入依据已变化。"
                    )
                    route = _route(order.phase)
            elif command.kind == "IMAGES":
                try:
                    candidate_id = UUID(command.payload.get("version_id", ""))
                except (ValueError, TypeError):
                    candidate_id = None
                version = (
                    await session.get(DeliverableVersion, candidate_id) if candidate_id else None
                )
                snapshot = await session.scalar(
                    select(InputSnapshot).where(
                        InputSnapshot.order_id == order.id,
                        InputSnapshot.revision == order.revision,
                    )
                )
                if (
                    self.include_production
                    and self.include_images
                    and order.phase == "NEEDS_IMAGE"
                    and order.revision == command.expected_revision
                    and version is not None
                    and version.order_id == order.id
                    and version.text_passed
                    and snapshot is not None
                    and version.input_snapshot_id == snapshot.id
                    and await accepted_revision(session, order)
                ):
                    order.phase, order.updated_at = "REVIEW", now()
                    order.pending_command_id = command.id
                    route, version_id = "render_images", str(version.id)
                    production_round = version.round_index + int(bool(version.assets))
                else:
                    await self._stale(session, order, command, "补图请求依据已变化，未修改旧版本。")
                    route = _route(order.phase)
            elif command.kind in {"RELEASE", "VETO"}:
                route = "delivery_decision"
                version_id = str(command.payload.get("version_id", ""))
            elif command.kind == "FEEDBACK":
                route = "feedback"
            elif command.kind == "CONFIRM_PUBLISH":
                route = "publish"
            else:
                await self._stale(session, order, command, "当前阶段不支持该命令，未执行操作。")
                route = _route(order.phase)
            return {
                "phase": order.phase,
                "route": route,
                "production_round": production_round,
                "version_id": version_id,
            }

    async def intake(self, state: IntakeState) -> dict:
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.command_id == command.id, AgentRun.role == "acquisition"
                )
            )
            if run is not None and run.status == "succeeded":
                return {"phase": order.phase, "route": _route(order.phase)}
            if command.status == "failed":
                await self._stale(session, order, command, "旧命令重试预算已用尽，未自动重新执行。")
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if await ai_reply_paused(session, order.id):
                command.status = "applied"
                if order.pending_command_id == command.id:
                    order.pending_command_id = None
                session.add(
                    OrderEvent(
                        order_id=order.id,
                        kind="intake_reply_paused",
                        visibility="internal",
                        message="人工工单处理中，已暂停本次自动获客回复。",
                        payload={"command_id": str(command.id)},
                    )
                )
                return {"phase": order.phase, "route": "wait"}
            if order.revision != command.expected_revision or order.phase not in WAITING_PHASES:
                await self._stale(
                    session, order, command, "输入或订单阶段已变化，未应用旧的资料审查。"
                )
                return {"phase": order.phase, "route": _route(order.phase)}
            snapshot = await session.scalar(
                select(InputSnapshot).where(
                    InputSnapshot.order_id == order.id, InputSnapshot.revision == order.revision
                )
            )
            if snapshot is None:
                raise WorkflowError("The expected immutable input snapshot is unavailable.")
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(Message.order_id == order.id)
                    .order_by(Message.created_at.desc())
                    .limit(12)
                )
            )
            references = [
                {
                    "id": str(image.id),
                    "width": image.width,
                    "height": image.height,
                    "method": image.method,
                    "storage_key": image.storage_key,
                    "sha256": image.sha256,
                }
                for image in await session.scalars(
                    select(Asset)
                    .where(Asset.order_id == order.id, Asset.kind == "reference")
                    .order_by(Asset.created_at.desc(), Asset.id)
                    .limit(3)
                )
            ]
            order_input = {
                "product_name": order.product_name,
                "product_details": order.product_details,
                "domestic_product_url": order.domestic_product_url,
                "facts": [{**fact, "key": canonical_key(fact["key"])} for fact in snapshot.facts],
                "notes": snapshot.notes,
                "messages": [
                    {"role": item.role, "content": item.content} for item in reversed(messages)
                ],
            }
            if run is None:
                run = AgentRun(
                    order_id=order.id,
                    command_id=command.id,
                    input_snapshot_id=snapshot.id,
                    input_revision=order.revision,
                    role="acquisition",
                    status="running",
                )
                session.add(run)
            else:
                run.status = "running"
                run.error_summary = None
            await session.flush()
            run_id, input_revision = run.id, order.revision

        # No ORM transaction or row lock is held during a model call.
        order_input, image_paths = await load_intake_sources(self.settings, order_input, references)
        result = await inspect_intake(
            order_input, self._provider("acquisition", "intake"), image_paths=image_paths
        )

        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise WorkflowError("The agent execution receipt is unavailable.")
            run.result = result.model_dump(mode="json")
            record_call(run, "usage", self.provider.last_call)
            run.finished_at = now()
            if await ai_reply_paused(session, order.id):
                run.status, command.status = "suppressed", "applied"
                if order.pending_command_id == command.id:
                    order.pending_command_id = None
                session.add(
                    OrderEvent(
                        order_id=order.id,
                        kind="intake_reply_suppressed",
                        visibility="internal",
                        message="人工接管期间的模型结果已记录用量，未发送自动回复。",
                        payload={"command_id": str(command.id), "run_id": str(run.id)},
                    )
                )
            elif order.revision != input_revision or order.phase not in WAITING_PHASES:
                run.status = "stale"
                await self._stale(
                    session, order, command, "商品资料已更新，旧审查结果未覆盖新资料。"
                )
            else:
                run.status = "succeeded"
                command.status = "applied"
                order.phase = "AWAITING_ACCEPTANCE" if result.ready_for_acceptance else "NEEDS_INFO"
                order.updated_at = now()
                if order.pending_command_id == command.id:
                    order.pending_command_id = None
                content = result.summary
                if result.questions:
                    content += "\n" + "\n".join(f"• {question}" for question in result.questions)
                session.add(
                    Message(
                        order_id=order.id,
                        role="assistant",
                        content=content,
                        input_revision=input_revision,
                    )
                )
                session.add(
                    OrderEvent(
                        order_id=order.id,
                        kind="intake_completed",
                        message="商品初审与文案方向已整理，等待负责人决定接单。"
                        if result.ready_for_acceptance
                        else "商品初审已记录，请查看顾问回复。",
                        payload={
                            "command_id": str(command.id),
                            "run_id": str(run.id),
                            "missing_fields": result.missing_fields,
                            "conflicts": [item.model_dump() for item in result.conflicts],
                        },
                    )
                )
            return {"phase": order.phase, "route": _route(order.phase)}

    async def decision(self, state: IntakeState) -> dict:
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            decision = await session.scalar(
                select(HumanDecision).where(HumanDecision.command_id == command.id)
            )
            # A checkpoint replay must not invalidate an already applied human decision.
            if decision is not None and decision.status == "applied":
                route = (
                    "research"
                    if self.include_production
                    and command.kind == "ACCEPT"
                    and command.status not in FINISHED_COMMANDS
                    else _route(order.phase)
                )
                return {"phase": order.phase, "route": route, "production_round": 0}
            if command.status == "failed":
                await self._stale(session, order, command, "旧命令重试预算已用尽，未自动重新执行。")
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": "end"}
            completed_intake = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.order_id == order.id,
                    AgentRun.role == "acquisition",
                    AgentRun.input_revision == order.revision,
                    AgentRun.status == "succeeded",
                )
                .order_by(AgentRun.finished_at.desc())
                .limit(1)
            )
            valid = (
                order.phase == "AWAITING_ACCEPTANCE"
                and order.revision == command.expected_revision
                and completed_intake is not None
                and (completed_intake.result or {}).get("ready_for_acceptance") is True
            )
            if not valid or decision is None or command.kind not in {"ACCEPT", "REJECT"}:
                await self._stale(
                    session, order, command, "审批依据已变化，本次操作未生效，请刷新订单。"
                )
                return {"phase": order.phase, "route": "end"}
            order.phase = "RESEARCH" if command.kind == "ACCEPT" else "REJECTED"
            order.updated_at = now()
            if order.pending_command_id == command.id:
                order.pending_command_id = None
            production = self.include_production and command.kind == "ACCEPT"
            command.status = "running" if production else "applied"
            if production:
                order.pending_command_id = command.id
            decision.status = "applied"
            session.add(
                OrderEvent(
                    order_id=order.id,
                    kind="order_accepted" if command.kind == "ACCEPT" else "order_rejected",
                    message="负责人已批准接单，等待研究阶段执行。"
                    if command.kind == "ACCEPT"
                    else "负责人已拒绝接单，本单结束。",
                    payload={"command_id": str(command.id)},
                )
            )
            return {
                "phase": order.phase,
                "route": "research" if production else "end",
                "production_round": 0,
            }

    async def _production_ready(self, state: IntakeState) -> dict | None:
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if order.revision != command.expected_revision:
                await self._stale(session, order, command, "生产输入已变化，未应用旧步骤结果。")
                return {"phase": order.phase, "route": _route(order.phase)}
            if not self.include_production or order.phase not in {
                "RESEARCH",
                "GENERATION",
                "REVIEW",
            }:
                await self._stale(session, order, command, "当前订单不能执行该生产步骤。")
                return {"phase": order.phase, "route": _route(order.phase)}
        self._provider()
        return None

    async def _feedback_request(self, session, command):
        source_id = (
            command.payload.get("production_command_id") if command.kind == "IMAGES" else None
        )
        try:
            source_id = UUID(source_id) if source_id else command.id
        except ValueError:
            raise WorkflowError("The original feedback request is invalid.") from None
        return await session.scalar(
            select(FeedbackRequest).where(
                FeedbackRequest.order_id == self.lease.order_id,
                FeedbackRequest.command_id == source_id,
            )
        )

    async def feedback(self, state: IntakeState) -> dict:
        result = await classify_feedback(
            self.database,
            self.lease.order_id,
            UUID(state["command_id"]),
            self._provider("acquisition_feedback", "feedback"),
            commit_guard=self._owned_job,
        )
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            request = await self._feedback_request(session, command)
            if request is None:
                raise WorkflowError("The seller feedback request is unavailable.")
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if order.revision != command.expected_revision:
                await self._stale(session, order, command, "反馈资料已变化，旧分类未推进生产。")
                return {"phase": order.phase, "route": _route(order.phase)}
            if not result.actionable:
                if request.status != "waiting_for_seller":
                    content = "修改意见已整理，目前尚未生成新成品。\n" + "\n".join(result.questions)
                    session.add(
                        Message(
                            order_id=order.id,
                            role="assistant",
                            content=content,
                            input_revision=order.revision,
                        )
                    )
                    session.add(
                        OrderEvent(
                            order_id=order.id,
                            kind="feedback_requires_confirmation",
                            message="修改仍需确认事实、提供证据或负责人处理，请查看获客回复。",
                            payload={"feedback_id": str(request.id), "category": result.category},
                        )
                    )
                request.status = "waiting_for_seller"
                command.status, order.pending_command_id = "applied", None
                order.phase, order.updated_at = "AWAITING_SELLER", now()
                return {"phase": order.phase, "route": "wait"}
            request.status = "producing"
            command.status, order.pending_command_id = "running", command.id
            order.phase, order.updated_at = (
                ("RESEARCH" if result.requires_research else "GENERATION"),
                now(),
            )
            return {
                "phase": order.phase,
                "route": "research" if result.requires_research else "generation",
                "production_round": 0,
                "version_id": "",
            }

    async def publish(self, state: IntakeState) -> dict:
        result = await publish_confirmed(
            self.settings,
            self.lease.order_id,
            UUID(state["command_id"]),
            database=self.database,
            provider=self._provider("publishing", "publish_plan"),
            publisher=self.publisher,
            commit_guard=self._owned_job,
        )
        if result.status != "SIMULATED":
            raise PublicationPending("Only a reconciled SIMULATED receipt ends this demonstration.")
        return {"phase": "COMPLETED", "route": "end"}

    async def _apply_production_step(
        self,
        state: IntakeState,
        step: StepResult,
        *,
        next_phase: str,
        route: str,
    ) -> dict:
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if order.revision != step.input_revision or order.revision != command.expected_revision:
                await self._stale(
                    session, order, command, "缓存生产结果的输入已过期，未覆盖当前订单。"
                )
                return {"phase": order.phase, "route": _route(order.phase)}
            order.phase, order.updated_at = next_phase, now()
            return {"phase": next_phase, "route": route}

    async def research(self, state: IntakeState) -> dict:
        halted = await self._production_ready(state)
        if halted is not None:
            return halted
        result = await research_step(
            self.lease.order_id,
            UUID(state["command_id"]),
            self.provider,
            database=self.database,
            commit_guard=self._owned_job,
        )
        return await self._apply_production_step(
            state, result, next_phase="GENERATION", route="generation"
        )

    async def generation(self, state: IntakeState) -> dict:
        halted = await self._production_ready(state)
        if halted is not None:
            return halted
        round_index = state.get("production_round", 0)
        if round_index >= MAX_PRODUCTION_CANDIDATES:
            raise WorkflowError("Production candidate budget exhausted; manual review is required.")
        feedback = None
        async with self.database.sessions() as session:
            command = await session.get(Command, UUID(state["command_id"]))
            if command is not None and command.kind == "VETO":
                feedback = {"owner_veto": command.payload.get("reason", "")}
                if round_index:
                    previous_review = await session.scalar(
                        select(ReviewReport)
                        .where(
                            ReviewReport.order_id == self.lease.order_id,
                            ReviewReport.command_id == command.id,
                        )
                        .order_by(ReviewReport.created_at.desc())
                        .limit(1)
                    )
                    if previous_review is not None:
                        feedback["review_issues"] = previous_review.result.get("issues", [])
            request = await self._feedback_request(session, command) if command else None
            if request is not None:
                await self._owned_job(session)
                if round_index >= request.candidate_limit:
                    raise OrderBudgetExceeded("本次反馈的候选预算已用尽，请负责人处理。")
                request.candidate_count = max(request.candidate_count, round_index + 1)
                feedback = {
                    "content": request.content,
                    "classification": request.classification,
                    "base_version_id": str(request.version_id),
                    "source_feedback_id": str(request.id),
                }
                previous_review = await session.scalar(
                    select(ReviewReport)
                    .where(
                        ReviewReport.order_id == self.lease.order_id,
                        ReviewReport.command_id == command.id,
                    )
                    .order_by(ReviewReport.created_at.desc())
                    .limit(1)
                )
                if previous_review is not None:
                    feedback["review_issues"] = previous_review.result.get("issues", [])
                await session.commit()
        result = await generation_step(
            self.lease.order_id,
            UUID(state["command_id"]),
            round_index,
            self.provider,
            feedback=feedback,
            database=self.database,
            commit_guard=self._owned_job,
        )
        outcome = await self._apply_production_step(
            state, result, next_phase="REVIEW", route="review"
        )
        return {**outcome, "version_id": str(result.version_id)}

    async def review(self, state: IntakeState) -> dict:
        halted = await self._production_ready(state)
        if halted is not None:
            return halted
        result = await review_step(
            self.lease.order_id,
            UUID(state["command_id"]),
            UUID(state["version_id"]),
            self.provider,
            database=self.database,
            commit_guard=self._owned_job,
        )
        async with self.database.sessions() as session, session.begin():
            job = await self._owned_job(session)
            order, command = await self._records(session, state)
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if (
                order.revision != result.input_revision
                or order.revision != command.expected_revision
            ):
                await self._stale(session, order, command, "审校结果对应旧输入，未推进订单。")
                return {"phase": order.phase, "route": _route(order.phase)}
            if result.result["text_passed"]:
                return {"phase": order.phase, "route": "render_images"}
            next_round = state.get("production_round", 0) + 1
            if next_round >= MAX_PRODUCTION_CANDIDATES:
                command.status, order.pending_command_id = "failed", None
                job.status, job.lease_until = "failed", None
                session.add(
                    OrderEvent(
                        order_id=order.id,
                        kind="production_revision_budget_exhausted",
                        visibility="internal",
                        message="三个生产候选均未通过文本审校，已停止自动返修，等待人工处理。",
                        payload={
                            "candidates": MAX_PRODUCTION_CANDIDATES,
                            "version_id": state["version_id"],
                        },
                    )
                )
                return {"phase": order.phase, "route": "end", "production_round": next_round}
            order.phase, order.updated_at = "GENERATION", now()
            return {"phase": order.phase, "route": "generation", "production_round": next_round}

    async def _pause_text_only(self, state: IntakeState) -> dict:
        """M5 extension point. M4 pauses here without inventing any image artifact."""
        halted = await self._production_ready(state)
        if halted is not None:
            return halted
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            version = await session.get(DeliverableVersion, UUID(state["version_id"]))
            if version is None or version.order_id != order.id or not version.text_passed:
                raise WorkflowError("Images cannot proceed before this version passes text review.")
            revision = await session.scalar(
                select(InputSnapshot.revision).where(
                    InputSnapshot.id == version.input_snapshot_id,
                    InputSnapshot.order_id == order.id,
                )
            )
            if revision != order.revision or command.expected_revision != order.revision:
                await self._stale(session, order, command, "图片准备阶段对应旧资料，未推进订单。")
                return {"phase": order.phase, "route": _route(order.phase)}
            order.phase, order.updated_at = "AWAITING_RELEASE", now()
            command.status, order.pending_command_id = "applied", None
            session.add(
                OrderEvent(
                    order_id=order.id,
                    kind="production_text_review_passed",
                    visibility="internal",
                    message="文本审校通过；图片尚待制作及完整审校，不能交付。",
                    payload={"version_id": str(version.id), "full_passed": version.full_passed},
                )
            )
            return {"phase": order.phase, "route": "wait"}

    async def _wait_for_images(
        self,
        state: IntakeState,
        version_id: UUID,
        message: str,
        *,
        restore_text: bool = False,
    ) -> dict:
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if order.revision != command.expected_revision:
                await self._stale(session, order, command, "补图等待对应旧输入，未推进订单。")
                return {"phase": order.phase, "route": _route(order.phase)}
            version = await session.get(DeliverableVersion, version_id)
            if version is None or version.order_id != order.id:
                raise WorkflowError("The image candidate no longer belongs to this order.")
            if restore_text:
                version.text_passed = True  # The unchanged text passed its standalone review.
            version.full_passed = False
            order.phase, order.updated_at = "NEEDS_IMAGE", now()
            command.status = "applied"
            if order.pending_command_id == command.id:
                order.pending_command_id = None
            session.add(
                Message(
                    order_id=order.id,
                    role="assistant",
                    input_revision=order.revision,
                    content=message,
                )
            )
            session.add(
                OrderEvent(
                    order_id=order.id,
                    kind="reference_image_required",
                    message=message,
                    payload={"version_id": str(version_id)},
                )
            )
            return {"phase": order.phase, "route": "wait", "version_id": str(version_id)}

    async def render_images(self, state: IntakeState) -> dict:
        if not self.include_images:
            return await self._pause_text_only(state)
        halted = await self._production_ready(state)
        if halted is not None:
            return halted
        async with self.database.sessions() as session:
            command = await session.get(Command, UUID(state["command_id"]))
            reference_id = command.payload.get("reference_asset_id") if command else None
            request = await self._feedback_request(session, command) if command else None
            if request is not None:
                await self._owned_job(session)
                round_index = state.get("production_round", 0)
                if round_index >= request.candidate_limit:
                    raise OrderBudgetExceeded("本次反馈的候选预算已用尽，请负责人处理。")
                request.candidate_count = max(request.candidate_count, round_index + 1)
                await session.commit()
        version_id = UUID(state["version_id"])
        try:
            images = await render_version_images(
                self.database,
                self.settings,
                self.lease.order_id,
                version_id,
                UUID(state["command_id"]),
                reference_asset_id=UUID(reference_id) if reference_id else None,
                round_index=state.get("production_round", 0),
                commit_guard=self._owned_job,
            )
        except MissingReferenceImage:
            return await self._wait_for_images(
                state,
                version_id,
                "英文文案已通过文本审校，请上传可核实的商品参考图，"
                "再继续制作并审校完整图片；目前尚未交付成品。",
            )
        result = await review_step(
            self.lease.order_id,
            UUID(state["command_id"]),
            images.version_id,
            self.provider,
            image_paths=images.paths,
            database=self.database,
            commit_guard=self._owned_job,
        )
        blocking = [
            issue
            for issue in result.result.get("issues", [])
            if issue.get("severity") == "blocking"
        ]

        def image_issue(issue):
            return str(issue.get("field_path", "")).startswith("image") or bool(
                re.search(
                    r"图片|图像|照片|\bimage\b|\bphoto\b|\bsubject\b",
                    str(issue.get("reason", "")),
                    re.I,
                )
            )

        def subject_issue(issue):
            return image_issue(issue) and bool(
                re.search(
                    r"数量|个数|主体|配件|外观|不符|\bcount\b|\bquantity\b|\bsubject\b|\bmismatch\b",
                    str(issue.get("reason", "")) + " " + str(issue.get("rule_id", "")),
                    re.I,
                )
            )

        if (
            blocking
            and all(image_issue(issue) for issue in blocking)
            and any(subject_issue(issue) for issue in blocking)
        ):
            return await self._wait_for_images(
                state,
                images.version_id,
                "图片中的商品主体、数量或配件与确认资料不一致，请更换正确参考图。"
                "系统不会只改数量角标来掩盖原图差异；新图会生成新版本并再次完整审校。",
                restore_text=True,
            )
        async with self.database.sessions() as session, session.begin():
            job = await self._owned_job(session)
            order, command = await self._records(session, state)
            if command.status in FINISHED_COMMANDS:
                return {"phase": order.phase, "route": _route(order.phase)}
            if (
                order.revision != result.input_revision
                or command.expected_revision != order.revision
            ):
                await self._stale(session, order, command, "完整图文审校对应旧输入，未交付。")
                return {"phase": order.phase, "route": _route(order.phase)}
            if result.result["passed"]:
                order.phase, order.updated_at = "AWAITING_RELEASE", now()
                command.status, order.pending_command_id = "applied", None
                session.add(
                    OrderEvent(
                        order_id=order.id,
                        kind="production_full_review_passed",
                        visibility="internal",
                        message="完整图文审校通过，等待负责人本版放行或否决。",
                        payload={"version_id": str(images.version_id), "full_passed": True},
                    )
                )
                return {"phase": order.phase, "route": "wait", "version_id": str(images.version_id)}
            next_round = state.get("production_round", 0) + 1
            if next_round >= MAX_PRODUCTION_CANDIDATES:
                command.status, order.pending_command_id = "failed", None
                job.status, job.lease_until = "failed", None
                session.add(
                    OrderEvent(
                        order_id=order.id,
                        kind="production_revision_budget_exhausted",
                        visibility="internal",
                        message="本请求三个候选仍未通过完整图文审校，已停止自动返修。",
                        payload={"version_id": str(images.version_id), "candidates": next_round},
                    )
                )
                return {"phase": order.phase, "route": "end", "version_id": str(images.version_id)}
            order.phase, order.updated_at = "GENERATION", now()
            return {
                "phase": order.phase,
                "route": "generation",
                "production_round": next_round,
                "version_id": str(images.version_id),
            }

    async def delivery_decision(self, state: IntakeState) -> dict:
        async with self.database.sessions() as session:
            command = await session.get(Command, UUID(state["command_id"]))
            if command is None:
                raise WorkflowError("The delivery decision is unavailable.")
            kind, payload = command.kind, command.payload
        try:
            version_id = UUID(str(payload.get("version_id", "")))
        except ValueError:
            raise WorkflowError("The delivery decision requires a concrete version.") from None
        if kind == "RELEASE":
            await release_version(
                self.database,
                self.settings,
                self.lease.order_id,
                version_id,
                UUID(state["command_id"]),
                commit_guard=self._owned_job,
            )
            async with self.database.sessions() as session:
                order = await session.get(Order, self.lease.order_id)
                return {
                    "phase": order.phase,
                    "route": _route(order.phase),
                    "version_id": str(version_id),
                }
        if kind != "VETO":
            raise WorkflowError("Unsupported delivery decision.")
        await veto_version(
            self.database,
            self.settings,
            self.lease.order_id,
            version_id,
            UUID(state["command_id"]),
            reason=str(payload.get("reason") or ""),
            commit_guard=self._owned_job,
        )
        async with self.database.sessions() as session, session.begin():
            await self._owned_job(session)
            order, command = await self._records(session, state)
            if order.revision != command.expected_revision:
                await self._stale(session, order, command, "否决后的输入已变化，未继续旧生产流程。")
                return {"phase": order.phase, "route": _route(order.phase)}
            order.phase, order.updated_at = "GENERATION", now()
            command.status, order.pending_command_id = "running", command.id
        return {
            "phase": "GENERATION",
            "route": "generation",
            "production_round": 0,
            "version_id": "",
        }


async def _claim(database: Database, settings: Settings, order_id: UUID) -> JobLease | None:
    async with database.sessions() as session, session.begin():
        # Preserve per-order job order; a delayed old job must not be overtaken by a new reply.
        job = await session.scalar(
            select(Job)
            .where(Job.order_id == order_id, Job.status.in_(["queued", "running"]))
            .order_by(Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None or job.available_at > now():
            return None
        if job.status == "running" and job.lease_until and job.lease_until > now():
            return None
        if job.attempts >= MAX_JOB_ATTEMPTS:
            job.status = "failed"
            job.lease_until = None
            command = await session.get(Command, job.command_id)
            order = await session.get(Order, order_id)
            if command is not None and command.status not in FINISHED_COMMANDS:
                command.status = "failed"
            if order is not None and order.pending_command_id == job.command_id:
                order.pending_command_id = None
            session.add(
                OrderEvent(
                    order_id=order_id,
                    kind="workflow_failed",
                    visibility="internal",
                    message="执行租约失效次数达到上限，需要人工检查后重试。",
                )
            )
            return None
        job.status = "running"
        job.attempts += 1
        job.lease_generation += 1
        job.lease_until = now() + timedelta(seconds=max(180, settings.request_timeout_seconds * 3))
        command = await session.get(Command, job.command_id)
        if command is None:
            raise WorkflowError("The queued command is unavailable.")
        if command.status not in FINISHED_COMMANDS:
            command.status = "running"
        return JobLease(job.id, job.order_id, job.command_id, job.lease_generation, job.attempts)


async def _finish(database: Database, lease: JobLease) -> None:
    async with database.sessions() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
        if job is not None and job.lease_generation == lease.generation and job.status == "failed":
            return  # A graph-level business budget can deliberately terminate this job as failed.
        if job is None or job.lease_generation != lease.generation or job.status != "running":
            raise WorkflowError("Execution lease changed before job completion.")
        job.status = "done"
        job.lease_until = None


async def _record_failure(database: Database, lease: JobLease, error: Exception) -> None:
    if isinstance(error, DeliveryError):
        safe_error = str(error.detail)
    elif isinstance(error, (ProviderError, WorkflowError)):
        safe_error = str(error)
    else:
        safe_error = (
            "Workflow execution failed; retry after checking the local service configuration."
        )
    async with database.sessions() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
        call_usage = getattr(error, "call_usage", None)
        run = None
        if call_usage is not None:
            run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.command_id == lease.command_id,
                    AgentRun.role.in_(["acquisition", "acquisition_feedback"]),
                )
                .with_for_update()
            )
            if run is not None:
                record_call(run, "usage", call_usage)
            attempt = await session.scalar(
                select(PublishAttempt)
                .where(
                    PublishAttempt.command_id == lease.command_id,
                )
                .with_for_update()
            )
            if attempt is not None:
                record_call(attempt, "plan_usage", call_usage)
        if job is None or job.lease_generation != lease.generation or job.status != "running":
            return
        retry = lease.attempts < MAX_JOB_ATTEMPTS and not isinstance(
            error, (DeliveryError, OrderBudgetExceeded)
        )
        job.status = "queued" if retry else "failed"
        job.available_at = now() + timedelta(seconds=2 * lease.attempts)
        job.lease_until = None
        command = await session.get(Command, lease.command_id)
        if command is not None and command.status not in FINISHED_COMMANDS:
            command.status = "queued" if retry else "failed"
        if run is None:
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.command_id == lease.command_id,
                    AgentRun.role.in_(["acquisition", "acquisition_feedback"]),
                )
            )
        if run is not None and run.status == "running":
            record_call(run, "usage", getattr(error, "call_usage", None))
            run.status, run.error_summary, run.finished_at = "failed", safe_error, now()
        if not retry:
            order = await session.get(Order, lease.order_id)
            if order is not None and order.pending_command_id == lease.command_id:
                order.pending_command_id = None
            # The failed job/command is authoritative execution state. Preserve classified
            # feedback routing so an audited owner retry can resume its original step/budget.
            for decision in await session.scalars(
                select(HumanDecision).where(
                    HumanDecision.command_id == lease.command_id,
                    HumanDecision.status == "requested",
                )
            ):
                decision.status = "failed"
        session.add(
            OrderEvent(
                order_id=lease.order_id,
                kind="workflow_retry_scheduled" if retry else "workflow_failed",
                visibility="internal",
                message=safe_error,
                payload={
                    "command_id": str(lease.command_id),
                    "attempt": lease.attempts,
                    "retry_scheduled": retry,
                },
            )
        )


async def _drive(context: WorkflowContext, checkpointer) -> None:
    from company.workflows.graph import build_graph

    graph = build_graph(context, checkpointer)
    config = {"configurable": {"thread_id": str(context.lease.order_id)}, "recursion_limit": 30}
    snapshot = await graph.aget_state(config)
    if snapshot.next and not snapshot.interrupts:
        # Recover the original in-flight node before considering a new command.
        await graph.ainvoke(None, config, durability="sync")
        snapshot = await graph.aget_state(config)
    async with context.database.sessions() as session:
        command = await session.get(Command, context.lease.command_id)
        if command is None:
            raise WorkflowError("The queued command is unavailable.")
        if command.status in FINISHED_COMMANDS:
            return
        requested_wait = command.payload.get("wait_id")
    if snapshot.interrupts:
        wait_id = snapshot.values["command_id"]
        if requested_wait is not None and requested_wait != wait_id:
            async with context.database.sessions() as session, session.begin():
                await context._owned_job(session)
                order, command = await context._records(
                    session, {"command_id": str(context.lease.command_id)}
                )
                await context._stale(
                    session, order, command, "等待点已变化，旧回复未应用到新等待点。"
                )
            return
        await graph.ainvoke(
            GraphCommand(resume={"command_id": str(context.lease.command_id), "wait_id": wait_id}),
            config,
            durability="sync",
        )
    else:
        await graph.ainvoke(
            {"order_id": str(context.lease.order_id), "command_id": str(context.lease.command_id)},
            config,
            durability="sync",
        )
    async with context.database.sessions() as session:
        command = await session.get(Command, context.lease.command_id)
        if command is None or command.status not in FINISHED_COMMANDS:
            raise WorkflowError("The workflow did not produce a committed command receipt.")


async def process_one_job(
    settings: Settings,
    *,
    provider=None,
    order_id: UUID | None = None,
    include_production: bool = True,
    include_images: bool = True,
    publisher=None,
) -> bool:
    """Run one wake-up job until graph pause/end. False means no job was claimed.

    ``provider`` and optional ``order_id`` support isolated integration tests. Production
    uses a real provider lazily; there is no fake fallback after an API failure.
    """
    database = Database(settings)
    try:
        async with database.sessions() as session:
            query = select(Job.order_id).where(
                or_(Job.status == "queued", (Job.status == "running") & (Job.lease_until < now())),
                Job.available_at <= now(),
            )
            if order_id is not None:
                query = query.where(Job.order_id == order_id)
            candidates = list(
                dict.fromkeys(
                    await session.scalars(
                        query.order_by(Job.available_at, Job.created_at).limit(20)
                    )
                )
            )
        for candidate in candidates:
            async with await psycopg.AsyncConnection.connect(
                checkpoint_conninfo(settings), autocommit=True
            ) as lock_connection:
                key = int.from_bytes(candidate.bytes[:8], "big", signed=True)
                cursor = await lock_connection.execute("SELECT pg_try_advisory_lock(%s)", (key,))
                locked = (await cursor.fetchone())[0]
                if not locked:
                    continue
                try:
                    lease = await _claim(database, settings, candidate)
                    if lease is None:
                        continue
                    context = WorkflowContext(
                        settings,
                        database,
                        lease,
                        provider,
                        include_production=include_production,
                        include_images=include_images,
                        publisher=publisher,
                    )
                    try:
                        async with AsyncPostgresSaver.from_conn_string(
                            checkpoint_conninfo(settings)
                        ) as checkpointer:
                            await _drive(context, checkpointer)
                        await _finish(database, lease)
                    except Exception as error:
                        if context.provider is not None:
                            attach_call(error, context.provider.last_call)
                        await _record_failure(database, lease, error)
                    finally:
                        await context.close()
                    return True
                finally:
                    await lock_connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
        return False
    except psycopg.Error:
        raise WorkflowError("Workflow database connection is unavailable.") from None
    finally:
        await database.close()
