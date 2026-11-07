"""Single-use registration email codes, never stored in plaintext."""

import sqlalchemy as sa

from alembic import op

revision = "0010_registration_email"
down_revision = "0009_seller_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_verifications",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_verifications_expires_at", "email_verifications", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_email_verifications_expires_at", table_name="email_verifications")
    op.drop_table("email_verifications")
