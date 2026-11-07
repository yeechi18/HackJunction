from pathlib import Path

import pytest
from pydantic import ValidationError

from company.config import REPO_ROOT, Settings
from company.persistence.database import checkpoint_conninfo


def test_environment_overrides_explicit_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "MIMO_MODEL=file-model\nMIMO_API_KEY=file-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("MIMO_MODEL", "environment-model")
    monkeypatch.setenv("MIMO_API_KEY", "environment-secret")
    configuration = Settings(_env_file=environment_file)
    assert configuration.mimo_model == "environment-model"
    assert configuration.mimo_api_key is not None
    assert configuration.mimo_api_key.get_secret_value() == "environment-secret"
    assert "environment-secret" not in repr(configuration)


def test_relative_assets_resolve_from_repository_not_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    configuration = Settings(_env_file=None, asset_dir="var/test-assets", mimo_api_key="")
    assert configuration.asset_dir == REPO_ROOT / "var" / "test-assets"
    assert configuration.mimo_api_key is None
    assert (REPO_ROOT / "docs" / "PROJECT_OVERVIEW.md").is_file()


def test_invalid_database_url_error_does_not_reveal_credentials():
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url="sqlite://private-secret")
    assert "private-secret" not in str(error.value)


def test_checkpoint_uri_preserves_credentials_without_sqlalchemy_driver(settings: Settings):
    uri = checkpoint_conninfo(settings)
    assert uri.startswith("postgresql://")
    assert "postgresql+psycopg" not in uri
    assert "connect_timeout=3" in uri


def test_fake_model_requires_explicit_test_environment():
    with pytest.raises(ValidationError, match="APP_ENV=test"):
        Settings(_env_file=None, app_env="production", mimo_mode="fake")

