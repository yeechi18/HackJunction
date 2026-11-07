from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from company.agents.intake_chat import IntakeChatInput, IntakeDraft, collect_intake
from company.agents.product_export import MAX_EXPORT_BYTES, read_product_export
from company.api.dependencies import DB, Configuration
from company.persistence.models import Message, OrderEvent
from company.providers.mimo import ProviderError
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


@router.post("/intake-chat", response_model=IntakeDraft)
async def intake_chat(
    request: Request,
    settings: Configuration,
    payload: Annotated[str, Form(max_length=40000)],
    file: UploadFile | None = None,
) -> IntakeDraft:
    check_origin(request, settings)
    # This endpoint precedes order creation, so the per-order model budget cannot apply.
    attempts = request.app.state.intake_chat_attempts
    now = monotonic()
    while attempts and attempts[0] <= now - 60:
        attempts.popleft()
    if len(attempts) >= 20:
        raise HTTPException(status_code=429, detail="Intake chat is busy.")
    attempts.append(now)
    try:
        body = IntakeChatInput.model_validate_json(payload)
        export = ""
        if file:
            data = await file.read(MAX_EXPORT_BYTES + 1)
            if len(data) > MAX_EXPORT_BYTES:
                raise HTTPException(status_code=413, detail="Product export is too large.")
            export = await run_in_threadpool(read_product_export, file.filename or "", data)
        if not body.message.strip() and not export:
            raise ValueError("Missing message or file")
        return await collect_intake(body, settings, export)
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid intake message or export.") from None
    except ProviderError:
        raise HTTPException(status_code=503, detail="Intake assistant is unavailable.") from None


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
