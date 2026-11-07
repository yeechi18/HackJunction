"""Opaque server-side sessions, scoped capabilities, Origin and CSRF checks."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.models import AuthSession, OrderAccess, now

SELLER_COOKIE = "seller_session"
OWNER_COOKIE = "owner_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_session_secret(settings: Settings) -> str:
    if not settings.session_secret or len(settings.session_secret.get_secret_value()) < 32:
        raise HTTPException(503, "Session configuration is incomplete; run company-init-admin.")
    return settings.session_secret.get_secret_value()


def csrf_token(settings: Settings, token: str) -> str:
    return hmac.new(require_session_secret(settings).encode(), token.encode(), "sha256").hexdigest()


def origin_of(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def check_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return  # Non-browser clients still require an unguessable session plus CSRF for writes.
    allowed = {origin_of(settings.public_app_url)}
    if settings.app_env == "development":
        allowed.update({"http://localhost:3000", "http://127.0.0.1:3000"})
    if origin.lower().rstrip("/") not in allowed:
        raise HTTPException(403, "Request Origin is not allowed.")


def check_csrf(request: Request, settings: Settings, token: str) -> None:
    check_origin(request, settings)
    actual = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(actual.encode(), csrf_token(settings, token).encode()):
        raise HTTPException(403, "Missing or invalid CSRF token.")


async def get_session(
    database: AsyncSession, token: str | None, kind: str, order_id: UUID | None = None
) -> AuthSession:
    if not token:
        raise HTTPException(401, "Authentication required.")
    session = await database.get(AuthSession, token_hash(token))
    if (
        session is None
        or session.kind != kind
        or session.revoked_at is not None
        or session.expires_at <= now()
    ):
        raise HTTPException(401, "Session is invalid or expired.")
    if order_id is not None and session.order_id != order_id:
        raise HTTPException(404, "Order not found.")
    return session


async def create_session(
    database: AsyncSession, settings: Settings, kind: str, order_id: UUID | None = None
) -> tuple[str, AuthSession]:
    require_session_secret(settings)
    token = secrets.token_urlsafe(32)
    hours = settings.owner_session_hours if kind == "owner" else settings.seller_session_hours
    session = AuthSession(
        token_hash=token_hash(token),
        kind=kind,
        order_id=order_id,
        expires_at=now() + timedelta(hours=hours),
    )
    database.add(session)
    await database.flush()
    return token, session


def set_session_cookie(
    response: Response, settings: Settings, token: str, session: AuthSession
) -> None:
    response.set_cookie(
        OWNER_COOKIE if session.kind == "owner" else SELLER_COOKIE,
        token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/api/v1",
        max_age=max(0, int((session.expires_at - now()).total_seconds())),
    )


async def exchange_link(
    database: AsyncSession, settings: Settings, order_id: UUID, token: str
) -> tuple[str, AuthSession]:
    link = await database.get(OrderAccess, token_hash(token))
    if link is None or link.order_id != order_id or link.expires_at <= now():
        raise HTTPException(401, "Order link is invalid or expired.")
    return await create_session(database, settings, "seller", order_id)
