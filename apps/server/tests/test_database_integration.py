import pytest

from company.cli.init_db import initialize_database
from company.config import Settings
from company.persistence.database import Database


@pytest.mark.integration
async def test_initialization_is_repeatable_and_ready(postgres_test_settings: Settings):
    # No DROP/TRUNCATE/cleanup: the fixture also refuses the development database.
    await initialize_database(postgres_test_settings)
    await initialize_database(postgres_test_settings)
    database = Database(postgres_test_settings)
    try:
        readiness = await database.readiness()
    finally:
        await database.close()
    assert readiness.ready
    assert readiness.schema == "initialized"
