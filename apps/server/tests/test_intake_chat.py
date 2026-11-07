import json
from io import BytesIO
from unittest.mock import AsyncMock
from zipfile import ZipFile

from fastapi.testclient import TestClient

from company.agents.intake_chat import IntakeDraft, IntakeFields
from company.config import Settings
from company.main import create_app


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
    payload = {"payload": json.dumps({"fields": {}})}
    with TestClient(create_app(settings)) as client:
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
