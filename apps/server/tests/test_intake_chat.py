import json
from io import BytesIO
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from company.agents.intake_chat import IntakeDraft, IntakeFields
from company.agents.product_export import MAX_EXPORT_BYTES
from company.config import Settings
from company.main import create_app
from company.providers.mimo import ProviderError
from company.services.access import ACCOUNT_COOKIE, csrf_token


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={"session_secret": SecretStr("intake-tests-session-secret-32-characters")}
    )


def excel_export() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as book:
        book.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row><c r="A1" t="inlineStr"><is><t>标题</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>价格</t></is></c></row>'
            '<row><c r="A2" t="inlineStr"><is><t>理线夹</t></is></c>'
            '<c r="B2"><v>19.9</v></c></row></sheetData></worksheet>',
        )
    return buffer.getvalue()


def test_intake_accepts_file_only_and_rejects_invalid_exports(monkeypatch, settings: Settings):
    collect = AsyncMock(
        return_value=IntakeDraft(reply="已整理", updates=IntakeFields(product_name="理线夹"))
    )
    monkeypatch.setattr("company.api.orders.collect_intake", collect)
    monkeypatch.setattr(
        "company.api.orders.get_session",
        AsyncMock(return_value=SimpleNamespace(account_id=uuid4())),
    )
    payload = {"payload": json.dumps({"fields": {}})}
    with TestClient(create_app(settings)) as client:
        client.cookies.set(ACCOUNT_COOKIE, "test-account-session")
        client.headers["X-CSRF-Token"] = csrf_token(settings, "test-account-session")
        for filename, data in (
            ("商品.csv", "标题,价格\n理线夹,19.9".encode("gb18030")),
            ("商品.xlsx", excel_export()),
        ):
            response = client.post(
                "/api/v1/orders/intake-chat", data=payload, files={"file": (filename, data)}
            )
            assert response.status_code == 200
            assert response.json()["updates"]["product_name"] == "理线夹"
            assert collect.await_args.args[2] == "标题,价格\r\n理线夹,19.9\r\n"
        for filename, data in (("商品.xlsx", b"not an excel file"), ("商品.csv", b"")):
            response = client.post(
                "/api/v1/orders/intake-chat", data=payload, files={"file": (filename, data)}
            )
            assert response.status_code == 422
    assert collect.await_count == 2


def test_intake_requires_account_and_csrf_before_model_call(monkeypatch, settings):
    collect = AsyncMock()
    monkeypatch.setattr("company.api.orders.collect_intake", collect)
    with TestClient(create_app(settings)) as client:
        payload = {"payload": json.dumps({"message": "商品资料", "fields": {}})}
        assert client.post("/api/v1/orders/intake-chat", data=payload).status_code == 401
        session = AsyncMock(return_value=SimpleNamespace(account_id=uuid4()))
        monkeypatch.setattr("company.api.orders.get_session", session)
        client.cookies.set(ACCOUNT_COOKIE, "test-account-session")
        assert client.post("/api/v1/orders/intake-chat", data=payload).status_code == 403
        session.return_value = SimpleNamespace(account_id=None)
        client.headers["X-CSRF-Token"] = csrf_token(settings, "test-account-session")
        assert client.post("/api/v1/orders/intake-chat", data=payload).status_code == 401
    collect.assert_not_awaited()


def test_intake_rejects_legacy_identity_and_unsafe_urls():
    for fields in (
        {"seller_name": "另一卖家"},
        {"material": "硅胶"},
        {"domestic_product_url": "javascript:alert(1)"},
        {"domestic_product_url": "https://name:password@example.com/item"},
    ):
        with pytest.raises(ValidationError):
            IntakeFields.model_validate(fields)
    fields = IntakeFields(product_details="黑色硅胶，尺寸未知")
    assert fields.product_details == "黑色硅胶，尺寸未知"


def test_intake_bounds_requests_and_reports_provider_failure(monkeypatch, settings):
    collect = AsyncMock(side_effect=ProviderError("private upstream diagnostics"))
    monkeypatch.setattr("company.api.orders.collect_intake", collect)
    monkeypatch.setattr(
        "company.api.orders.get_session",
        AsyncMock(return_value=SimpleNamespace(account_id=uuid4())),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.cookies.set(ACCOUNT_COOKIE, "test-account-session")
        client.headers["X-CSRF-Token"] = csrf_token(settings, "test-account-session")
        endpoint = "/api/v1/orders/intake-chat"
        assert client.post(endpoint, data={"payload": '{"fields":{}}'}).status_code == 422
        payload = {"payload": json.dumps({"fields": {}, "message": "商品资料"})}
        response = client.post(endpoint, data=payload)
        assert response.status_code == 503
        assert "private upstream" not in response.text
        assert client.post(
            endpoint, data=payload, files={"file": ("large.csv", b"x" * (MAX_EXPORT_BYTES + 1))}
        ).status_code == 413
        app.state.intake_chat_attempts.extend([monotonic()] * 20)
        assert client.post(endpoint, data=payload).status_code == 429
    assert collect.await_count == 1
