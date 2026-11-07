"""Seller identities and account-owned orders, separate from owner authentication."""

import hashlib
import hmac
import secrets
from collections import OrderedDict, deque
from datetime import timedelta
from time import monotonic

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from company.api.dependencies import DB, Configuration
from company.persistence.models import EmailVerification, Order, SellerAccount, now
from company.schemas.accounts import (
    AccountLogin,
    AccountOrderList,
    AccountOrderView,
    AccountRegistration,
    AccountSessionView,
    CodeDeliveryView,
    EmailAddress,
)
from company.services.access import (
    ACCOUNT_COOKIE,
    SELLER_COOKIE,
    check_csrf,
    check_origin,
    create_session,
    csrf_token,
    get_session,
    require_session_secret,
    set_session_cookie,
)
from company.services.registration_email import (
    code_digest,
    deliver_registration_code,
    lock_email,
    require_mail,
)

router = APIRouter(prefix="/seller", tags=["seller accounts"])
passwords = PasswordHash.recommended()
dummy_hash = passwords.hash("unused-login-timing-placeholder-not-an-account")


def throttle(request: Request, email: str, action: str) -> None:
    """Bounded per-process attempt windows for the single-process native API."""
    if not hasattr(request.app.state, "account_attempts"):
        request.app.state.account_attempts = OrderedDict()
    attempts = request.app.state.account_attempts
    current = monotonic()
    keys = [
        (
            f"ip:{action}:{request.client.host if request.client else 'unknown'}",
            20 if action == "verification" else 60,
        )
    ]
    if action == "login":
        keys.append((f"email:{email}", 15))
    if action == "verification":
        keys.append((f"verification:{email}", 5))
    for value, limit in keys:
        key = hashlib.sha256(value.encode()).hexdigest()
        window = attempts.setdefault(key, deque())
        while window and window[0] < current - 900:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(429, "Too many authentication attempts; try again later.")
        window.append(current)
        attempts.move_to_end(key)
    while len(attempts) > 4096:
        attempts.popitem(last=False)


async def account_session(request: Request, database):
    token = request.cookies.get(ACCOUNT_COOKIE)
    session = await get_session(database, token, "account")
    account = await database.get(SellerAccount, session.account_id) if session.account_id else None
    if account is None:
        raise HTTPException(401, "Account session is unavailable.")
    return token, session, account


def session_view(account, session, settings, token) -> AccountSessionView:
    return AccountSessionView(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        csrf_token=csrf_token(settings, token),
        expires_at=session.expires_at,
    )


async def sign_in(request, response, database, settings, account):
    previous = request.cookies.get(ACCOUNT_COOKIE)
    if previous:
        try:
            old = await get_session(database, previous, "account")
            old.revoked_at = now()
        except HTTPException:
            pass
    token, session = await create_session(database, settings, "account", account_id=account.id)
    await database.commit()
    set_session_cookie(response, settings, token, session)
    response.delete_cookie(SELLER_COOKIE, path="/api/v1")
    return session_view(account, session, settings, token)


@router.post("/auth/verification-code", response_model=CodeDeliveryView)
async def send_code(body: EmailAddress, request: Request, database: DB, settings: Configuration):
    check_origin(request, settings)
    require_session_secret(settings)
    require_mail(settings)
    throttle(request, body.email, "verification")
    await lock_email(database, body.email)
    if await database.scalar(select(SellerAccount.id).where(SellerAccount.email == body.email)):
        raise HTTPException(409, "This email cannot be registered; try signing in.")
    previous = await database.get(EmailVerification, body.email)
    current = now()
    if previous and (current - previous.sent_at).total_seconds() < 60:
        raise HTTPException(
            429, "Wait before requesting another code.", headers={"Retry-After": "60"}
        )
    code = f"{secrets.randbelow(1000000):06d}"
    nonce = secrets.token_hex(16)
    try:
        await run_in_threadpool(deliver_registration_code, settings, body.email, code)
    except Exception:
        # SMTP errors can include credentials or recipient data; never echo or log them.
        raise HTTPException(503, "Verification email could not be sent; try again later.") from None
    record = previous or EmailVerification(email=body.email)
    record.code_hash = code_digest(settings, body.email, nonce, code)
    record.nonce = nonce
    record.sent_at = current
    record.expires_at = current + timedelta(minutes=10)
    record.attempts = 0
    record.consumed_at = None
    database.add(record)
    await database.commit()
    return CodeDeliveryView()


