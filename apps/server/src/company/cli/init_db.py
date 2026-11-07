"""Initialize the application migrations and LangGraph checkpoint schema."""

import asyncio

from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.engine import Connection

from alembic import command
from company.config import SERVER_ROOT, Settings, get_settings
from company.persistence.database import checkpoint_conninfo, create_engine
from company.persistence.schema import SCHEMA_VERSION
from company.runtime import configure_asyncio


def run_migrations(connection: Connection) -> None:
    configuration = Config(str(SERVER_ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(SERVER_ROOT / "alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, "head")


async def initialize_database(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(run_migrations)
        # setup() owns its idempotent migrations; do not wrap it in our transaction.
        checkpoint_manager = AsyncPostgresSaver.from_conn_string(checkpoint_conninfo(settings))
        async with checkpoint_manager as checkpointer:
            await checkpointer.setup()
        # Publish readiness only after BOTH application and checkpointer setup succeed.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO system_metadata (key,value) VALUES ('schema_version',:version) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"
                ),
                {"version": SCHEMA_VERSION},
            )
            await connection.execute(
                text(
                    "INSERT INTO system_metadata (key, value) "
                    "VALUES ('checkpointer_initialized', 'true') "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                )
            )
    finally:
        await engine.dispose()


def main() -> None:
    configure_asyncio()
    try:
        asyncio.run(initialize_database(get_settings()))
    except Exception:
        print("Database initialization failed. Check PostgreSQL and local configuration.")
        raise SystemExit(1) from None
    print("Application migration and LangGraph checkpoint setup completed. No orders were created.")


if __name__ == "__main__":
    main()
