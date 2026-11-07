from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from company.api.dependencies import DB, Configuration
from company.persistence.models import Message, OrderEvent
from company.schemas.orders import (
    CreatedOrder,
    CreateOrder,
    EventList,
    EventView,
    MessageInput,
    MessageList,
    MessageView,
    OrderSummary,
    SupplementInput,
)
from company.services import orders
from company.services.access import (
    OWNER_COOKIE,
    SELLER_COOKIE,
    check_csrf,
    check_origin,
    get_session,
)

router = APIRouter(prefix="/orders", tags=["orders"])


async def read_access(request: Request, database: DB, order_id: UUID) -> str:
    owner_token = request.cookies.get(OWNER_COOKIE)
    if owner_token:
        try:
            await get_session(database, owner_token, "owner")
            return "owner"
        except HTTPException as error:
            if error.status_code != 401:
                raise
    await get_session(database, request.cookies.get(SELLER_COOKIE), "seller", order_id)
    return "seller"


async def seller_write(
    request: Request, database: DB, settings: Configuration, order_id: UUID
) -> None:
    token = request.cookies.get(SELLER_COOKIE)
    await get_session(database, token, "seller", order_id)
    check_csrf(request, settings, token)


@router.post("", response_model=CreatedOrder, status_code=201)
async def create(
    body: CreateOrder, request: Request, database: DB, settings: Configuration
) -> CreatedOrder:
    check_origin(request, settings)
    return await orders.create_order(database, settings, body)


@router.get("/{order_id}", response_model=OrderSummary)
async def get_order(order_id: UUID, request: Request, database: DB) -> OrderSummary:
    await read_access(request, database, order_id)
    return await orders.summary(database, await orders.load_order(database, order_id))


@router.get("/{order_id}/messages", response_model=MessageList)
async def messages(order_id: UUID, request: Request, database: DB) -> MessageList:
    await read_access(request, database, order_id)
    rows = (
        await database.scalars(
            select(Message)
            .where(Message.order_id == order_id)
            .order_by(Message.created_at, Message.id)
            .limit(200)
        )
    ).all()
    return MessageList(
        items=[
            MessageView(id=row.id, role=row.role, content=row.content, created_at=row.created_at)
            for row in rows
        ]
    )


@router.post("/{order_id}/messages", response_model=MessageView, status_code=201)
async def add_message(
    order_id: UUID,
    body: MessageInput,
    request: Request,
    database: DB,
    settings: Configuration,
) -> MessageView:
    await seller_write(request, database, settings, order_id)
    return await orders.add_message(database, order_id, body)


@router.post("/{order_id}/supplements", response_model=OrderSummary)
async def supplement(
    order_id: UUID,
    body: SupplementInput,
    request: Request,
    database: DB,
    settings: Configuration,
) -> OrderSummary:
    await seller_write(request, database, settings, order_id)
    return await orders.add_supplement(database, order_id, body)


@router.get("/{order_id}/events", response_model=EventList)
async def events(
    order_id: UUID,
    request: Request,
    database: DB,
    after: Annotated[int, Query(ge=0)] = 0,
) -> EventList:
    principal = await read_access(request, database, order_id)
    query = select(OrderEvent).where(OrderEvent.order_id == order_id, OrderEvent.id > after)
    if principal == "seller":
        query = query.where(OrderEvent.visibility == "seller")
    rows = (await database.scalars(query.order_by(OrderEvent.id).limit(200))).all()
    return EventList(
        items=[
            EventView(id=row.id, kind=row.kind, message=row.message, created_at=row.created_at)
            for row in rows
        ]
    )
