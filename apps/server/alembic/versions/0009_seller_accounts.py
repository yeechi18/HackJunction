"""Seller accounts and nullable ownership for existing private-link orders."""

import sqlalchemy as sa

from alembic import op

revision = "0009_seller_accounts"
down_revision = "0008_m7_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seller_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_seller_accounts_email"),
    )
    op.add_column("orders", sa.Column("seller_account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_orders_seller_account",
        "orders",
        "seller_accounts",
        ["seller_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_orders_seller_account_id", "orders", ["seller_account_id"])
    op.add_column("auth_sessions", sa.Column("account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_auth_sessions_account",
        "auth_sessions",
        "seller_accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_auth_sessions_account_id", "auth_sessions", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_account_id", table_name="auth_sessions")
    op.drop_constraint("fk_auth_sessions_account", "auth_sessions", type_="foreignkey")
    op.drop_column("auth_sessions", "account_id")
    op.drop_index("ix_orders_seller_account_id", table_name="orders")
    op.drop_constraint("fk_orders_seller_account", "orders", type_="foreignkey")
    op.drop_column("orders", "seller_account_id")
    op.drop_table("seller_accounts")
