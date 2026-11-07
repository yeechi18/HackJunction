"""M7 scope, human handoff, explicit active time and controlled retries on real PostgreSQL."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_delivery import delivery_context as delivery_context
from test_orders_integration import context as context
from test_publication import PlanProvider, confirm
from test_publication import prepare as prepare_publication
from test_workflow import OfflineProvider

from company.api.support import router
from company.persistence.agent_runs import AgentRun
from company.persistence.models import Command, InputSnapshot, Job, Order, OrderEvent, now
from company.persistence.production import ProductionStepReceipt
from company.persistence.support import RetryPermission, SupportTicket
from company.services.publication import publish_confirmed
from company.services.support import ai_reply_paused
from company.services.workflow import process_one_job

pytestmark = pytest.mark.integration


def mount(app):
    if "/api/v1/internal/support-tickets" not in app.openapi()["paths"]:
        app.include_router(router, prefix="/api/v1")
        app.openapi_schema = None


async def seller(context, *, facts=None):
    _, app, clients, create, _ = context
    mount(app)
    customer = await clients()
    order = await create(customer, facts)
    exchanged = await customer.post(
        "/api/v1/order-access/exchange",
        json={"order_id": order["id"], "token": order["private_url"].split("#token=", 1)[1]},
    )
    assert exchanged.status_code == 200
    return customer, order, exchanged.json()["csrf_token"]


async def mark_failed(app, order_id):
    async with app.state.database.sessions() as session, session.begin():
        identifier = UUID(str(order_id))
        command = await session.scalar(
            select(Command).where(Command.order_id == identifier, Command.kind == "START")
        )
        job = await session.scalar(select(Job).where(Job.command_id == command.id))
        command.status, job.status, job.attempts, job.lease_generation = "failed", "failed", 3, 4
        return command.id, job.id


async def test_support_scope_csrf_reuse_human_reply_and_return_to_intake(context):
    _, app, _, _, login_owner = context
    customer, order, csrf = await seller(context)
    other, _, other_csrf = await seller(context)
    owner, owner_csrf = await login_owner()
    path = f"/api/v1/orders/{order['id']}/support-requests"
    body = {"content": "请人工核实商品尺寸。"}
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "request-human"}
    assert (await customer.post(path, json=body)).status_code == 403
    assert (
        await other.post(path, json=body, headers={"X-CSRF-Token": other_csrf})
    ).status_code == 404
    response = await customer.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    ticket = response.json()
    replay = await customer.post(path, json=body, headers=headers)
    assert replay.json()["id"] == ticket["id"] and len(replay.json()["messages"]) == 1
    assert (
        await customer.post(path, json={"content": "different"}, headers=headers)
    ).status_code == 409
    assert (await other.get(path)).status_code == 404
    assert (await customer.get("/api/v1/internal/support-tickets")).status_code == 401
    assert (await customer.get(path)).json()["ai_reply_paused"] is True
    assert (await customer.get(f"/api/v1/orders/{order['id']}")).json()["ai_reply_paused"] is True
    async with app.state.database.sessions() as session, session.begin():
        assert await ai_reply_paused(session, UUID(order["id"]))
        record = await session.get(Order, UUID(order["id"]))
        record.phase = "NEEDS_INFO"
        # The existing paused intake wakeup has completed without generating a reply.
        for command in await session.scalars(select(Command).where(Command.order_id == record.id)):
            command.status = "applied"
        for job in await session.scalars(select(Job).where(Job.order_id == record.id)):
            job.status = "done"
    reply_path = f"/api/v1/internal/support-tickets/{ticket['id']}/messages"
    answer = {"content": "负责人已核实，将继续资料审查。", "resolve": True}
    assert (await owner.post(reply_path, json=answer)).status_code == 403
    assert (await customer.post(reply_path, json=answer, headers=headers)).status_code == 401
    answer_headers = {"X-CSRF-Token": owner_csrf, "Idempotency-Key": "close-case"}
    closed = await owner.post(reply_path, json=answer, headers=answer_headers)
    assert closed.status_code == 200, closed.text
    assert (await customer.get(f"/api/v1/orders/{order['id']}")).json()["ai_reply_paused"] is False
    assert (
        closed.json()["status"] == "resolved" and closed.json()["messages"][-1]["role"] == "human"
    )
    assert (await owner.post(reply_path, json=answer, headers=answer_headers)).json()[
        "id"
    ] == ticket["id"]
    async with app.state.database.sessions() as session:
        assert not await ai_reply_paused(session, UUID(order["id"]))
        record = await session.get(Order, UUID(order["id"]))
        assert record.phase == "NEEDS_INFO" and record.revision == 1
        resumes = list(
            await session.scalars(
                select(Command).where(Command.order_id == record.id, Command.kind == "MESSAGE")
            )
        )
        assert len(resumes) == 1 and "人工客服已结束" in resumes[0].payload["content"]
        assert record.pending_command_id == resumes[0].id
    public_messages = (await customer.get(f"/api/v1/orders/{order['id']}/messages")).json()["items"]
    assert any(
        message["role"] == "human" and message["content"] == answer["content"]
        for message in public_messages
    )


async def test_support_does_not_stop_or_restart_production(context):
    _, app, _, _, login_owner = context
    customer, order, csrf = await seller(context)
    async with app.state.database.sessions() as session, session.begin():
        (await session.get(Order, UUID(order["id"]))).phase = "RESEARCH"
    response = await customer.post(
        f"/api/v1/orders/{order['id']}/support-requests", json={}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201
    async with app.state.database.sessions() as session:
        assert await ai_reply_paused(session, UUID(order["id"]))
        assert (await session.get(Order, UUID(order["id"]))).phase == "RESEARCH"
    owner, token = await login_owner()
    closed = await owner.post(
        f"/api/v1/internal/support-tickets/{response.json()['id']}/messages",
        json={"content": "人工咨询已处理。", "resolve": True},
        headers={"X-CSRF-Token": token},
    )
    assert closed.status_code == 200
    async with app.state.database.sessions() as session:
        assert (await session.get(Order, UUID(order["id"]))).phase == "RESEARCH"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Command)
                .where(Command.order_id == UUID(order["id"]), Command.kind == "MESSAGE")
            )
            == 0
        )


async def test_active_time_excludes_waiting_and_metrics_count_only_recorded_usage(context):
    _, app, _, _, login_owner = context
    customer, order, csrf = await seller(context)
    owner, owner_csrf = await login_owner()
    request = await customer.post(
        f"/api/v1/orders/{order['id']}/support-requests", json={}, headers={"X-CSRF-Token": csrf}
    )
    async with app.state.database.sessions() as session, session.begin():
        (await session.get(SupportTicket, request.json()["id"])).created_at = now() - timedelta(
            days=2
        )
        snapshot = await session.scalar(
            select(InputSnapshot).where(InputSnapshot.order_id == UUID(order["id"]))
        )
        command = await session.scalar(select(Command).where(Command.order_id == UUID(order["id"])))
        session.add(
            AgentRun(
                order_id=UUID(order["id"]),
                command_id=command.id,
                input_snapshot_id=snapshot.id,
                input_revision=1,
                role="acquisition",
                status="succeeded",
                result={},
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                model="recorded-intake-stub",
                elapsed_seconds=0.1,
            )
        )
        session.add(
            ProductionStepReceipt(
                order_id=UUID(order["id"]),
                command_id=command.id,
                step_key="generation:0:recorded",
                role="generation",
                input_snapshot_id=snapshot.id,
                input_revision=1,
                dependency_hash="e" * 64,
                status="stale",
                result={},
                usage={"input_tokens": 20, "output_tokens": 7, "total_tokens": 27},
                model="recorded-generation-stub",
                elapsed_seconds=0.2,
                cost_usd=Decimal("0.002"),
            )
        )
    base = f"/api/v1/internal/orders/{order['id']}"
    assert (await customer.get(base + "/metrics")).status_code == 401
    before = (await owner.get(base + "/metrics")).json()
    assert before["owner_active_seconds"] == 0
    entry_headers = {"X-CSRF-Token": owner_csrf, "Idempotency-Key": "active-30s"}
    assert (
        await owner.post(base + "/time-entries", json={"seconds": 30, "reason": "人工核对尺寸"})
    ).status_code == 403
    first = await owner.post(
        base + "/time-entries",
        json={"seconds": 30, "reason": "人工核对尺寸"},
        headers=entry_headers,
    )
    repeated = await owner.post(
        base + "/time-entries",
        json={"seconds": 30, "reason": "人工核对尺寸"},
        headers=entry_headers,
    )
    assert first.status_code == 201 and repeated.json()["id"] == first.json()["id"]
    await owner.post(
        base + "/time-entries",
        json={"seconds": 15, "reason": "回复客服"},
        headers={**entry_headers, "Idempotency-Key": "active-15s"},
    )
    metrics = (await owner.get(base + "/metrics")).json()
    assert metrics["owner_active_seconds"] == 45  # Not two days of elapsed ticket waiting.
    assert metrics["is_demo"] is True and metrics["recorded_model_calls"] == 2
    assert metrics["tokens"] == {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}
    assert (
        Decimal(metrics["known_cost_usd"]) == Decimal("0.002") and metrics["total_cost_usd"] is None
    )


async def test_metrics_include_actual_publishing_plan_usage_without_counting_artifact_estimates(
    delivery_context,
):
    prepared = await prepare_publication(delivery_context)
    settings, app, item, _, _, _ = prepared
    mount(app)
    queued = await confirm(prepared)
    await publish_confirmed(
        settings,
        item.order_id,
        queued["command_id"],
        database=app.state.database,
        provider=PlanProvider(),
    )
    owner = await delivery_context[3](owner=True)
    response = await owner.get(f"/api/v1/internal/orders/{item.order_id}/metrics")
    assert response.status_code == 200, response.text
    metrics = response.json()
    assert metrics["calls_by_role"] == {"publishing": 1}
    assert metrics["tokens"]["total_tokens"] == 22
    assert metrics["total_cost_usd"] is None


async def test_controlled_retry_is_queued_idempotent_and_grants_one_scheduler_attempt(context):
    settings, app, _, _, login_owner = context
    customer, order, _ = await seller(
        context,
        facts=[
            {"key": "material", "value": "silicone", "confirmed": True},
            {"key": "pack_quantity", "value": "6", "confirmed": True},
            {"key": "dimensions", "value": "25 x 25 x 15", "unit": "mm", "confirmed": True},
            {"key": "use_cases", "value": "indoor desk cables", "confirmed": True},
        ],
    )
    command_id, job_id = await mark_failed(app, order["id"])
    owner, csrf = await login_owner()
    body = {"command_id": str(command_id), "expected_revision": 1}
    path = f"/api/v1/internal/orders/{order['id']}/retries"
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "retry-technical"}
    assert (
        await owner.post(path, json=body, headers={"Idempotency-Key": "no-csrf"})
    ).status_code == 403
    assert (await customer.post(path, json=body, headers=headers)).status_code == 401
    result = await owner.post(path, json=body, headers=headers)
    assert result.status_code == 202, result.text
    repeat = await owner.post(path, json=body, headers=headers)
    assert repeat.status_code == 202 and repeat.json()["retry_id"] == result.json()["retry_id"]
    async with app.state.database.sessions() as session:
        job = await session.get(Job, job_id)
        assert job.status == "queued" and job.attempts == 2 and job.lease_generation == 5
        assert (await session.get(Command, command_id)).status == "queued"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RetryPermission)
                .where(RetryPermission.order_id == UUID(order["id"]))
            )
            == 1
        )
    provider = OfflineProvider()
    assert await process_one_job(
        settings, provider=provider, order_id=UUID(order["id"]), include_production=False
    )
    assert provider.calls == 1
    async with app.state.database.sessions() as session:
        assert (await session.get(Job, job_id)).attempts == 3
        assert (await session.get(Command, command_id)).status == "applied"
    assert (
        await owner.post(
            path, json=body, headers={**headers, "Idempotency-Key": "cannot-repeat-success"}
        )
    ).status_code == 409


@pytest.mark.parametrize(
    "invalid", ["stale", "succeeded", "revision", "quality_budget", "pending", "completed"]
)
async def test_retry_rejects_stale_success_budget_or_other_pending_work(context, invalid):
    _, app, _, _, login_owner = context
    _, order, _ = await seller(context)
    command_id, job_id = await mark_failed(app, order["id"])
    async with app.state.database.sessions() as session, session.begin():
        command = await session.get(Command, command_id)
        record = await session.get(Order, UUID(order["id"]))
        if invalid == "stale":
            command.status = "stale"
        elif invalid == "succeeded":
            command.status = "applied"
        elif invalid == "revision":
            record.revision = 2
        elif invalid == "quality_budget":
            session.add(
                OrderEvent(
                    order_id=record.id,
                    kind="production_revision_budget_exhausted",
                    visibility="internal",
                    message="Fixture: quality candidates exhausted.",
                    payload={"command_id": str(command_id), "candidates": 3},
                )
            )
        elif invalid == "pending":
            record.pending_command_id = uuid4()
        else:
            record.phase = "COMPLETED"
    owner, csrf = await login_owner()
    response = await owner.post(
        f"/api/v1/internal/orders/{order['id']}/retries",
        json={"command_id": str(command_id), "expected_revision": 1},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"reject-{invalid}"},
    )
    assert response.status_code == 409
    async with app.state.database.sessions() as session:
        assert (await session.get(Job, job_id)).status == "failed"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RetryPermission)
                .where(RetryPermission.order_id == UUID(order["id"]))
            )
            == 0
        )


async def test_retry_cannot_target_command_of_another_order(context):
    _, app, _, _, login_owner = context
    _, first, _ = await seller(context)
    _, second, _ = await seller(context)
    command_id, _ = await mark_failed(app, first["id"])
    owner, csrf = await login_owner()
    response = await owner.post(
        f"/api/v1/internal/orders/{second['id']}/retries",
        json={"command_id": str(command_id), "expected_revision": 1},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "cross-order"},
    )
    assert response.status_code == 404
