from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from company.config import Settings
from company.main import create_app
from company.persistence.database import Database, Readiness


def test_live_does_not_depend_on_database_or_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    readiness = AsyncMock(side_effect=AssertionError("live must not connect to PostgreSQL"))
    close = AsyncMock()
    monkeypatch.setattr(Database, "readiness", readiness)
    monkeypatch.setattr(Database, "close", close)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    readiness.assert_not_awaited()
    close.assert_awaited_once()


@pytest.mark.parametrize(
    ("check", "status_code", "status"),
    [
        (Readiness(True, "connected", "initialized", "Infrastructure is ready."), 200, "ready"),
        (
            Readiness(False, "connected", "not_initialized", "Run company-db-init."),
            503,
            "not_ready",
        ),
        (
            Readiness(False, "unavailable", "unknown", "Database is unavailable."),
            503,
            "not_ready",
        ),
    ],
)
def test_ready_reports_database_and_schema(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    check: Readiness,
    status_code: int,
    status: str,
):
    monkeypatch.setattr(Database, "readiness", AsyncMock(return_value=check))
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == status_code
    assert response.json()["status"] == status
    assert response.json()["schema"] == check.schema
    assert response.json()["business_workflow_implemented"] is True
    assert response.json()["real_amazon_implemented"] is False
    assert response.json()["publish_mode"] == "simulated"


async def test_database_connection_failure_is_sanitized(settings: Settings):
    database = Database(settings)
    original_engine = database.engine
    database.engine = MagicMock()
    database.engine.connect.side_effect = OperationalError(
        "SELECT private_data", {}, Exception("password=super-secret postgresql://private")
    )
    try:
        result = await database.readiness()
    finally:
        await original_engine.dispose()
    assert not result.ready
    assert result.database == "unavailable"
    assert "secret" not in result.message
    assert "postgresql" not in result.message
    assert "SELECT" not in result.message

