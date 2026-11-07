"""Confirmation and publication DTOs; currency amounts are canonical decimal strings."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from company.schemas.commands import QueuedCommand
from company.schemas.orders import InputModel, ProductFact


class ConfirmationInput(InputModel):
    expected_revision: int = Field(ge=1)
    version_id: UUID
    listing_price: str = Field(pattern=r"^(?:0|[1-9]\d{0,5})\.\d{2}$")
    currency: Literal["USD"] = "USD"
    seller_sku: str = Field(min_length=1, max_length=100)

    @field_validator("listing_price")
    @classmethod
    def positive_price(cls, value: str) -> str:
        if value == "0.00":
            raise ValueError("Listing price must be positive.")
        return value


class ConfirmationQueued(QueuedCommand):
    confirmation_id: UUID


class FeedbackInput(InputModel):
    expected_revision: int = Field(ge=1)
    version_id: UUID
    content: str = Field(min_length=1, max_length=10000)
    fact_updates: list[ProductFact] | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("fact_updates")
    @classmethod
    def facts_must_be_explicitly_confirmed(cls, value):
        if value is not None and any(fact.confirmed is not True for fact in value):
            raise ValueError("Every submitted fact must be explicitly confirmed by the seller.")
        return value


class FeedbackQueued(QueuedCommand):
    feedback_id: UUID


class PublishPlan(InputModel):
    version_id: UUID
    confirmation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    delivery_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    listing_price: str = Field(pattern=r"^(?:0|[1-9]\d{0,5})\.\d{2}$")
    currency: Literal["USD"]
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace: Literal["AMAZON_US"]


class PublishingResult(PublishPlan):
    usage: dict[str, int]
    model: str
    elapsed_seconds: float


class PublishResult(BaseModel):
    confirmation_id: UUID
    attempt_id: UUID
    mode: str
    status: str
    listing_url: str | None
    cache_hit: bool = False


class ConfirmationView(BaseModel):
    id: UUID
    version_id: UUID
    listing_price: str
    currency: str
    seller_sku: str
    snapshot_hash: str
    valid: bool
    confirmed_at: datetime


class AttemptView(BaseModel):
    id: UUID
    status: str
    submit_count: int
    updated_at: datetime


class PublicationView(BaseModel):
    mode: str
    status: str
    listing_url: str | None
    notice: str
    confirmation: ConfirmationView | None
    attempt: AttemptView | None
