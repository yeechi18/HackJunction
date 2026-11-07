"""M3 durable agent execution receipts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_m3_agent_runs"
down_revision = "0002_m2_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("input_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["input_snapshot_id"], ["order_input_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", "role"),
    )
    op.create_index("ix_agent_runs_order_id", "agent_runs", ["order_id"])
    op.execute("UPDATE system_metadata SET value = 'm3' WHERE key = 'schema_version'")


def downgrade() -> None:
    op.execute("UPDATE system_metadata SET value = 'm2' WHERE key = 'schema_version'")
    op.drop_index("ix_agent_runs_order_id", table_name="agent_runs")
    op.drop_table("agent_runs")
