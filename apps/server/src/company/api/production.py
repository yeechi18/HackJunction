"""Owner-only manual evidence and internal production artifacts."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from company.api.dependencies import DB, Owner, owner_write
from company.persistence.models import Command, Job, Order, OrderEvent, now
from company.persistence.production import (
    DeliverableVersion,
    Evidence,
    ProductionStepReceipt,
    ResearchPackage,
    ReviewReport,
)
from company.schemas.commands import QueuedCommand
from company.schemas.orders import InputModel
from company.services.production import accepted_revision, digest, evidence_view

router = APIRouter(prefix="/internal/orders", tags=["internal production"])


class EvidenceInput(InputModel):
    kind: str = Field(default="manual_observation", min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    provider: Literal["manual"] = "manual"
    status: Literal["success", "partial", "failed"] = "success"
    observed_at: datetime = Field(default_factory=now)
    scope: str = Field(default="Amazon US / this SKU", min_length=1, max_length=1000)
    is_demo: bool = False
    content: str = Field(min_length=1, max_length=20000)
    data: dict = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def require_time_zone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Observation time must include a timezone.")
        return value

    @field_validator("source_url")
    @classmethod
    def source_url_is_reference_only(cls, value: str | None) -> str | None:
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Use an HTTP(S) source URL; the API does not fetch it.")
        return value

    @field_validator("data")
    @classmethod
    def bounded_data(cls, value: dict) -> dict:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 20000:
            raise ValueError("Manual observation data exceeds the internal demo budget.")
        return value


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


class ProductionStartInput(InputModel):
    expected_revision: int = Field(ge=1)


async def require_order(database: DB, order_id: UUID, *, lock: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if lock:
        query = query.with_for_update()
    order = await database.scalar(query)
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


@router.post(
    "/{order_id}/production/start",
    response_model=QueuedCommand,
    status_code=202,
    dependencies=[Depends(owner_write)],
)
async def start_production(
    order_id: UUID,
    body: ProductionStartInput,
    database: DB,
    idempotency_key: str = Header(min_length=1, max_length=128),
) -> QueuedCommand:
    if idempotency_key.startswith("system:"):
        raise HTTPException(422, "Idempotency keys starting with system: are reserved.")
    order = await require_order(database, order_id, lock=True)
    request_hash = digest(body.model_dump())
    previous = await database.scalar(
        select(Command).where(
            Command.order_id == order_id,
            Command.idempotency_key == idempotency_key,
        )
    )
    if previous is not None:
        if previous.kind != "PRODUCE" or previous.request_hash != request_hash:
            raise HTTPException(409, "Idempotency key was already used for different input.")
        return QueuedCommand(
            command_id=previous.id, order_id=order_id, revision=previous.expected_revision
        )
    if (
        order.phase != "RESEARCH"
        or order.pending_command_id is not None
        or order.revision != body.expected_revision
        or not await accepted_revision(database, order)
    ):
        raise HTTPException(
            409, "Production requires a current accepted order in RESEARCH with no pending command."
        )
    command = Command(
        order_id=order_id,
        kind="PRODUCE",
        expected_revision=order.revision,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        payload=body.model_dump(),
    )
    database.add(command)
    await database.flush()
    order.pending_command_id = command.id
    database.add(Job(order_id=order_id, command_id=command.id, kind="RESUME"))
    database.add(
        OrderEvent(
            order_id=order_id,
            kind="production_queued",
            visibility="internal",
            message="生产命令已入队，尚未运行；图将按既定顺序执行研究与产出。",
        )
    )
    await database.commit()
    return QueuedCommand(command_id=command.id, order_id=order_id, revision=order.revision)


@router.post(
    "/{order_id}/evidence",
    response_model=EvidenceView,
    status_code=201,
    dependencies=[Depends(owner_write)],
)
async def create_evidence(order_id: UUID, body: EvidenceInput, database: DB) -> EvidenceView:
    order = await require_order(database, order_id, lock=True)
    if body.is_demo and not order.is_demo:
        raise HTTPException(422, "Demo evidence cannot be attached to a real order.")
    count = await database.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.order_id == order_id)
    )
    if count >= 100:
        raise HTTPException(409, "The manual evidence budget for this demo order is exhausted.")
    record = Evidence(order_id=order_id, **body.model_dump())
    database.add(record)
    database.add(
        OrderEvent(
            order_id=order_id,
            kind="manual_evidence_added",
            visibility="internal",
            message="人工观察已保存；不自动抓取来源或改写既有研究，后续研究需新命令。",
        )
    )
    await database.commit()
    return EvidenceView.model_validate(evidence_view(record))


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
