"""Offline defaults; database integration is explicit and never targets the dev DB."""

import asyncio

import pytest
from sqlalchemy.engine import make_url

from company.config import Settings


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-db-tests",
        action="store_true",
        default=False,
        help="Use the separate PostgreSQL test DB.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-db-tests"):
        return
    skip = pytest.mark.skip(reason="Use --run-db-tests to opt in to the independent test database.")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        mimo_api_key=None,
        database_url="postgresql+psycopg://offline:do-not-connect@127.0.0.1:1/offline_test",
    )


def pytest_asyncio_loop_factories(config, item):
    # Psycopg needs a selector loop on Windows; use the same factory in both OSes.
    return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture
def postgres_test_settings() -> Settings:
    configuration = Settings()
    development = make_url(configuration.database_url.get_secret_value())
    testing = make_url(configuration.test_database_url.get_secret_value())
    development_target = (development.host, development.port or 5432, development.database)
    testing_target = (testing.host, testing.port or 5432, testing.database)
    if development_target == testing_target:
        pytest.fail("Refusing database integration: test and development targets are identical.")
    if not testing.database or not testing.database.endswith("_test"):
        pytest.fail("Refusing database integration: use a separate database ending in _test.")
    return configuration.model_copy(
        update={
            "app_env": "test",
            "database_url": configuration.test_database_url,
            "mimo_api_key": None,
            "seedream_enabled": False,
            "seedream_auto_on_upload": False,
            "seedream_api_key": None,
        }
    )

