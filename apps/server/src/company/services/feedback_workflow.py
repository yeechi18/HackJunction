"""Durable feedback classification receipt, separate from graph routing and version creation."""

from uuid import UUID

from sqlalchemy import select

from company.agents.feedback import FeedbackResult, inspect_feedback
from company.persistence.agent_runs import AgentRun
from company.persistence.database import Database
from company.persistence.models import Command, InputSnapshot, Order, now
from company.persistence.production import DeliverableVersion
from company.persistence.publication import FeedbackRequest
from company.providers.mimo import MiMoProvider, ProviderError
from company.providers.usage import record_call, tracked_provider, unwrapped_provider
from company.services.production import CommitGuard, digest


async def classify_feedback(
    database: Database,
    order_id: UUID,
    command_id: UUID,
    provider,
    *,
    commit_guard: CommitGuard | None = None,
) -> FeedbackResult:
    provider = tracked_provider(provider)
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        command = await session.get(Command, command_id)
        request = await session.scalar(
            select(FeedbackRequest).where(
                FeedbackRequest.order_id == order_id,
                FeedbackRequest.command_id == command_id,
            )
        )
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.command_id == command_id,
                AgentRun.role == "acquisition_feedback",
            )
        )
        if (
            order is None
            or command is None
            or request is None
            or command.order_id != order_id
            or command.kind != "FEEDBACK"
        ):
            raise ProviderError("Feedback does not belong to the requested order.")
        if run is not None and run.status == "succeeded":
            return FeedbackResult.model_validate(run.result)
        if (
            order is None
            or command is None
            or request is None
            or command.kind != "FEEDBACK"
            or command.order_id != order_id
            or order.revision != command.expected_revision
            or request.resulting_revision != order.revision
            or command.status in {"failed", "stale", "cancelled"}
            or order.phase != "AWAITING_SELLER"
        ):
            raise ProviderError("Feedback classification no longer matches current seller input.")
        snapshot = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == order_id,
                InputSnapshot.revision == order.revision,
            )
        )
        version = await session.get(DeliverableVersion, request.version_id)
        base = await session.get(InputSnapshot, version.input_snapshot_id) if version else None
        if snapshot is None or base is None or version.order_id != order_id:
            raise ProviderError("The feedback base version or fact snapshot is unavailable.")
        context = {
            "product_name": order.product_name,
            "content": request.content,
            "fact_updates": request.fact_updates,
            "facts": snapshot.facts,
            "base_facts": base.facts,
            "base_version_id": str(version.id),
            "previous_version": version.draft,
        }
        fingerprint = digest(context)
        if run is None:
            run = AgentRun(
                order_id=order_id,
                command_id=command_id,
                role="acquisition_feedback",
                input_snapshot_id=snapshot.id,
                input_revision=order.revision,
            )
            session.add(run)
        else:
            run.status, run.error_summary = "running", None
        await session.flush()
        run_id, input_revision = run.id, order.revision
    traced_provider = unwrapped_provider(provider)
    if isinstance(traced_provider, MiMoProvider):
        traced_provider.trace_context = {
            "order_id": str(order_id),
            "command_id": str(command_id),
            "role": "acquisition_feedback",
            "step_key": "feedback",
        }
    result = await inspect_feedback(context, provider)
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        command = await session.get(Command, command_id)
        request = await session.scalar(
            select(FeedbackRequest).where(
                FeedbackRequest.command_id == command_id,
            )
        )
        snapshot = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == order_id,
                InputSnapshot.revision == input_revision,
            )
        )
        run = await session.get(AgentRun, run_id)
        run.result = result.model_dump(mode="json")
        record_call(run, "usage", provider.last_call)
        run.finished_at = now()
        current = {
            **context,
            "content": request.content,
            "fact_updates": request.fact_updates,
            "facts": snapshot.facts if snapshot else [],
        }
        if (
            order.revision != input_revision
            or command.expected_revision != input_revision
            or command.status in {"failed", "stale", "cancelled"}
            or order.phase != "AWAITING_SELLER"
            or digest(current) != fingerprint
        ):
            run.status = "stale"
            await session.commit()
            raise ProviderError(
                "Feedback inputs changed during classification; result was not applied."
            )
        run.status = "succeeded"
        request.classification = result.model_dump(mode="json")
    return result
