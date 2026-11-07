from contextlib import AsyncExitStack
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from pwdlib import PasswordHash
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from company.cli.init_db import initialize_database
from company.config import Settings
from company.main import create_app
from company.persistence.models import (
    AuthSession,
    Command,
    HumanDecision,
    InputSnapshot,
    Job,
    Order,
    OrderAccess,
    now,
)
from company.schemas.orders import CreateOrder
from company.services.access import token_hash
from company.services.orders import create_order

pytestmark = pytest.mark.integration
OWNER_PASSWORD = "test-only-owner-password-not-used-outside-this-test"


@pytest.fixture
async def context(postgres_test_settings: Settings):
    settings = postgres_test_settings.model_copy(
        update={
            "session_secret": SecretStr("test-only-session-secret-which-is-at-least-32-bytes"),
            "admin_password_hash": SecretStr(PasswordHash.recommended().hash(OWNER_PASSWORD)),
        }
    )
    await initialize_database(settings)
    app = create_app(settings)
    created_ids: list[UUID] = []
    owner_tokens: list[str] = []
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))

        async def client():
            return await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url=settings.public_app_url
                )
            )

        async def create(customer, facts=None):
            # Legacy capability fixtures are internal; the public create route requires an account.
            async with app.state.database.sessions() as database:
                response = await create_order(
                    database,
                    settings,
                    CreateOrder(
                        **{
                            "seller_name": "Test Seller",
                            "product_name": "Fictional cable clips",
                            "contact": "private@example.invalid",
                            "facts": facts or [],
                            "is_demo": True,
                        }
                    ),
                )
            result = response.model_dump(mode="json")
            created_ids.append(UUID(result["id"]))
            return result

        async def owner():
            customer = await client()
            response = await customer.post(
                "/api/v1/internal/auth/login",
                json={"username": "owner", "password": OWNER_PASSWORD},
            )
            assert response.status_code == 200, response.text
            owner_tokens.append(customer.cookies.get("owner_session"))
            return customer, response.json()["csrf_token"]

        yield settings, app, client, create, owner
        async with app.state.database.sessions() as session:
            # Only records created by this fixture; no TRUNCATE or broad database cleanup.
            if created_ids:
                await session.execute(delete(Order).where(Order.id.in_(created_ids)))
            if owner_tokens:
                await session.execute(
                    delete(AuthSession).where(
                        AuthSession.token_hash.in_([token_hash(token) for token in owner_tokens])
                    )
                )
            await session.commit()


async def exchange(customer: httpx.AsyncClient, order: dict) -> tuple[str, str]:
    token = order["private_url"].split("#token=", 1)[1]
    response = await customer.post(
        "/api/v1/order-access/exchange", json={"order_id": order["id"], "token": token}
    )
    assert response.status_code == 200, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    return response.json()["csrf_token"], token


async def test_scoped_access_csrf_snapshots_and_persistence(context):
    settings, app, client, create, _ = context
    customer = await client()
    other = await client()
    first = await create(customer)
    second = await create(other)
    assert first["phase"] == "INTAKE"  # Incomplete data is deliberately accepted at intake.
    assert (await other.get(f"/api/v1/orders/{first['id']}")).status_code == 401
    csrf, link_token = await exchange(customer, first)
    assert (await customer.get(f"/api/v1/orders/{second['id']}")).status_code == 404
    assert (await customer.get("/api/v1/internal/orders")).status_code == 401
    visible = (await customer.get(f"/api/v1/orders/{first['id']}")).json()
    assert visible["facts"] == []
    assert "contact" not in visible and "commands" not in visible
    assert visible["workflow_status"] == "queued"
    assert (await customer.get("/api/v1/order-access/session")).json()["csrf_token"] == csrf
    path = f"/api/v1/orders/{first['id']}/supplements"
    fact = {"key": "package_quantity", "value": "6", "confirmed": True}
    supplement = {"expected_revision": 1, "facts": [fact]}
    assert (await customer.post(path, json=supplement)).status_code == 403
    assert (
        await customer.post(
            path, json=supplement, headers={"X-CSRF-Token": csrf, "Origin": "https://evil.invalid"}
        )
    ).status_code == 403
    saved = await customer.post(path, json=supplement, headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert (
        await customer.post(path, json=supplement, headers={"X-CSRF-Token": csrf})
    ).status_code == 409
    message = await customer.post(
        f"/api/v1/orders/{first['id']}/messages",
        json={"expected_revision": 2, "content": "请保留事实依据。"},
        headers={"X-CSRF-Token": csrf},
    )
    assert message.status_code == 201, message.text
    history = (await customer.get(f"/api/v1/orders/{first['id']}/messages")).json()
    assert history["items"][0]["content"] == "请保留事实依据。"
    async with app.state.database.sessions() as session:
        link = await session.get(OrderAccess, token_hash(link_token))
        assert link is not None and link.token_hash != link_token
        snapshots = (
            await session.scalars(
                select(InputSnapshot)
                .where(InputSnapshot.order_id == UUID(first["id"]))
                .order_by(InputSnapshot.revision)
            )
        ).all()
        assert [row.revision for row in snapshots] == [1, 2, 3]
        assert snapshots[0].facts == []
    # A new process/app can read the same durable session and data after a restart.
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url=settings.public_app_url,
            cookies={"seller_session": customer.cookies.get("seller_session")},
        ) as restored:
            assert (await restored.get(f"/api/v1/orders/{first['id']}")).json()["revision"] == 3


