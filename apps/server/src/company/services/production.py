"""Single production steps with durable receipts; no business-stage orchestration here."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.agents.generation import generate_listing
from company.agents.research import research_product
from company.agents.review import review_listing
from company.config import Settings, get_settings
from company.domain.facts import canonical_key
from company.persistence.agent_runs import AgentRun
from company.persistence.assets import Asset
from company.persistence.database import Database
from company.persistence.models import Command, HumanDecision, InputSnapshot, Order, OrderEvent, now
from company.persistence.production import (
    DeliverableVersion,
    Evidence,
    ProductionStepReceipt,
    ResearchPackage,
    ReviewReport,
)
from company.persistence.publication import FeedbackRequest
from company.providers.mimo import ProviderError
from company.providers.usage import (
    attach_call,
    observed_call,
    record_call,
    tracked_provider,
    unwrapped_provider,
)
from company.schemas.listings import ListingDraft

CommitGuard = Callable[[AsyncSession], Awaitable[Any]]
PRODUCTION_PHASES = {"RESEARCH", "GENERATION", "REVIEW", "AWAITING_RELEASE", "AWAITING_SELLER"}


async def accepted_revision(session: AsyncSession, order: Order) -> bool:
    accepted = await session.scalar(
        select(HumanDecision.id)
        .join(Command, HumanDecision.command_id == Command.id)
        .where(
            HumanDecision.order_id == order.id,
            HumanDecision.action == "accept",
            HumanDecision.status == "applied",
            Command.expected_revision == order.revision,
        )
        .limit(1)
    )
    if accepted is not None:
        return True
    feedback = await session.scalar(
        select(FeedbackRequest)
        .where(
            FeedbackRequest.order_id == order.id,
            FeedbackRequest.resulting_revision == order.revision,
            FeedbackRequest.status.in_(["producing", "awaiting_release", "delivered"]),
            FeedbackRequest.fact_updates.is_not(None),
            FeedbackRequest.classification["actionable"].as_boolean().is_(True),
            FeedbackRequest.classification["category"].as_string() == "fact",
        )
        .order_by(FeedbackRequest.created_at.desc())
        .limit(1)
    )
    return feedback is not None


class ProductionError(ProviderError):
    """Safe step-level diagnostic, without raw SQL/provider payloads."""


class StepResult(BaseModel):
    receipt_id: UUID
    role: str
    artifact_id: UUID
    input_snapshot_id: UUID
    input_revision: int
    step_key: str
    version_id: UUID | None = None
    version_number: int | None = None
    result: dict
    cache_hit: bool = False


@dataclass(frozen=True)
class Prepared:
    order_id: UUID
    command_id: UUID
    receipt_id: UUID
    attempt_token: UUID
    input_snapshot_id: UUID
    input_revision: int
    evidence_hash: str
    evidence_snapshot: list[dict]
    context: dict
    role: str
    step_key: str
    round_index: int
    research_id: UUID | None = None
    parent_version_id: UUID | None = None
    version_id: UUID | None = None


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def evidence_view(record: Evidence) -> dict:
    return {
        "id": str(record.id),
        "kind": record.kind,
        "source": record.source,
        "source_url": record.source_url,
        "provider": record.provider,
        "status": record.status,
        "observed_at": record.observed_at.isoformat(),
        "scope": record.scope,
        "is_demo": record.is_demo,
        "content": record.content,
        "data": record.data,
        "created_at": record.created_at.isoformat(),
        # Only explicitly supplied observation fields become pricing inputs.
        "price": record.data.get("price"),
        "currency": record.data.get("currency"),
    }


async def evidence_snapshot(session: AsyncSession, order_id: UUID) -> list[dict]:
    records = await session.scalars(
        select(Evidence)
        .where(Evidence.order_id == order_id)
        .order_by(Evidence.created_at, Evidence.id)
    )
    return [evidence_view(record) for record in records]


def confirmed_facts(snapshot: InputSnapshot) -> list[dict]:
    facts: dict[str, dict] = {}
    for record in snapshot.facts:
        if record.get("confirmed") is not True:
            continue
        key = canonical_key(record["key"])
        fact = {
            "id": f"fact:{key}",
            "key": key,
            "value": record["value"],
            "unit": record.get("unit"),
        }
        if key in facts and facts[key] != fact:
            raise ProductionError("Confirmed input facts still contain conflicting values.")
        facts[key] = fact
    return list(facts.values())


async def source_facts(session: AsyncSession, order: Order, snapshot: InputSnapshot) -> list[dict]:
    """Keep confirmations and seller descriptions at separate provenance levels."""
    result = {
        fact["key"]: {**fact, "provenance": "seller_confirmed"}
        for fact in confirmed_facts(snapshot)
    }
    result.setdefault(
        "product_name",
        {
            "id": "fact:product_name",
            "key": "product_name",
            "value": order.product_name,
            "unit": None,
            "provenance": "seller_provided",
            "source_quote": order.product_name,
            "source_kind": "seller_input",
        },
    )
    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.input_snapshot_id == snapshot.id,
            AgentRun.role == "acquisition",
            AgentRun.status == "succeeded",
        )
        .order_by(AgentRun.finished_at.desc())
        .limit(1)
    )
    grouped = {}
    for fact in (run.result or {}).get("source_facts", []) if run else []:
        key = canonical_key(fact["key"])
        if key in result or fact.get("source") == "product_page":
            continue
        # A request for a performance/authenticity claim cannot become its own evidence.
        if re.search(
            r"防水|认证|正版|官方授权|waterproof|certif|official|authentic", fact["value"], re.I
        ):
            continue
        grouped.setdefault(key, []).append(fact)
    for key, records in grouped.items():
        if len({(item["value"], item.get("unit")) for item in records}) != 1:
            continue
        fact = records[0]
        result[key] = {
            "id": f"fact:{key}",
            "key": key,
            "value": fact["value"],
            "unit": fact.get("unit"),
            "provenance": "seller_provided",
            "source_quote": fact["source_quote"],
            "source_kind": "seller_input",
        }
    return list(result.values())


def receipt_result(receipt: ProductionStepReceipt, *, cache_hit: bool) -> StepResult:
    if receipt.artifact_id is None:
        raise ProductionError("A completed production receipt is missing its artifact.")
    return StepResult(
        receipt_id=receipt.id,
        role=receipt.role,
        artifact_id=receipt.artifact_id,
        input_snapshot_id=receipt.input_snapshot_id,
        input_revision=receipt.input_revision,
        step_key=receipt.step_key,
        version_id=receipt.version_id,
        version_number=receipt.version_number,
        result=receipt.result,
        cache_hit=cache_hit,
    )


@asynccontextmanager
async def database_context(database: Database | None, settings: Settings | None):
    owned = database is None
    instance = database or Database(settings or get_settings())
    try:
        yield instance
    finally:
        if owned:
            await instance.close()


async def prepare(
    database: Database,
    order_id: UUID,
    command_id: UUID,
    role: str,
    step_key: str,
    *,
    round_index: int = 0,
    feedback: Any = None,
    version_id: UUID | None = None,
    image_hash: str | None = None,
    provider=None,
    commit_guard: CommitGuard | None = None,
) -> Prepared | StepResult:
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        command = await session.get(Command, command_id)
        if order is None or command is None or command.order_id != order_id:
            raise ProductionError("Production command does not belong to the requested order.")
        receipt = await session.scalar(
            select(ProductionStepReceipt).where(
                ProductionStepReceipt.order_id == order_id,
                ProductionStepReceipt.command_id == command_id,
                ProductionStepReceipt.step_key == step_key,
            )
        )
        if receipt is not None and receipt.status == "succeeded":
            return receipt_result(receipt, cache_hit=True)
        if (
            order.revision != command.expected_revision
            or order.phase not in PRODUCTION_PHASES
            or command.status in {"failed", "stale", "cancelled"}
        ):
            raise ProductionError("Production input or command is no longer current.")
        snapshot = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == order_id,
                InputSnapshot.revision == order.revision,
            )
        )
        if snapshot is None:
            raise ProductionError("The immutable production input snapshot is unavailable.")
        evidence = await evidence_snapshot(session, order_id)
        evidence_hash = digest(evidence)
        feedback_request = None
        source_command = command
        source_command_id = command.payload.get("production_command_id", str(command.id))
        if command.kind in {"FEEDBACK", "IMAGES"}:
            try:
                source_command_id = UUID(str(source_command_id))
            except ValueError:
                raise ProductionError("The source production command is invalid.") from None
            feedback_request = await session.scalar(
                select(FeedbackRequest).where(
                    FeedbackRequest.command_id == source_command_id,
                    FeedbackRequest.order_id == order_id,
                )
            )
            if command.kind == "IMAGES":
                source_command = await session.get(Command, source_command_id)
                if source_command is None or source_command.order_id != order_id:
                    raise ProductionError("The original image production command is unavailable.")
        if isinstance(feedback, dict) and feedback.get("source_feedback_id"):
            try:
                feedback_id = UUID(feedback["source_feedback_id"])
            except (ValueError, TypeError):
                raise ProductionError("Feedback does not identify a valid request.") from None
            feedback_request = await session.get(FeedbackRequest, feedback_id)
            permitted_command = str(command.payload.get("production_command_id", command.id))
            if (
                feedback_request is None
                or feedback_request.order_id != order_id
                or str(feedback_request.command_id) != permitted_command
                or feedback_request.resulting_revision != order.revision
                or feedback_request.status != "producing"
                or not feedback_request.classification.get("actionable")
            ):
                raise ProductionError("Feedback is not an approved current production request.")
        if feedback_request is not None and (
            feedback_request.resulting_revision != order.revision
            or feedback_request.status != "producing"
            or not (feedback_request.classification or {}).get("actionable")
        ):
            raise ProductionError("The original feedback is no longer approved for production.")
        context = {
            "product_name": order.product_name,
            "product_details": order.product_details,
            "domestic_product_url": order.domestic_product_url,
            "seller_requirements": snapshot.notes,
            "reference_images": [
                {
                    "id": str(image.id),
                    "width": image.width,
                    "height": image.height,
                    "method": image.method,
                }
                for image in await session.scalars(
                    select(Asset)
                    .where(Asset.order_id == order.id, Asset.kind == "reference")
                    .order_by(Asset.created_at)
                    .limit(16)
                )
            ],
            "confirmed_facts": await source_facts(session, order, snapshot),
            "evidence": evidence,
        }
        intake = await session.scalar(
            select(AgentRun)
            .where(
                AgentRun.input_snapshot_id == snapshot.id,
                AgentRun.role == "acquisition",
                AgentRun.status == "succeeded",
            )
            .order_by(AgentRun.finished_at.desc())
            .limit(1)
        )
        context["initial_analysis"] = intake.result if intake else None
        research_id = parent_id = None
        if role != "research":
            reuse_base = None
            if feedback_request and feedback_request.classification.get("category") == "style":
                reuse_base = await session.get(DeliverableVersion, feedback_request.version_id)
                if (
                    reuse_base is None
                    or reuse_base.order_id != order_id
                    or reuse_base.input_snapshot_id != snapshot.id
                ):
                    raise ProductionError(
                        "Style changes must preserve the delivered fact snapshot."
                    )
            elif source_command.kind == "VETO":
                try:
                    reuse_base = await session.get(
                        DeliverableVersion, UUID(source_command.payload["version_id"])
                    )
                except (ValueError, KeyError, TypeError):
                    raise ProductionError("The veto base version is invalid.") from None
                if (
                    reuse_base is None
                    or reuse_base.order_id != order_id
                    or reuse_base.input_snapshot_id != snapshot.id
                    or reuse_base.status != "vetoed"
                ):
                    raise ProductionError("Veto production must preserve its current base facts.")
            research = (
                await session.get(ResearchPackage, reuse_base.research_package_id)
                if reuse_base
                else await session.scalar(
                    select(ResearchPackage)
                    .where(
                        ResearchPackage.order_id == order_id,
                        ResearchPackage.input_snapshot_id == snapshot.id,
                        ResearchPackage.evidence_hash == evidence_hash,
                    )
                    .order_by(ResearchPackage.created_at.desc())
                    .limit(1)
                )
            )
            if research is None:
                raise ProductionError(
                    "Current inputs require a new research package before production."
                )
            research_id = research.id
            context["research"] = research.result
            context["buyer_questions"] = research.result.get("buyer_questions", [])
            if reuse_base:
                context["evidence"] = research.evidence_snapshot
        if role == "generation":
            previous = await session.scalar(
                select(DeliverableVersion)
                .where(
                    DeliverableVersion.order_id == order_id,
                    DeliverableVersion.input_snapshot_id == snapshot.id,
                    or_(
                        DeliverableVersion.command_id != command_id,
                        DeliverableVersion.round_index < round_index,
                    ),
                )
                .order_by(DeliverableVersion.version_number.desc())
                .limit(1)
            )
            if previous is None and feedback_request:
                previous = await session.get(DeliverableVersion, feedback_request.version_id)
            if previous is not None:
                parent_id = previous.id
                context["previous_version"] = previous.draft
                if feedback is None:
                    review = await session.scalar(
                        select(ReviewReport)
                        .where(
                            ReviewReport.version_id == previous.id,
                        )
                        .order_by(ReviewReport.created_at.desc())
                        .limit(1)
                    )
                    feedback = review.result.get("issues", []) if review else None
            context["feedback"] = feedback
        if role == "review":
            version = await session.get(DeliverableVersion, version_id)
            if (
                version is None
                or version.order_id != order_id
                or version.input_snapshot_id != snapshot.id
            ):
                raise ProductionError("Review version does not match the current immutable inputs.")
            context["draft"] = version.draft
            parent_id = version.id
        prompt = files("company").joinpath(f"prompts/{role}.md").read_text(encoding="utf-8")
        dependency_hash = digest(
            {
                "context": context,
                "input_snapshot_id": snapshot.id,
                "research_id": research_id,
                "parent_id": parent_id,
                "image_hash": image_hash,
                "prompt": prompt,
                "model": getattr(
                    getattr(provider, "settings", None), "mimo_model", "injected-provider"
                ),
            }
        )
        token = uuid4()
        if receipt is None:
            receipt = ProductionStepReceipt(
                order_id=order_id,
                command_id=command_id,
                step_key=step_key,
                role=role,
                input_snapshot_id=snapshot.id,
                input_revision=order.revision,
                dependency_hash=dependency_hash,
                attempt_token=token,
            )
            session.add(receipt)
        else:
            receipt.status, receipt.attempt_token = "running", token
            receipt.attempts += 1
            receipt.dependency_hash = dependency_hash
            receipt.error_summary, receipt.finished_at = None, None
        await session.flush()
        return Prepared(
            order_id,
            command_id,
            receipt.id,
            token,
            snapshot.id,
            order.revision,
            evidence_hash,
            evidence,
            context,
            role,
            step_key,
            round_index,
            research_id,
            parent_id,
            version_id,
        )


def record_usage(receipt: ProductionStepReceipt, result: BaseModel, call_usage=None) -> dict:
    payload = result.model_dump(mode="json")
    receipt.result = payload
    record_call(receipt, "usage", call_usage or observed_call(result))
    receipt.cost_usd = None
    return payload


async def record_failure(
    database: Database,
    prepared: Prepared,
    error: Exception,
    result: BaseModel | None = None,
    call_usage=None,
) -> None:
    async with database.sessions() as session, session.begin():
        receipt = await session.scalar(
            select(ProductionStepReceipt)
            .where(
                ProductionStepReceipt.id == prepared.receipt_id,
            )
            .with_for_update()
        )
        if receipt is not None:
            # A superseded attempt still incurred this actual call; only accounting is merged.
            record_call(receipt, "usage", call_usage)
        if (
            receipt is not None
            and receipt.attempt_token == prepared.attempt_token
            and receipt.status == "running"
        ):
            if result is not None:
                record_usage(receipt, result, call_usage)
            else:
                record_call(receipt, "usage", call_usage)
            receipt.status = "failed"
            receipt.error_summary = (
                str(error)
                if isinstance(error, ProviderError)
                else "Agent execution failed; inspect the step inputs and retry."
            )
            receipt.finished_at = now()


async def commit_result(
    database: Database,
    prepared: Prepared,
    result: BaseModel,
    commit_guard: CommitGuard | None,
    call_usage=None,
) -> StepResult:
    async with database.sessions() as session:
        if commit_guard is not None:
            await commit_guard(session)
        order = await session.scalar(
            select(Order).where(Order.id == prepared.order_id).with_for_update()
        )
        receipt = await session.scalar(
            select(ProductionStepReceipt)
            .where(
                ProductionStepReceipt.id == prepared.receipt_id,
            )
            .with_for_update()
        )
        if receipt is None:
            raise ProductionError("Production step receipt is unavailable.")
        if receipt.status == "succeeded":
            record_call(receipt, "usage", call_usage)
            await session.commit()
            return receipt_result(receipt, cache_hit=True)
        if receipt.attempt_token != prepared.attempt_token:
            raise ProductionError("A newer attempt owns this production step.")
        current_evidence = await evidence_snapshot(session, prepared.order_id)
        command = await session.get(Command, prepared.command_id)
        payload = record_usage(receipt, result, call_usage)
        if (
            order is None
            or order.revision != prepared.input_revision
            or order.phase not in PRODUCTION_PHASES
            or command is None
            or command.status in {"stale", "cancelled", "failed"}
            or digest(current_evidence) != prepared.evidence_hash
        ):
            receipt.status, receipt.finished_at = "stale", now()
            receipt.error_summary = (
                "Inputs or evidence changed during the model call; result was not applied."
            )
            await session.commit()
            raise ProductionError(receipt.error_summary)
        operation_key = f"{prepared.command_id}:{prepared.step_key}"
        version = None
        if prepared.role == "research":
            artifact = ResearchPackage(
                order_id=order.id,
                command_id=prepared.command_id,
                input_snapshot_id=prepared.input_snapshot_id,
                input_revision=prepared.input_revision,
                evidence_hash=prepared.evidence_hash,
                evidence_snapshot=prepared.evidence_snapshot,
                result=payload,
                operation_key=operation_key,
            )
        elif prepared.role == "generation":
            latest = await session.scalar(
                select(func.max(DeliverableVersion.version_number)).where(
                    DeliverableVersion.order_id == order.id,
                )
            )
            version = artifact = DeliverableVersion(
                order_id=order.id,
                command_id=prepared.command_id,
                version_number=(latest or 0) + 1,
                round_index=prepared.round_index,
                parent_version_id=prepared.parent_version_id,
                input_snapshot_id=prepared.input_snapshot_id,
                research_package_id=prepared.research_id,
                draft={key: payload[key] for key in ListingDraft.model_fields},
                assets=[],
                operation_key=operation_key,
            )
        else:
            version = await session.get(DeliverableVersion, prepared.version_id)
            if version is None:
                raise ProductionError("The reviewed immutable version is unavailable.")
            version.text_passed, version.full_passed = result.text_passed, result.passed
            version.status = (
                "review_passed"
                if result.passed
                else "text_review_passed"
                if result.text_passed
                else "review_failed"
            )
            artifact = ReviewReport(
                order_id=order.id,
                command_id=prepared.command_id,
                version_id=version.id,
                result=payload,
                text_passed=result.text_passed,
                full_passed=result.passed,
                scope=result.scope,
                operation_key=operation_key,
            )
        session.add(artifact)
        await session.flush()
        receipt.artifact_id, receipt.status = artifact.id, "succeeded"
        if version is not None:
            receipt.version_id, receipt.version_number = version.id, version.version_number
        receipt.cost_usd, receipt.error_summary, receipt.finished_at = None, None, now()
        session.add(
            OrderEvent(
                order_id=order.id,
                kind=f"{prepared.role}_completed",
                visibility="internal",
                message="生产步骤已保存，仅内部可见；订单推进由工作流控制。",
                payload={"receipt_id": str(receipt.id), "artifact_id": str(artifact.id)},
            )
        )
        await session.commit()
        return receipt_result(receipt, cache_hit=False)


async def execute_step(
    order_id: UUID,
    command_id: UUID,
    provider,
    *,
    role: str,
    step_key: str,
    database: Database | None = None,
    settings: Settings | None = None,
    round_index: int = 0,
    feedback: Any = None,
    version_id: UUID | None = None,
    image_paths: list[str | Path] | None = None,
    image_hash: str | None = None,
    commit_guard: CommitGuard | None = None,
) -> StepResult:
    provider = tracked_provider(provider)
    async with database_context(database, settings) as db:
        prepared = await prepare(
            db,
            order_id,
            command_id,
            role,
            step_key,
            round_index=round_index,
            feedback=feedback,
            version_id=version_id,
            image_hash=image_hash,
            provider=provider,
            commit_guard=commit_guard,
        )
        if isinstance(prepared, StepResult):
            return prepared
        result = None
        provider.last_call = None
        try:
            traced_provider = unwrapped_provider(provider)
            if hasattr(traced_provider, "trace_context"):
                traced_provider.trace_context = {
                    "order_id": str(order_id),
                    "command_id": str(command_id),
                    "role": role,
                    "step_key": step_key,
                }
            if role == "research":
                result = await research_product(prepared.context, provider)
            elif role == "generation":
                result = await generate_listing(prepared.context, provider)
            else:
                context = {**prepared.context, "image_paths": image_paths or []}
                result = await review_listing(context, provider)
                if (
                    digest(await asyncio.to_thread(image_dependencies, image_paths or []))
                    != image_hash
                ):
                    raise ProductionError(
                        "Image inputs changed during review; result was not applied."
                    )
            return await commit_result(db, prepared, result, commit_guard, provider.last_call)
        except Exception as error:
            call_usage = attach_call(error, provider.last_call)
            await record_failure(db, prepared, error, result, call_usage)
            raise


async def research_step(order_id: UUID, command_id: UUID, provider, **kwargs) -> StepResult:
    return await execute_step(
        order_id, command_id, provider, role="research", step_key="research", **kwargs
    )


async def generation_step(
    order_id: UUID,
    command_id: UUID,
    round_index: int,
    provider,
    feedback=None,
    **kwargs,
) -> StepResult:
    if not isinstance(round_index, int) or round_index < 0:
        raise ValueError("Generation round_index must be a non-negative integer.")
    return await execute_step(
        order_id,
        command_id,
        provider,
        role="generation",
        step_key=f"generation:{round_index}",
        round_index=round_index,
        feedback=feedback,
        **kwargs,
    )


def image_dependencies(paths: list[str | Path]) -> list[tuple[str, str]]:
    return [
        (str(Path(path).resolve()), hashlib.sha256(Path(path).read_bytes()).hexdigest())
        for path in paths
    ]


async def review_step(
    order_id: UUID,
    command_id: UUID,
    version_id: UUID,
    provider,
    image_paths=None,
    **kwargs,
) -> StepResult:
    # Hash actual bytes, not only filenames: a changed image must not reuse an old report.
    images = await asyncio.to_thread(image_dependencies, image_paths or [])
    image_hash = digest(images)
    return await execute_step(
        order_id,
        command_id,
        provider,
        role="review",
        step_key=f"review:{version_id}:{image_hash[:20]}",
        version_id=version_id,
        image_paths=image_paths,
        image_hash=image_hash,
        **kwargs,
    )
