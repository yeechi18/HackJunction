"""One immutable public snapshot and package receipt per delivered version."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from company.persistence.models import Base, now


class DeliverySnapshot(Base):
    __tablename__ = "delivery_snapshots"
    __table_args__ = (UniqueConstraint("order_id", "version_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliverable_versions.id", ondelete="CASCADE")
    )
    release_command_id: Mapped[UUID] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"))
    input_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_input_snapshots.id", ondelete="CASCADE")
    )
    input_revision: Mapped[int] = mapped_column(Integer)
    version_number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    package_key: Mapped[str] = mapped_column(String(100), unique=True)
    package_sha256: Mapped[str] = mapped_column(String(64))
    package_byte_size: Mapped[int] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
