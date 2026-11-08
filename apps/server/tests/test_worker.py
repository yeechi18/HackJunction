from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest

from company import worker


async def test_worker_recovers_database_disconnect_without_leaking_error(monkeypatch, capsys):
    database = SimpleNamespace(
        close=AsyncMock(),
        readiness=AsyncMock(
            return_value=SimpleNamespace(database="connected", schema="initialized")
        ),
    )
    monkeypatch.setattr(worker, "Database", lambda settings: database)
    process = AsyncMock(side_effect=[psycopg.OperationalError("secret-connection"), False])
    monkeypatch.setattr(worker, "process_one_job", process)
    monkeypatch.setattr(worker, "process_one_image_task", AsyncMock(return_value=False))

    class StopLoop(Exception):
        pass

    sleep = AsyncMock(side_effect=[None, StopLoop()])
    monkeypatch.setattr(worker.asyncio, "sleep", sleep)
    with pytest.raises(StopLoop):
        await worker.run_worker(SimpleNamespace(worker_heartbeat_seconds=2))
    assert process.await_count == 2
    assert sleep.call_args_list[0].args == (5,)
    output = capsys.readouterr().out
    assert "worker_database_retry" in output
    assert "worker_heartbeat" in output
    assert "secret-connection" not in output
    database.close.assert_awaited_once()


async def test_worker_once_reports_sanitized_database_failure(monkeypatch):
    database = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(worker, "Database", lambda settings: database)
    monkeypatch.setattr(
        worker,
        "process_one_job",
        AsyncMock(side_effect=psycopg.OperationalError("secret-connection")),
    )
    with pytest.raises(worker.WorkflowError, match="Database temporarily unavailable"):
        await worker.run_worker(SimpleNamespace(), once=True)
    database.close.assert_awaited_once()
