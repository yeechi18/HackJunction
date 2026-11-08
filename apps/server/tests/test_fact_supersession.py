from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_assets import asset_context as _asset_context

from company.domain.current_facts import resolve_current_facts
from company.persistence.models import Command, InputSnapshot, Job, Order
from company.persistence.production import ProductionStepReceipt
from company.services.model_budget import OrderBudgetExceeded, check_budget
from company.services.orders import summary

asset_context = _asset_context


def test_new_confirmed_volume_supersedes_unknowns_but_keeps_real_sources_and_numeric_conflicts():
    context = {
        "product_details": "单瓶净含量未知，包装材质未确认。另一具体报价说明是570 mL。",
        "initial_analysis": {"summary": "容量未知", "source_quote": "单瓶净含量未知"},
        "evidence": [{"id": "source", "content": "卖家尚未确认净含量"}],
        "confirmed_facts": [
            {
                "id": "fact:net_volume",
                "key": "net_volume",
                "value": "100",
                "unit": "mL",
                "provenance": "seller_confirmed",
            }
        ],
    }
    original = deepcopy(context)
    result = resolve_current_facts(context)
    assert context == original
    assert "100 mL" in result["product_details"] and "净含量未知" not in result["product_details"]
    assert "包装材质未确认" in result["product_details"] and "570 mL" in result["product_details"]
    assert result["evidence"] == original["evidence"]
    assert (
        result["initial_analysis"]["source_quote"] == original["initial_analysis"]["source_quote"]
    )
    assert result["fact_precedence"]["current_net_volume"] == "100 mL"
    context["confirmed_facts"][0]["provenance"] = "seller_provided"
    assert "fact_precedence" not in resolve_current_facts(context)


async def add_failed_usage(app, oid, tokens=100):
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        order.phase = "GENERATION"
        command = await session.scalar(select(Command).where(Command.order_id == oid))
        command.status = "failed"
        job = await session.scalar(select(Job).where(Job.command_id == command.id))
        job.status = "failed"
        snapshot = await session.scalar(select(InputSnapshot).where(InputSnapshot.order_id == oid))
        receipt = ProductionStepReceipt(
            order_id=oid,
            command_id=command.id,
            step_key="generation:test",
            role="generation",
            input_snapshot_id=snapshot.id,
            input_revision=1,
            dependency_hash="0" * 64,
            attempt_token=uuid4(),
            status="failed",
            usage={"total_tokens": tokens},
            error_summary="本单 token 用量达到预算，请负责人检查。",
        )
        session.add(receipt)
        await session.commit()


@pytest.mark.integration
async def test_failed_production_reports_error_without_stale_revision_failures(
    asset_context,
):
    settings, app, client, new_order = asset_context
    oid = await new_order()
    await add_failed_usage(app, oid)
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        state = await summary(session, order, internal=True)
        assert state.workflow_status == "failed" and "预算" in state.workflow_error
        order.revision = 2
        session.add(InputSnapshot(order_id=oid, revision=2, facts=[], source="test", notes=None))
        await session.commit()
        state = await summary(session, order, internal=True)
        assert state.workflow_status != "failed" and state.workflow_error is None


@pytest.mark.integration
async def test_budget_grant_is_owner_only_bounded_idempotent_and_does_not_resume_job(asset_context):
    settings, app, client, new_order = asset_context
    app.state.settings = settings.model_copy(update={"order_token_budget": 5})
    oid = await new_order()
    await add_failed_usage(app, oid)
    owner, csrf = await client(owner=True)
    seller, seller_csrf = await client(oid)
    path = f"/api/v1/internal/orders/{oid}/model-budget"
    body = {
        "expected_revision": 1,
        "request_id": str(uuid4()),
        "reason": "已核对中断问题，仅追加本单有限处理上限。",
    }
    assert (
        await seller.post(path, json=body, headers={"x-csrf-token": seller_csrf})
    ).status_code == 401
    assert (await owner.post(path, json=body)).status_code == 403
    with pytest.raises(OrderBudgetExceeded):
        await check_budget(app.state.database, app.state.settings, oid)
    response = await owner.post(path, json=body, headers={"x-csrf-token": csrf})
    assert response.status_code == 200 and response.json()["granted_tokens"] == 20000
    assert (await owner.post(path, json=body, headers={"x-csrf-token": csrf})).json()[
        "granted_tokens"
    ] == 20000
    assert (
        await owner.post(
            path, json={**body, "request_id": str(uuid4())}, headers={"x-csrf-token": csrf}
        )
    ).status_code == 409
    assert (
        await owner.post(
            path, json={**body, "additional_tokens": 999999}, headers={"x-csrf-token": csrf}
        )
    ).status_code == 422
    await check_budget(app.state.database, app.state.settings, oid)
    async with app.state.database.sessions() as session:
        job = await session.scalar(select(Job).where(Job.order_id == oid))
        assert job.status == "failed"
