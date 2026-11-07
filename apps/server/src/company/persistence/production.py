"""Immutable M4 artifacts and receipts, separate from M3 intake runs."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from company.persistence.models import Base, now


class Evidence(Base):
    __tablename__ = "production_evidence"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    provider: Mapped[str] = mapped_column(String(64), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="success")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str] = mapped_column(String(1000))
    is_demo: Mapped[bool] = mapped_column(default=False)
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchPackage(Base):
    __tablename__ = "research_packages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"))
    input_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_input_snapshots.id", ondelete="CASCADE")
    )
    input_revision: Mapped[int] = mapped_column(Integer)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    evidence_snapshot: Mapped[list] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    operation_key: Mapped[str] = mapped_column(String(300), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeliverableVersion(Base):
    __tablename__ = "deliverable_versions"
    __table_args__ = (UniqueConstraint("order_id", "version_number"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer)
    round_index: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("deliverable_versions.id", ondelete="SET NULL")
    )
    input_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_input_snapshots.id", ondelete="CASCADE")
    )
    research_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_packages.id", ondelete="CASCADE")
    )
    draft: Mapped[dict] = mapped_column(JSONB)
    assets: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    text_passed: Mapped[bool] = mapped_column(default=False)
    full_passed: Mapped[bool] = mapped_column(default=False)
    operation_key: Mapped[str] = mapped_column(String(300), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewReport(Base):
    __tablename__ = "review_reports"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliverable_versions.id", ondelete="CASCADE"), index=True
    )
    result: Mapped[dict] = mapped_column(JSONB)
    text_passed: Mapped[bool]
    full_passed: Mapped[bool]
    scope: Mapped[str] = mapped_column(String(32))
    operation_key: Mapped[str] = mapped_column(String(300), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProductionStepReceipt(Base):
    __tablename__ = "production_step_receipts"
    __table_args__ = (UniqueConstraint("order_id", "command_id", "step_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(250))
    role: Mapped[str] = mapped_column(String(32))
    input_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_input_snapshots.id", ondelete="CASCADE")
    )
    input_revision: Mapped[int] = mapped_column(Integer)
    dependency_hash: Mapped[str] = mapped_column(String(64))
    artifact_id: Mapped[UUID | None]
    version_id: Mapped[UUID | None]
    version_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="running")
    attempt_token: Mapped[UUID] = mapped_column(default=uuid4)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    model: Mapped[str | None] = mapped_column(String(200))
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
