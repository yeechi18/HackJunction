from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import literal, null, select, union_all

from company.api.dependencies import DB, Owner, owner_write
from company.persistence.agent_runs import AgentRun
from company.persistence.models import Order
from company.persistence.production import ProductionStepReceipt
from company.persistence.publication import PublishAttempt
from company.schemas.commands import DecisionInput, QueuedCommand
from company.schemas.orders import InternalOrder, InternalOrderList, OperationsRun
from company.services import orders

router = APIRouter(prefix="/internal/orders", tags=["internal orders"])


@router.get("", response_model=InternalOrderList)
async def list_orders(
    database: DB, owner: Owner, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> InternalOrderList:
    rows = (
        await database.scalars(select(Order).order_by(Order.created_at.desc()).limit(limit))
    ).all()
    return InternalOrderList(
        items=[await orders.summary(database, row, internal=True) for row in rows]
    )


TRACE_FIELDS = {
    "copy_preview": ("draft", "missing_fields", "issues"),
    "acquisition": ("summary", "ready_for_acceptance", "missing_fields", "questions", "conflicts"),
    "acquisition_feedback": ("summary", "category", "reason", "questions", "requires_research"),
    "research": (
        "market_notes",
        "buyer_questions",
        "missing_facts",
        "follow_up_questions",
        "pricing_notes",
        "suggested_listing_price_range",
    ),
    "generation": (
        "title",
        "bullets",
        "description",
        "faqs",
        "claims",
        "image_plan",
        "program_issues",
    ),
    "review": ("scope", "passed", "issues", "buyer_question_coverage", "requires_images"),
    "publishing": ("marketplace", "currency", "listing_price", "seller_sku", "version_id"),
}


@router.get("/agent-runs", response_model=list[OperationsRun])
async def list_agent_runs(
    database: DB,
    owner: Owner,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    include_trace: bool = False,
) -> list[OperationsRun]:
    def projection(table, role, usage, step, finished_at, result):
        return select(
            table.id,
            table.order_id,
            role.label("role"),
            table.status,
            table.model,
            usage.label("usage"),
            table.elapsed_seconds,
            table.error_summary,
            table.created_at,
            step.label("step"),
            finished_at.label("finished_at"),
            (result if include_trace else null()).label("result"),
        )

    runs = union_all(
        projection(
            AgentRun,
            AgentRun.role,
            AgentRun.usage,
            AgentRun.role,
            AgentRun.finished_at,
            AgentRun.result,
        ),
        projection(
            ProductionStepReceipt,
            ProductionStepReceipt.role,
            ProductionStepReceipt.usage,
            ProductionStepReceipt.step_key,
            ProductionStepReceipt.finished_at,
            ProductionStepReceipt.result,
        ),
        projection(
            PublishAttempt,
            literal("publishing"),
            PublishAttempt.plan_usage,
            literal("publication"),
            null(),
            PublishAttempt.plan,
        ),
    ).subquery()
    rows = await database.execute(
        select(runs).order_by(runs.c.created_at.desc(), runs.c.id).limit(limit)
    )
    records = []
    for row in rows.mappings():
        result = row["result"] or {}
        trace = {key: result[key] for key in TRACE_FIELDS.get(row["role"], ()) if key in result}
        records.append(OperationsRun.model_validate({**row, "result": trace or None}))
    return records


@router.get("/{order_id}", response_model=InternalOrder)
async def detail(order_id: UUID, database: DB, owner: Owner) -> InternalOrder:
    return await orders.internal_detail(database, await orders.load_order(database, order_id))


@router.post(
    "/{order_id}/decisions",
    response_model=QueuedCommand,
    status_code=202,
    dependencies=[Depends(owner_write)],
)
async def decide(
    order_id: UUID,
    body: DecisionInput,
    database: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> QueuedCommand:
    if idempotency_key.startswith("system:"):
        raise HTTPException(422, "Idempotency keys starting with system: are reserved.")
    return await orders.enqueue_decision(database, order_id, body, idempotency_key)