async def test_decision_commands_are_idempotent_and_do_not_apply_themselves(context):
    _, app, client, create, login_owner = context
    customer = await client()
    order = await create(customer)
    owner, csrf = await login_owner()
    path = f"/api/v1/internal/orders/{order['id']}/decisions"
    body = {"action": "accept", "expected_revision": 1}
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "test-accept-one"}
    assert (await owner.post(path, json=body, headers=headers)).status_code == 409
    async with app.state.database.sessions() as session:
        row = await session.get(Order, UUID(order["id"]))
        row.phase = "AWAITING_ACCEPTANCE"  # Fixture only; M2 has no acquisition consumer.
        await session.commit()
    stale = {"action": "accept", "expected_revision": 99}
    assert (await owner.post(path, json=stale, headers=headers)).status_code == 409
    assert (
        await owner.post(path, json=body, headers={"Idempotency-Key": "without-csrf"})
    ).status_code == 403
    first = await owner.post(path, json=body, headers=headers)
    assert first.status_code == 202, first.text
    duplicate = await owner.post(path, json=body, headers=headers)
    assert duplicate.status_code == 202
    assert first.json()["command_id"] == duplicate.json()["command_id"]
    changed = await owner.post(path, json={**body, "action": "reject"}, headers=headers)
    assert changed.status_code == 409
    detail = (await owner.get(f"/api/v1/internal/orders/{order['id']}")).json()
    assert detail["phase"] == "AWAITING_ACCEPTANCE"
    assert detail["allowed_actions"] == []
    assert detail["decisions"][0]["status"] == "requested"
    assert detail["pending_command_id"] == first.json()["command_id"]
    assert (
        await owner.get(f"/api/v1/orders/{order['id']}/messages")
    ).status_code == 200  # Internal read access does not require a seller session.
    async with app.state.database.sessions() as session:
        identifier = UUID(order["id"])
        assert (
            await session.scalar(
                select(func.count()).select_from(Command).where(Command.order_id == identifier)
            )
            == 2
        )  # START and ACCEPT, not two ACCEPT commands.
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.order_id == identifier)
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(HumanDecision)
                .where(HumanDecision.order_id == identifier)
            )
            == 1
        )
    assert (
        await owner.post("/api/v1/internal/auth/logout", headers={"X-CSRF-Token": csrf})
    ).status_code == 204
    assert (await owner.get("/api/v1/internal/auth/session")).status_code == 401


async def test_conflicts_and_expired_capabilities(context):
    _, app, client, create, _ = context
    customer = await client()
    order = await create(customer, [{"key": "package_quantity", "value": "6", "confirmed": True}])
    csrf, _ = await exchange(customer, order)
    response = await customer.post(
        f"/api/v1/orders/{order['id']}/supplements",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_revision": 1,
            "facts": [
                {"key": "package_quantity", "value": "6", "confirmed": True},
                {"key": "package_quantity", "value": "4", "confirmed": False},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["conflicting_fact_keys"] == ["pack_quantity"]
    assert len(response.json()["facts"]) == 2
    resolved = await customer.post(
        f"/api/v1/orders/{order['id']}/supplements",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_revision": 2,
            "facts": [{"key": "package_quantity", "value": "4", "confirmed": True}],
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["conflicting_fact_keys"] == []
    async with app.state.database.sessions() as session:
        history = (
            await session.scalars(
                select(InputSnapshot)
                .where(InputSnapshot.order_id == UUID(order["id"]))
                .order_by(InputSnapshot.revision)
            )
        ).all()
        assert [len(item.facts) for item in history] == [1, 2, 1]
        assert history[0].facts[0]["value"] == "6"
    async with app.state.database.sessions() as session:
        current = await session.get(AuthSession, token_hash(customer.cookies.get("seller_session")))
        current.expires_at = now() - timedelta(seconds=1)
        await session.commit()
    assert (await customer.get("/api/v1/order-access/session")).status_code == 401
    async with app.state.database.sessions() as session:
        link = await session.scalar(
            select(OrderAccess).where(OrderAccess.order_id == UUID(order["id"]))
        )
        link.expires_at = now() - timedelta(seconds=1)
        await session.commit()
    token = order["private_url"].split("#token=", 1)[1]
    assert (
        await customer.post(
            "/api/v1/order-access/exchange", json={"order_id": order["id"], "token": token}
        )
    ).status_code == 401
