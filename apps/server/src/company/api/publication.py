"""Authenticated order-scoped seller feedback and simulated publication."""

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from company.api.dependencies import DB, Configuration
from company.api.orders import read_access, seller_write
from company.schemas.publication import (
    ConfirmationInput,
    ConfirmationQueued,
    FeedbackInput,
    FeedbackQueued,
    PublicationView,
)
from company.services.publication import (
    PublicationError,
    publication_view,
    queue_confirmation,
    queue_feedback,
)

router = APIRouter(tags=["seller publication"])


@router.post("/orders/{order_id}/confirmations", response_model=ConfirmationQueued, status_code=202)
async def confirm(
    order_id: UUID,
    body: ConfirmationInput,
    request: Request,
    database: DB,
    settings: Configuration,
    idempotency_key: str = Header(min_length=1, max_length=128),
) -> ConfirmationQueued:
    await seller_write(request, database, settings, order_id)
    try:
        return await queue_confirmation(database, settings, order_id, body, idempotency_key)
    except PublicationError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/orders/{order_id}/feedback", response_model=FeedbackQueued, status_code=202)
async def feedback(
    order_id: UUID,
    body: FeedbackInput,
    request: Request,
    database: DB,
    settings: Configuration,
    idempotency_key: str = Header(min_length=1, max_length=128),
) -> FeedbackQueued:
    await seller_write(request, database, settings, order_id)
    try:
        return await queue_feedback(database, order_id, body, idempotency_key)
    except PublicationError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/orders/{order_id}/publication", response_model=PublicationView)
async def publication(order_id: UUID, request: Request, database: DB) -> PublicationView:
    await read_access(request, database, order_id)
    return await publication_view(database, order_id)
