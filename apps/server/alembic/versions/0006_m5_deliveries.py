"""M5 immutable version-bound delivery snapshots and ZIP receipts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_m5_deliveries"
down_revision = "0005_m5_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("release_command_id", sa.Uuid(), nullable=False),
        sa.Column("input_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("package_key", sa.String(100), nullable=False),
        sa.Column("package_sha256", sa.String(64), nullable=False),
        sa.Column("package_byte_size", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["deliverable_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_command_id"], ["commands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["input_snapshot_id"], ["order_input_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "version_id"),
        sa.UniqueConstraint("package_key"),
    )
    op.create_index("ix_delivery_snapshots_order_id", "delivery_snapshots", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_delivery_snapshots_order_id", table_name="delivery_snapshots")
    op.drop_table("delivery_snapshots")
