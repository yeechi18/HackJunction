"""Immutable seller approvals, publication attempts and explicit simulation receipts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from company.persistence.models import Base, now


class SellerConfirmation(Base):
    __tablename__ = "seller_confirmations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(
        ForeignKey("commands.id", ondelete="CASCADE"), unique=True
    )
    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("delivery_snapshots.id", ondelete="CASCADE")
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliverable_versions.id", ondelete="CASCADE")
    )
    input_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_input_snapshots.id", ondelete="CASCADE")
    )
    input_revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    listing_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    seller_sku: Mapped[str] = mapped_column(String(100))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PublishAttempt(Base):
    __tablename__ = "publish_attempts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"))
    confirmation_id: Mapped[UUID] = mapped_column(
        ForeignKey("seller_confirmations.id", ondelete="CASCADE"), unique=True
    )
    operation_key: Mapped[str] = mapped_column(String(100), unique=True)
    mode: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="READY")
    plan: Mapped[dict | None] = mapped_column(JSONB)
    plan_usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    model: Mapped[str | None] = mapped_column(String(200))
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    submit_count: Mapped[int] = mapped_column(Integer, default=0)
    external_result: Mapped[dict | None] = mapped_column(JSONB)
    listing_url: Mapped[str | None] = mapped_column(String(2000))
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SimulatedListing(Base):
    __tablename__ = "simulated_listings"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    operation_key: Mapped[str] = mapped_column(String(100), unique=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FeedbackRequest(Base):
    __tablename__ = "feedback_requests"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(
        ForeignKey("commands.id", ondelete="CASCADE"), unique=True
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliverable_versions.id", ondelete="CASCADE")
    )
    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("delivery_snapshots.id", ondelete="CASCADE")
    )
    input_revision: Mapped[int] = mapped_column(Integer)
    resulting_revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    fact_updates: Mapped[list | None] = mapped_column(JSONB)
    classification: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_limit: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
