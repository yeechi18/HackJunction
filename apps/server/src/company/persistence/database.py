"""Async connection ownership and sanitized infrastructure readiness checks."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from company.config import Settings
from company.persistence.schema import ALEMBIC_REVISION, SCHEMA_VERSION


@dataclass(frozen=True)
class Readiness:
    ready: bool
    database: Literal["connected", "unavailable"]
    schema: Literal["initialized", "not_initialized", "unknown"]
    message: str


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_timeout=settings.database_connect_timeout_seconds,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        hide_parameters=True,
    )


def checkpoint_conninfo(settings: Settings) -> str:
    """Psycopg needs a PostgreSQL URI, not SQLAlchemy's driver-qualified URL."""
    url = make_url(settings.database_url.get_secret_value())
    query = dict(url.query)
    query.setdefault("connect_timeout", str(settings.database_connect_timeout_seconds))
    return url.set(drivername="postgresql", query=query).render_as_string(hide_password=False)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_engine(settings)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def readiness(self) -> Readiness:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                tables = await connection.execute(
                    text(
                        "SELECT to_regclass('system_metadata'), "
                        "to_regclass('checkpoints'), to_regclass('checkpoint_blobs'), "
                        "to_regclass('checkpoint_writes'), to_regclass('checkpoint_migrations'), "
                        "to_regclass('alembic_version')"
                    )
                )
                if not all(tables.one()):
                    return Readiness(False, "connected", "not_initialized", "Run company-db-init.")
                version = await connection.scalar(
                    text("SELECT value FROM system_metadata WHERE key = 'schema_version'")
                )
                migration_revision = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                checkpoint_initialized = await connection.scalar(
                    text("SELECT value FROM system_metadata WHERE key = 'checkpointer_initialized'")
                )
                if (
                    version != SCHEMA_VERSION
                    or migration_revision != ALEMBIC_REVISION
                    or checkpoint_initialized != "true"
                ):
                    return Readiness(False, "connected", "not_initialized", "Run company-db-init.")
        except (SQLAlchemyError, TimeoutError, OSError):
            # Never return driver exceptions, SQL, credentials, or connection URLs.
            return Readiness(False, "unavailable", "unknown", "Database is unavailable.")
        return Readiness(True, "connected", "initialized", "Infrastructure is ready.")

    async def close(self) -> None:
        await self.engine.dispose()

