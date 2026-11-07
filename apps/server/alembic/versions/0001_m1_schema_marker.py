"""Create the M1 infrastructure marker; no business/order data exists yet."""

import sqlalchemy as sa

from alembic import op

revision = "0001_m1_schema_marker"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    marker = op.create_table(
        "system_metadata",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False),
    )
    op.bulk_insert(marker, [{"key": "schema_version", "value": "m1"}])


def downgrade() -> None:
    op.drop_table("system_metadata")
