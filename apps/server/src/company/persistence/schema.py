"""Expected application schema and migration revision for readiness checks."""

from sqlalchemy import Column, MetaData, String, Table

SCHEMA_VERSION = "product_intake_v1"
ALEMBIC_REVISION = "0011_product_intake"
metadata = MetaData()
system_metadata = Table(
    "system_metadata",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", String(255), nullable=False),
)
