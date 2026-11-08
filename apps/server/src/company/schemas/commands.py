from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from company.schemas.orders import InputModel


class ExchangeInput(InputModel):
    order_id: UUID
    token: str = Field(min_length=20, max_length=200)


class LoginInput(InputModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


class SellerSessionView(BaseModel):
    order_id: UUID
    csrf_token: str
    expires_at: datetime


class OwnerSessionView(BaseModel):
    username: Literal["owner"] = "owner"
    csrf_token: str
    expires_at: datetime


class DecisionInput(InputModel):
    action: Literal["accept", "reject", "veto", "release"]
    expected_revision: int = Field(ge=1)
    version_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)
    override_review: bool = False
    review_token: str | None = Field(default=None, min_length=64, max_length=64)


class QueuedCommand(BaseModel):
    command_id: UUID
    status: Literal["queued"] = "queued"
    order_id: UUID
    revision: int
