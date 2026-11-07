from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from company.schemas.orders import InputModel, MessageView


class SupportRequestInput(InputModel):
    content: str = Field(default="请人工协助处理本订单。", min_length=1, max_length=10000)


class SupportReplyInput(InputModel):
    content: str = Field(min_length=1, max_length=10000)
    resolve: bool = False


class SupportTicketView(BaseModel):
    id: UUID
    order_id: UUID
    status: str
    requested_phase: str
    created_at: datetime
    resolved_at: datetime | None
    ai_reply_paused: bool
    messages: list[MessageView]


class SupportTicketList(BaseModel):
    items: list[SupportTicketView]
    ai_reply_paused: bool | None = None


class ActiveTimeInput(InputModel):
    seconds: int = Field(strict=True, ge=1, le=86400)
    reason: str = Field(min_length=1, max_length=2000)


class ActiveTimeView(BaseModel):
    id: UUID
    seconds: int
    reason: str
    created_at: datetime
    owner_active_seconds: int


class RetryInput(InputModel):
    command_id: UUID
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="负责人授权恢复当前技术失败任务。", min_length=1, max_length=2000)


class RetryQueued(BaseModel):
    retry_id: UUID
    command_id: UUID
    job_id: UUID
    revision: int
    status: Literal["queued"] = "queued"


class TokenTotals(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class OrderMetrics(BaseModel):
    order_id: UUID
    is_demo: bool
    publish_mode: str
    recorded_model_calls: int
    calls_by_role: dict[str, int]
    model_mode: Literal["none", "live", "offline", "mixed", "unknown"]
    calls_by_mode: dict[str, int]
    tokens_by_mode: dict[str, TokenTotals]
    attempted_steps: int
    steps_without_reported_usage: int
    tokens: TokenTotals
    known_cost_usd: Decimal | None
    total_cost_usd: Decimal | None
    versions: int
    revision_candidates: int
    automatic_rework_candidates: int
    failed_reviews: int
    feedback_requests: int
    owner_active_seconds: int
    open_support_tickets: int
    explanations: dict[str, str]
