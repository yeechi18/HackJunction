"""Remove order demo mode, duplicate account identity and retired time tracking."""

import sqlalchemy as sa

from alembic import op

revision = "0012_intake_cleanup"
down_revision = "0011_product_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("orders", "is_demo")
    op.drop_column("orders", "seller_name")
    op.drop_column("orders", "contact")
    op.drop_column("delivery_snapshots", "is_demo")
    op.drop_table("active_time_entries")


def downgrade() -> None:
    op.add_column(
        "orders", sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "orders", sa.Column("seller_name", sa.String(200), nullable=False, server_default="")
    )
    op.add_column("orders", sa.Column("contact", sa.String(500), nullable=True))
    op.execute("""UPDATE orders SET seller_name = a.display_name, contact = a.email
                  FROM seller_accounts a WHERE a.id = orders.seller_account_id""")
    op.add_column(
        "delivery_snapshots",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "active_time_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", "idempotency_key"),
    )
    op.create_index("ix_active_time_entries_order_id", "active_time_entries", ["order_id"])
