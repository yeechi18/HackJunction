from fastapi import APIRouter, Request, Response

from company.api.dependencies import DB, Configuration
from company.persistence.models import now
from company.schemas.commands import ExchangeInput, SellerSessionView
from company.services.access import (
    SELLER_COOKIE,
    check_csrf,
    check_origin,
    csrf_token,
    exchange_link,
    get_session,
    set_session_cookie,
)

router = APIRouter(prefix="/order-access", tags=["seller access"])


@router.post("/exchange", response_model=SellerSessionView)
async def exchange(
    body: ExchangeInput, request: Request, response: Response, database: DB, settings: Configuration
) -> SellerSessionView:
    check_origin(request, settings)
    token, session = await exchange_link(database, settings, body.order_id, body.token)
    await database.commit()
    set_session_cookie(response, settings, token, session)
    return SellerSessionView(
        order_id=session.order_id,
        csrf_token=csrf_token(settings, token),
        expires_at=session.expires_at,
    )


@router.get("/session", response_model=SellerSessionView)
async def current(request: Request, database: DB, settings: Configuration) -> SellerSessionView:
    token = request.cookies.get(SELLER_COOKIE)
    session = await get_session(database, token, "seller")
    return SellerSessionView(
        order_id=session.order_id,
        csrf_token=csrf_token(settings, token),
        expires_at=session.expires_at,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, database: DB, settings: Configuration
) -> None:
    token = request.cookies.get(SELLER_COOKIE)
    session = await get_session(database, token, "seller")
    check_csrf(request, settings, token)
    session.revoked_at = now()
    await database.commit()
    response.delete_cookie(SELLER_COOKIE, path="/api/v1")
