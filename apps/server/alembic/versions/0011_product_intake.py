"""Persist original product description and optional domestic reference URL."""

import sqlalchemy as sa

from alembic import op

revision = "0011_product_intake"
down_revision = "0010_registration_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("product_details", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("domestic_product_url", sa.String(2000), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "domestic_product_url")
    op.drop_column("orders", "product_details")
