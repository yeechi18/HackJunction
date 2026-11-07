from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from company.domain.facts import canonical_key


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductFact(InputModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    unit: str | None = Field(default=None, max_length=100)
    confirmed: bool = False

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return canonical_key(value)


class SubmitOrder(InputModel):
    product_name: str = Field(min_length=1, max_length=300)
    product_details: str | None = Field(default=None, min_length=1, max_length=10000)
    domestic_product_url: str | None = Field(default=None, max_length=2000)
    facts: list[ProductFact] = Field(default_factory=list, max_length=100)
    notes: str | None = Field(default=None, max_length=10000)
    is_demo: bool = False

    @field_validator("domestic_product_url")
    @classmethod
    def safe_product_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        if any(ord(char) < 33 for char in value):
            raise ValueError("Supply a valid HTTP or HTTPS product URL.")
        url = HttpUrl(value)
        if url.username is not None or url.password is not None:
            raise ValueError("Product URLs cannot contain credentials.")
        return str(url)


class CreateOrder(SubmitOrder):
    """Internal creation input; public seller identity is resolved from the account."""

    seller_name: str = Field(min_length=1, max_length=200)
    contact: str | None = Field(default=None, max_length=500)


class CreatedOrder(BaseModel):
    id: UUID
    phase: str
    revision: int
    private_url: str


class OrderSummary(BaseModel):
    id: UUID
    phase: str
    revision: int
    seller_name: str
    product_name: str
    product_details: str | None = None
    domestic_product_url: str | None = None
    facts: list[ProductFact]
    notes: str | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    workflow_status: str = "queued"
    pending_command_id: UUID | None
    conflicting_fact_keys: list[str]
    ai_reply_paused: bool = False


class SupplementInput(InputModel):
    expected_revision: int = Field(ge=1)
    facts: list[ProductFact] | None = Field(
        default=None,
        max_length=100,
        description="Complete seller-selected fact snapshot; null keeps existing facts.",
    )
    notes: str | None = Field(default=None, max_length=10000)


class MessageInput(InputModel):
    expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=10000)


class MessageView(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageView]


class EventView(BaseModel):
    id: int
    kind: str
    message: str
    created_at: datetime


class EventList(BaseModel):
    items: list[EventView]


class SnapshotView(BaseModel):
    id: UUID
    revision: int
    facts: list[ProductFact]
    notes: str | None
    source: str
    created_at: datetime


class CommandView(BaseModel):
    id: UUID
    kind: str
    status: str
    expected_revision: int
    created_at: datetime


class DecisionView(BaseModel):
    id: UUID
    action: str
    status: str
    version_id: UUID | None
    command_id: UUID
    created_at: datetime


class AgentRunView(BaseModel):
    id: UUID
    role: str
    status: str
    input_revision: int
    model: str | None
    usage: dict[str, int]
    elapsed_seconds: float | None
    error_summary: str | None
    result: dict | None
    created_at: datetime
    finished_at: datetime | None = None


class InternalOrder(OrderSummary):
    contact: str | None
    input_snapshots: list[SnapshotView]
    commands: list[CommandView]
    decisions: list[DecisionView]
    agent_runs: list[AgentRunView] = Field(default_factory=list)


class InternalOrderList(BaseModel):
    items: list[OrderSummary]
