"""Owner-only read access to evidence and agent production artifacts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from company.api.dependencies import DB, Owner
from company.persistence.models import Order
from company.persistence.production import (
    DeliverableVersion,
    Evidence,
    ProductionStepReceipt,
    ResearchPackage,
    ReviewReport,
)
from company.services.production import evidence_view

router = APIRouter(prefix="/internal/orders", tags=["internal production"])


class EvidenceView(BaseModel):
    id: UUID
    kind: str
    source: str
    source_url: str | None
    provider: str
    status: str
    observed_at: datetime
    scope: str
    is_demo: bool
    content: str
    data: dict
    created_at: datetime


class EvidenceList(BaseModel):
    items: list[EvidenceView]


class ResearchView(BaseModel):
    id: UUID
    input_snapshot_id: UUID
    input_revision: int
    evidence_snapshot: list
    result: dict
    created_at: datetime


class VersionView(BaseModel):
    id: UUID
    version_number: int
    parent_version_id: UUID | None
    input_snapshot_id: UUID
    research_package_id: UUID
    draft: dict
    assets: list
    status: str
    text_passed: bool
    full_passed: bool
    created_at: datetime


class ReportView(BaseModel):
    id: UUID
    version_id: UUID
    result: dict
    text_passed: bool
    full_passed: bool
    scope: str
    created_at: datetime


class ReceiptView(BaseModel):
    id: UUID
    command_id: UUID
    role: str
    step_key: str
    input_revision: int
    artifact_id: UUID | None
    status: str
    attempts: int
    usage: dict
    model: str | None
    elapsed_seconds: float | None
    cost_usd: Decimal | None
    error_summary: str | None
    created_at: datetime
    finished_at: datetime | None


class ProductionView(BaseModel):
    research: list[ResearchView]
    versions: list[VersionView]
    reviews: list[ReportView]
    runs: list[ReceiptView]


async def require_order(database: DB, order_id: UUID, *, lock: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if lock:
        query = query.with_for_update()
    order = await database.scalar(query)
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


@router.get("/{order_id}/evidence", response_model=EvidenceList)
async def get_evidence(order_id: UUID, database: DB, owner: Owner) -> EvidenceList:
    await require_order(database, order_id)
    records = await database.scalars(
        select(Evidence).where(Evidence.order_id == order_id).order_by(Evidence.created_at)
    )
    return EvidenceList(
        items=[EvidenceView.model_validate(evidence_view(record)) for record in records]
    )


@router.get("/{order_id}/production", response_model=ProductionView)
async def get_production(order_id: UUID, database: DB, owner: Owner) -> ProductionView:
    await require_order(database, order_id)

    async def records(model):
        return list(
            await database.scalars(
                select(model).where(model.order_id == order_id).order_by(model.created_at.desc())
            )
        )

    research = await records(ResearchPackage)
    versions = await records(DeliverableVersion)
    reviews = await records(ReviewReport)
    runs = await records(ProductionStepReceipt)
    return ProductionView(
        research=[ResearchView.model_validate(row, from_attributes=True) for row in research],
        versions=[VersionView.model_validate(row, from_attributes=True) for row in versions],
        reviews=[ReportView.model_validate(row, from_attributes=True) for row in reviews],
        runs=[ReceiptView.model_validate(row, from_attributes=True) for row in runs],
    )


@router.get("/{order_id}/release-review")
async def get_release_review(order_id: UUID, version_id: UUID, database: DB, owner: Owner):
    from company.services.owner_release import release_review

    await require_order(database, order_id)
    result = await release_review(database, order_id, version_id)
    order = await require_order(database, order_id)
    if order.pending_command_id:
        result["blockers"].append("当前任务仍在处理，请等任务结束后确认放行。")
    return result
