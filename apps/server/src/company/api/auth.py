from fastapi import APIRouter, HTTPException, Request, Response
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from starlette.concurrency import run_in_threadpool

from company.api.dependencies import DB, Configuration, Owner
from company.persistence.models import now
from company.schemas.commands import LoginInput, OwnerSessionView
from company.services.access import (
    OWNER_COOKIE,
    check_csrf,
    check_origin,
    create_session,
    csrf_token,
    set_session_cookie,
)

router = APIRouter(prefix="/internal/auth", tags=["owner authentication"])
passwords = PasswordHash.recommended()


@router.post("/login", response_model=OwnerSessionView)
async def login(
    body: LoginInput, request: Request, response: Response, database: DB, settings: Configuration
) -> OwnerSessionView:
    check_origin(request, settings)
    if settings.admin_password_hash is None:
        raise HTTPException(503, "Owner account is not initialized; run company-init-admin.")
    try:
        verified = await run_in_threadpool(
            passwords.verify, body.password, settings.admin_password_hash.get_secret_value()
        )
    except (UnknownHashError, ValueError, TypeError):
        raise HTTPException(503, "Owner account configuration is invalid.") from None
    if body.username != "owner" or not verified:
        raise HTTPException(401, "Invalid username or password.")
    token, session = await create_session(database, settings, "owner")
    await database.commit()
    set_session_cookie(response, settings, token, session)
    return OwnerSessionView(csrf_token=csrf_token(settings, token), expires_at=session.expires_at)


@router.get("/session", response_model=OwnerSessionView)
async def current(request: Request, settings: Configuration, owner: Owner) -> OwnerSessionView:
    return OwnerSessionView(
        csrf_token=csrf_token(settings, request.cookies.get(OWNER_COOKIE, "")),
        expires_at=owner.expires_at,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, database: DB, settings: Configuration, owner: Owner
) -> None:
    check_csrf(request, settings, request.cookies.get(OWNER_COOKIE, ""))
    owner.revoked_at = now()
    await database.commit()
    response.delete_cookie(OWNER_COOKIE, path="/api/v1")
