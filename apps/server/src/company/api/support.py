"""Scoped support, measured human activity and explicit owner retry requests."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from company.api.dependencies import DB, Configuration, Owner, owner_write
from company.api.orders import read_access, seller_write
from company.schemas.support import (
    ActiveTimeInput,
    ActiveTimeView,
    OrderMetrics,
    RetryInput,
    RetryQueued,
    SupportReplyInput,
    SupportRequestInput,
    SupportTicketList,
    SupportTicketView,
)
from company.services import support

router = APIRouter(tags=["support and operations"])


@router.post(
    "/orders/{order_id}/support-requests", response_model=SupportTicketView, status_code=201
)
async def request_support(
    order_id: UUID,
    body: SupportRequestInput,
    request: Request,
    database: DB,
    settings: Configuration,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
) -> SupportTicketView:
    await seller_write(request, database, settings, order_id)
    return await support.request_support(database, order_id, body, idempotency_key)


@router.get("/orders/{order_id}/support-requests", response_model=SupportTicketList)
async def seller_tickets(order_id: UUID, request: Request, database: DB) -> SupportTicketList:
    await read_access(request, database, order_id)
    return await support.support_list(database, order_id=order_id)


@router.get("/internal/support-tickets", response_model=SupportTicketList)
async def owner_tickets(
    database: DB,
    owner: Owner,
    status: Literal["open", "resolved", "all"] = "open",
    order_id: UUID | None = None,
) -> SupportTicketList:
    return await support.support_list(database, order_id=order_id, status=status)


@router.post(
    "/internal/support-tickets/{ticket_id}/messages",
    response_model=SupportTicketView,
    dependencies=[Depends(owner_write)],
)
async def reply(
    ticket_id: UUID,
    body: SupportReplyInput,
    database: DB,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
) -> SupportTicketView:
    return await support.owner_reply(database, ticket_id, body, idempotency_key)


@router.post(
    "/internal/orders/{order_id}/time-entries",
    response_model=ActiveTimeView,
    status_code=201,
    dependencies=[Depends(owner_write)],
)
async def active_time(
    order_id: UUID,
    body: ActiveTimeInput,
    database: DB,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
) -> ActiveTimeView:
    return await support.add_active_time(database, order_id, body, idempotency_key)


@router.get("/internal/orders/{order_id}/metrics", response_model=OrderMetrics)
async def metrics(order_id: UUID, database: DB, owner: Owner) -> OrderMetrics:
    return await support.order_metrics(database, order_id)


@router.post(
    "/internal/orders/{order_id}/retries",
    response_model=RetryQueued,
    status_code=202,
    dependencies=[Depends(owner_write)],
)
async def retry(
    order_id: UUID,
    body: RetryInput,
    database: DB,
    idempotency_key: str = Header(min_length=1, max_length=128),
) -> RetryQueued:
    return await support.queue_retry(database, order_id, body, idempotency_key)
