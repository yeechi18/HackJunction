from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select

from company.api.dependencies import DB, Owner, owner_write
from company.persistence.models import Order
from company.schemas.commands import DecisionInput, QueuedCommand
from company.schemas.orders import InternalOrder, InternalOrderList
from company.services import orders

router = APIRouter(prefix="/internal/orders", tags=["internal orders"])


@router.get("", response_model=InternalOrderList)
async def list_orders(
    database: DB, owner: Owner, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> InternalOrderList:
    rows = (
        await database.scalars(select(Order).order_by(Order.created_at.desc()).limit(limit))
    ).all()
    return InternalOrderList(
        items=[await orders.summary(database, row, internal=True) for row in rows]
    )


@router.get("/{order_id}", response_model=InternalOrder)
async def detail(order_id: UUID, database: DB, owner: Owner) -> InternalOrder:
    return await orders.internal_detail(database, await orders.load_order(database, order_id))


@router.post(
    "/{order_id}/decisions",
    response_model=QueuedCommand,
    status_code=202,
    dependencies=[Depends(owner_write)],
)
async def decide(
    order_id: UUID,
    body: DecisionInput,
    database: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> QueuedCommand:
    if idempotency_key.startswith("system:"):
        raise HTTPException(422, "Idempotency keys starting with system: are reserved.")
    return await orders.enqueue_decision(database, order_id, body, idempotency_key)