@router.post("/auth/register", response_model=AccountSessionView, status_code=201)
async def register(
    body: AccountRegistration,
    request: Request,
    response: Response,
    database: DB,
    settings: Configuration,
):
    check_origin(request, settings)
    require_session_secret(settings)
    throttle(request, body.email, "register")
    await lock_email(database, body.email)
    if await database.scalar(select(SellerAccount.id).where(SellerAccount.email == body.email)):
        raise HTTPException(409, "This email cannot be registered; try signing in.")
    verification = await database.get(EmailVerification, body.email)
    if verification is None or verification.consumed_at or verification.expires_at <= now():
        raise HTTPException(400, "Verification code is invalid or expired.")
    if verification.attempts >= 5:
        raise HTTPException(429, "Verification attempts exhausted; request a new code.")
    supplied = code_digest(
        settings, body.email, verification.nonce, body.verification_code.get_secret_value()
    )
    if not hmac.compare_digest(supplied, verification.code_hash):
        verification.attempts += 1
        await database.commit()  # Failed attempts must survive the HTTP error and process restarts.
        raise HTTPException(400, "Verification code is invalid or expired.")
    hashed = await run_in_threadpool(passwords.hash, body.password.get_secret_value())
    verification.consumed_at = now()
    account = SellerAccount(email=body.email, display_name=body.display_name, password_hash=hashed)
    database.add(account)
    try:
        await database.flush()
    except IntegrityError:
        await database.rollback()
        raise HTTPException(409, "This email cannot be registered; try signing in.") from None
    return await sign_in(request, response, database, settings, account)


@router.post("/auth/login", response_model=AccountSessionView)
async def login(
    body: AccountLogin,
    request: Request,
    response: Response,
    database: DB,
    settings: Configuration,
):
    check_origin(request, settings)
    require_session_secret(settings)
    throttle(request, body.email, "login")
    account = await database.scalar(select(SellerAccount).where(SellerAccount.email == body.email))
    verified = await run_in_threadpool(
        passwords.verify,
        body.password.get_secret_value(),
        account.password_hash if account else dummy_hash,
    )
    if account is None or not verified:
        raise HTTPException(401, "Invalid email or password.")
    return await sign_in(request, response, database, settings, account)


@router.get("/auth/session", response_model=AccountSessionView)
async def current(request: Request, database: DB, settings: Configuration):
    token, session, account = await account_session(request, database)
    return session_view(account, session, settings, token)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, database: DB, settings: Configuration):
    token, session, _ = await account_session(request, database)
    check_csrf(request, settings, token)
    session.revoked_at = now()
    await database.commit()
    response.delete_cookie(ACCOUNT_COOKIE, path="/api/v1")
    response.delete_cookie(SELLER_COOKIE, path="/api/v1")


def order_view(order: Order) -> AccountOrderView:
    return AccountOrderView(
        id=order.id,
        product_name=order.product_name,
        phase=order.phase,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get("/orders", response_model=AccountOrderList)
async def my_orders(
    request: Request,
    database: DB,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    _, _, account = await account_session(request, database)
    predicate = Order.seller_account_id == account.id
    total = await database.scalar(select(func.count()).select_from(Order).where(predicate))
    rows = await database.scalars(
        select(Order)
        .where(predicate)
        .order_by(Order.created_at.desc(), Order.id)
        .offset(offset)
        .limit(limit)
    )
    return AccountOrderList(
        items=[order_view(row) for row in rows], total=total or 0, limit=limit, offset=offset
    )
