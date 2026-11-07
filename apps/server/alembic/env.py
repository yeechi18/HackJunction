"""Async Alembic environment with explicit, externally owned connection support."""

import asyncio

from sqlalchemy.engine import Connection

from alembic import context
from company.config import get_settings
from company.persistence import (  # noqa: F401 -- register every model for autogeneration
    agent_runs,
    assets,
    deliveries,
    models,
    production,
    publication,
    support,
)
from company.persistence.database import create_engine
from company.persistence.schema import metadata
from company.runtime import configure_asyncio

configuration = context.config
target_metadata = metadata


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif configuration.attributes.get("connection") is not None:
    run_migrations(configuration.attributes["connection"])
else:
    configure_asyncio()
    asyncio.run(run_async_migrations())

