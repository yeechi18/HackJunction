"""Account persistence, server-enforced ownership and authentication boundaries."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from test_orders_integration import context  # noqa: F401

from company.main import create_app
from company.persistence.models import AuthSession, EmailVerification, Order, SellerAccount, now
from company.services.access import token_hash

pytestmark = pytest.mark.integration
PASSWORD = "Account12!"
ORDER = {
    "product_details": "Fictional silicone cable clips, known material only.",
    "product_name": "Fictional clips",
    "facts": [],
    "is_demo": True,
}


@pytest.fixture
async def accounts(context, tmp_path):  # noqa: F811
    settings, app, client, _, owner = context
    settings = settings.model_copy(update={"mail_mode": "test", "mail_test_dir": tmp_path})
    app.state.settings = settings
    emails = set()

    async def request_code(customer, email):
        emails.add(email.strip().casefold())
        result = await customer.post("/api/v1/seller/auth/verification-code", json={"email": email})
        assert result.status_code == 200, result.text
        return read_code(settings, email)

    async def register(email=None):
        customer = await client()
        email = email or f"test-{uuid4().hex}@example.invalid"
        code = await request_code(customer, email)
        result = await customer.post(
            "/api/v1/seller/auth/register",
            json={
                "email": email,
                "password": PASSWORD,
                "display_name": "Test Seller",
                "verification_code": code,
            },
        )
        assert result.status_code == 201, result.text
        data = result.json()
        return customer, data

    yield settings, app, client, register, owner, request_code
    async with app.state.database.sessions() as db:
        ids = (
            await db.scalars(select(SellerAccount.id).where(SellerAccount.email.in_(emails)))
        ).all()
        if ids:
            await db.execute(delete(Order).where(Order.seller_account_id.in_(ids)))
            await db.execute(delete(SellerAccount).where(SellerAccount.id.in_(ids)))
        await db.execute(delete(EmailVerification).where(EmailVerification.email.in_(emails)))
        await db.commit()


def read_code(settings, email):
    key = hashlib.sha256(email.strip().casefold().encode()).hexdigest()
    return json.loads((settings.mail_test_dir / f"{key}.json").read_text())["code"]


async def create(customer, account):
    result = await customer.post(
        "/api/v1/orders", json=ORDER, headers={"X-CSRF-Token": account["csrf_token"]}
    )
    assert result.status_code == 201, result.text
    return result.json()


async def test_registration_login_logout_and_restart(accounts):
    settings, app, client, register, _, _ = accounts
    email = f"Mixed-{uuid4().hex}@Example.invalid"
    customer, account = await register(email)
    assert account["email"] == email.casefold()
    assert "password" not in str(account)
    token = customer.cookies.get("account_session")
    assert token and not customer.cookies.get("seller_session")
    async with app.state.database.sessions() as db:
        row = await db.get(SellerAccount, UUID(account["id"]))
        assert row.password_hash != PASSWORD
        assert PasswordHash.recommended().verify(PASSWORD, row.password_hash)
        session = await db.get(AuthSession, token_hash(token))
        assert session.account_id == row.id and session.kind == "account"
    duplicate = await customer.post(
        "/api/v1/seller/auth/register",
        json={
            "email": email.upper(),
            "password": PASSWORD,
            "display_name": "Duplicate",
            "verification_code": "000000",
        },
    )
    assert duplicate.status_code == 409
    order = await create(customer, account)
    assert (await customer.get("/api/v1/seller/orders")).json()["total"] == 1
    # Durable session can access the account and its order in another API instance.
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url=settings.public_app_url,
            cookies={"account_session": token},
        ) as restored:
            assert (await restored.get("/api/v1/seller/auth/session")).json()["id"] == account["id"]
            assert (await restored.get(f"/api/v1/orders/{order['id']}")).status_code == 200
    assert (await customer.post("/api/v1/seller/auth/logout")).status_code == 403
    assert (
        await customer.post(
            "/api/v1/seller/auth/logout", headers={"X-CSRF-Token": account["csrf_token"]}
        )
    ).status_code == 204
    assert (await customer.get("/api/v1/seller/auth/session")).status_code == 401
    assert (await customer.get(f"/api/v1/orders/{order['id']}")).status_code == 401
    login = await customer.post(
        "/api/v1/seller/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200
    assert customer.cookies.get("account_session") != token
    assert (await customer.get("/api/v1/seller/orders")).json()["items"][0]["id"] == order["id"]
    # Even with a stolen copy, the logged-out token is revoked.
    visitor = await client()
    visitor.cookies.set("account_session", token)
    assert (await visitor.get("/api/v1/seller/auth/session")).status_code == 401


async def test_new_orders_require_login_csrf_and_matching_account(accounts):
    _, app, client, register, login_owner, _ = accounts
    guest = await client()
    assert (await guest.post("/api/v1/orders", json=ORDER)).status_code == 401
    first, a = await register()
    second, b = await register()
    assert (await first.post("/api/v1/orders", json=ORDER)).status_code == 403
    assert (
        await first.post(
            "/api/v1/orders",
            json=ORDER,
            headers={"X-CSRF-Token": a["csrf_token"], "Origin": "https://evil.invalid"},
        )
    ).status_code == 403
    injected = await first.post(
        "/api/v1/orders",
        json={**ORDER, "seller_account_id": b["id"]},
        headers={"X-CSRF-Token": a["csrf_token"]},
    )
    assert injected.status_code == 422
    order = await create(first, a)
    other_order = await create(second, b)
    async with app.state.database.sessions() as db:
        assert (await db.get(Order, UUID(order["id"]))).seller_account_id == UUID(a["id"])
    own = (await first.get("/api/v1/seller/orders?limit=1&offset=0")).json()
    assert own["total"] == 1 and [item["id"] for item in own["items"]] == [order["id"]]
    assert (await first.get("/api/v1/seller/orders?offset=1")).json()["items"] == []
    assert (await first.get("/api/v1/seller/orders?limit=101")).status_code == 422
    assert (await first.get(f"/api/v1/orders/{other_order['id']}")).status_code == 404
    for suffix in [
        "",
        "/messages",
        "/events",
        "/assets",
        "/deliverables",
        "/download",
        "/publication",
        "/support-requests",
    ]:
        assert (await second.get(f"/api/v1/orders/{order['id']}{suffix}")).status_code == 404
        assert (await guest.get(f"/api/v1/orders/{order['id']}{suffix}")).status_code == 401
    body = {"order_id": order["id"], "token": order["private_url"].split("#token=", 1)[1]}
    assert (await guest.post("/api/v1/order-access/exchange", json=body)).status_code == 401
    assert (await second.post("/api/v1/order-access/exchange", json=body)).status_code == 404
    exchange = await first.post("/api/v1/order-access/exchange", json=body)
    assert exchange.status_code == 200 and exchange.json()["csrf_token"] == a["csrf_token"]
    assert (
        await first.get(f"/api/v1/order-access/session?order_id={order['id']}")
    ).status_code == 200
    path = f"/api/v1/orders/{order['id']}/messages"
    payload = {"expected_revision": 1, "content": "Persisted seller message"}
    assert (
        await second.post(path, json=payload, headers={"X-CSRF-Token": b["csrf_token"]})
    ).status_code == 404
    assert (await first.post(path, json=payload)).status_code == 403
    assert (
        await first.post(path, json=payload, headers={"X-CSRF-Token": a["csrf_token"]})
    ).status_code == 201
    assert (await first.get("/api/v1/internal/orders")).status_code == 401
    owner, _ = await login_owner()
    assert (await owner.get(f"/api/v1/internal/orders/{order['id']}")).status_code == 200
    assert (await owner.post("/api/v1/orders", json=ORDER)).status_code == 401


async def test_auth_validation_rotation_expiry_and_rate_limit(accounts):
    _, app, client, register, _, _ = accounts
    guest = await client()
    for patch in [
        {"password": "Sh0!x"},
        {"password": "x" * 129},
        {"display_name": " "},
        {"email": "not-an-email"},
    ]:
        response = await guest.post(
            "/api/v1/seller/auth/register",
            json={
                "email": "fake@example.invalid",
                "password": PASSWORD,
                "display_name": "Test",
                "verification_code": "000000",
                **patch,
            },
        )
        assert response.status_code == 422
        assert PASSWORD not in response.text and "Sh0!x" not in response.text
        assert all("input" not in error for error in response.json()["detail"])
    response = await guest.post(
        "/api/v1/seller/auth/register",
        json={
            "email": "fake@example.invalid",
            "password": PASSWORD,
            "display_name": "Test",
            "verification_code": "000000",
        },
        headers={"Origin": "https://evil.invalid"},
    )
    assert response.status_code == 403
    customer, account = await register()
    old = customer.cookies.get("account_session")
    result = await customer.post(
        "/api/v1/seller/auth/login", json={"email": account["email"], "password": PASSWORD}
    )
    assert result.status_code == 200
    assert (
        "httponly" in result.headers["set-cookie"].lower()
        and "samesite=lax" in result.headers["set-cookie"].lower()
    )
    async with app.state.database.sessions() as db:
        assert (await db.get(AuthSession, token_hash(old))).revoked_at is not None
        current = await db.get(AuthSession, token_hash(customer.cookies.get("account_session")))
        current.expires_at = now() - timedelta(seconds=1)
        await db.commit()
    assert (await customer.get("/api/v1/seller/auth/session")).status_code == 401
    for _ in range(15):
        response = await guest.post(
            "/api/v1/seller/auth/login",
            json={"email": "missing@example.invalid", "password": PASSWORD},
        )
        assert (
            response.status_code == 401
            and response.json()["detail"] == "Invalid email or password."
        )
    assert (
        await guest.post(
            "/api/v1/seller/auth/login",
            json={"email": "missing@example.invalid", "password": PASSWORD},
        )
    ).status_code == 429


async def test_code_lifecycle_expiry_wrong_attempts_and_resend(accounts):
    settings, app, client, _, _, request_code = accounts
    customer = await client()
    email = f"codes-{uuid4().hex}@example.invalid"
    code = await request_code(customer, email)
    body = {"email": email, "password": PASSWORD, "display_name": "Test", "verification_code": code}
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        assert row.code_hash != code and len(row.code_hash) == 64
        assert len(row.nonce) == 32 and row.attempts == 0
    again = await customer.post("/api/v1/seller/auth/verification-code", json={"email": email})
    assert again.status_code == 429 and again.headers["Retry-After"] == "60"
    wrong = "000001" if code != "000001" else "000002"
    for _ in range(5):
        response = await customer.post(
            "/api/v1/seller/auth/register", json={**body, "verification_code": wrong}
        )
        assert response.status_code == 400 and code not in response.text
    assert (await customer.post("/api/v1/seller/auth/register", json=body)).status_code == 429
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        assert row.attempts == 5
        row.sent_at = now() - timedelta(seconds=61)
        await db.commit()
    # A successful resend resets attempts and invalidates the previous challenge.
    code = await request_code(customer, email)
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        assert row.attempts == 0
        row.expires_at = now() - timedelta(seconds=1)
        await db.commit()
    assert (
        await customer.post(
            "/api/v1/seller/auth/register", json={**body, "verification_code": code}
        )
    ).status_code == 400
    # Codes and attempt limits survive API restarts.
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url=settings.public_app_url
        ) as visitor:
            assert (
                await visitor.post(
                    "/api/v1/seller/auth/register", json={**body, "verification_code": code}
                )
            ).status_code == 400


async def test_email_binding_consumption_and_concurrent_requests(accounts):
    import asyncio

    _, app, client, _, _, request_code = accounts
    customer = await client()
    email = f"bound-{uuid4().hex}@example.invalid"
    code = await request_code(customer, email)
    body = {"email": email, "password": PASSWORD, "display_name": "Test", "verification_code": code}
    other = f"other-{uuid4().hex}@example.invalid"
    assert (
        await customer.post("/api/v1/seller/auth/register", json={**body, "email": other})
    ).status_code == 400
    results = await asyncio.gather(
        *[customer.post("/api/v1/seller/auth/register", json=body) for _ in range(2)]
    )
    assert sorted(result.status_code for result in results) == [201, 409]
    assert (
        await customer.post("/api/v1/seller/auth/verification-code", json={"email": email})
    ).status_code == 409
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        assert row.consumed_at is not None
        # Demonstrate that even if the account is removed, the used code cannot register again.
        await db.execute(delete(SellerAccount).where(SellerAccount.email == email))
        await db.commit()
    assert (await customer.post("/api/v1/seller/auth/register", json=body)).status_code == 400


async def test_mail_failure_and_missing_configuration_fail_closed(accounts, monkeypatch):
    _, app, client, _, _, request_code = accounts
    customer = await client()
    email = f"delivery-{uuid4().hex}@example.invalid"
    code = await request_code(customer, email)
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        row.sent_at = now() - timedelta(seconds=61)
        await db.commit()

    def failed_delivery(*args):
        raise RuntimeError("PRIVATE-SMTP-CREDENTIAL-MARKER")

    monkeypatch.setattr("company.api.accounts.deliver_registration_code", failed_delivery)
    response = await customer.post("/api/v1/seller/auth/verification-code", json={"email": email})
    assert response.status_code == 503 and "PRIVATE-SMTP" not in response.text
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        from company.services.registration_email import code_digest

        assert row.code_hash == code_digest(app.state.settings, email, row.nonce, code)
    app.state.settings = app.state.settings.model_copy(
        update={"mail_mode": "smtp", "smtp_password": None}
    )
    assert (
        await customer.post("/api/v1/seller/auth/verification-code", json={"email": email})
    ).status_code == 503


async def test_valid_email_code_survives_restart_and_cannot_be_reused(accounts):
    settings, app, client, _, _, request_code = accounts
    customer = await client()
    email = f"restart-code-{uuid4().hex}@example.invalid"
    code = await request_code(customer, email)
    body = {
        "email": email,
        "password": PASSWORD,
        "display_name": "Restart",
        "verification_code": code,
    }
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url=settings.public_app_url
        ) as visitor:
            assert (
                await visitor.post("/api/v1/seller/auth/register", json=body)
            ).status_code == 201
            assert (
                await visitor.post("/api/v1/seller/auth/register", json=body)
            ).status_code == 409
    async with app.state.database.sessions() as db:
        assert (await db.get(EmailVerification, email)).consumed_at is not None


async def test_concurrent_resend_changes_code_once_and_invalidates_old_code(accounts, monkeypatch):
    import asyncio

    settings, app, client, _, _, request_code = accounts
    customer = await client()
    email = f"resend-{uuid4().hex}@example.invalid"
    monkeypatch.setattr("company.api.accounts.secrets.randbelow", lambda limit: 111111)
    first = await request_code(customer, email)
    async with app.state.database.sessions() as db:
        row = await db.get(EmailVerification, email)
        row.sent_at = now() - timedelta(seconds=61)
        await db.commit()
    monkeypatch.setattr("company.api.accounts.secrets.randbelow", lambda limit: 222222)
    results = await asyncio.gather(
        *[
            customer.post("/api/v1/seller/auth/verification-code", json={"email": email})
            for _ in range(2)
        ]
    )
    assert sorted(result.status_code for result in results) == [200, 429]
    second = read_code(settings, email)
    assert second != first
    body = {
        "email": email,
        "password": PASSWORD,
        "display_name": "Resend",
        "verification_code": first,
    }
    assert (await customer.post("/api/v1/seller/auth/register", json=body)).status_code == 400
    assert (
        await customer.post(
            "/api/v1/seller/auth/register", json={**body, "verification_code": second}
        )
    ).status_code == 201


async def test_account_identity_is_automatic_and_product_input_persists(accounts):
    settings, app, client, register, owner_login, _ = accounts
    customer, account = await register()
    payload = {
        "product_name": "五项建单测试",
        "product_details": "硅胶材质，黑色，6个装，单件4×3×2cm。",
        "domestic_product_url": "https://item.jd.com/100000000000.html",
        "notes": "希望突出桌面整洁，不夸大防水效果。",
        "is_demo": True,
    }
    response = await customer.post(
        "/api/v1/orders", json=payload, headers={"X-CSRF-Token": account["csrf_token"]}
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    detail = (await customer.get(f"/api/v1/orders/{identifier}")).json()
    assert detail["product_details"] == payload["product_details"]
    assert detail["domestic_product_url"] == payload["domestic_product_url"]
    assert detail["notes"] == payload["notes"] and detail["facts"] == []
    owner, _ = await owner_login()
    internal = (await owner.get(f"/api/v1/internal/orders/{identifier}")).json()
    assert internal["seller_name"] == account["display_name"]
    assert internal["contact"] == account["email"]
    for forged in [{"seller_name": "Different Seller"}, {"contact": "other@example.invalid"}]:
        rejected = await customer.post(
            "/api/v1/orders",
            json={**payload, **forged},
            headers={"X-CSRF-Token": account["csrf_token"]},
        )
        assert rejected.status_code == 422
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url=settings.public_app_url,
            cookies={"account_session": customer.cookies.get("account_session")},
        ) as visitor:
            saved = (await visitor.get(f"/api/v1/orders/{identifier}")).json()
            assert saved["product_details"] == payload["product_details"]
            assert saved["domestic_product_url"] == payload["domestic_product_url"]
