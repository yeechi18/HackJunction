"""Scoped support and explicit owner retry requests."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from company.api.dependencies import DB, Configuration, Owner, owner_write
from company.api.orders import read_access, seller_write
from company.persistence.models import Command, Message, OrderEvent
from company.schemas.support import (
    OrderMetrics,
    RetryInput,
    RetryQueued,
    SupportReplyInput,
    SupportRequestInput,
    SupportTicketList,
    SupportTicketView,
)
from company.services import support
from company.services.orders import load_order

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


# Owner-authored advice and issue acknowledgements do not change workflow approvals.


class IssueActionInput(BaseModel):
    action: Literal["ignore", "seller_advice"]
    expected_revision: int = Field(ge=1)
    command_id: UUID | None = None
    request_id: UUID
    content: str = Field(min_length=1, max_length=2000)


@router.post("/internal/orders/{order_id}/issue-actions", dependencies=[Depends(owner_write)])
async def issue_action(order_id: UUID, body: IssueActionInput, database: DB):
    order = await load_order(database, order_id, lock=True)
    payload = body.model_dump(mode="json")
    previous = await database.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order_id,
            OrderEvent.kind == "owner_issue_action",
            OrderEvent.payload["request_id"].astext == str(body.request_id),
        )
    )
    if previous:
        if previous.payload != payload:
            raise HTTPException(409, "此操作编号已用于其他内容，请重新提交。")
        return {"recorded": True}
    if order.revision != body.expected_revision:
        raise HTTPException(409, "商品资料已变化，请重新核对问题。")
    if not body.content.strip():
        raise HTTPException(422, "请填写处理说明。")
    if body.action == "ignore":
        command = await database.get(Command, body.command_id) if body.command_id else None
        if (
            not command
            or command.order_id != order_id
            or command.status != "failed"
            or command.expected_revision != order.revision
        ):
            raise HTTPException(409, "失败任务已变化，请重新读取运行日志。")
    if body.action == "seller_advice":
        database.add(
            Message(
                order_id=order_id,
                role="owner",
                input_revision=order.revision,
                content=body.content.strip(),
            )
        )
    database.add(
        OrderEvent(
            order_id=order_id,
            kind="owner_issue_action",
            visibility="internal",
            message="负责人已记录忽略提示（任务状态不变）。"
            if body.action == "ignore"
            else "负责人已向卖家发送处理建议。",
            payload=payload,
        )
    )
    await database.commit()
    return {"recorded": True}
