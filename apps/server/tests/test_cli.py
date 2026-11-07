import json
from pathlib import Path

import pytest

from company.cli.export_openapi import export_contract
from company.cli.seed_demo import validate_fixtures


def test_export_contract_does_not_need_database(tmp_path: Path):
    output = export_contract(tmp_path / "openapi.json")
    document = json.loads(output.read_text(encoding="utf-8"))
    assert "/api/v1/health/live" in document["paths"]
    assert "/api/v1/health/ready" in document["paths"]
    assert "503" in document["paths"]["/api/v1/health/ready"]["get"]["responses"]


def test_seed_refuses_unlabelled_fixtures(tmp_path: Path):
    for name in ("product.json", "evidence.json"):
        (tmp_path / name).write_text('{"is_demo": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="is_demo=true"):
        validate_fixtures(tmp_path)


def test_seed_only_validates_labeled_demo_files(tmp_path: Path):
    for name in ("product.json", "evidence.json"):
        (tmp_path / name).write_text('{"is_demo": true}', encoding="utf-8")
    assert validate_fixtures(tmp_path) == 2

