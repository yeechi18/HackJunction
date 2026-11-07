"""Research contracts. Prices are observations from supplied evidence, never live scraping."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfirmedFact(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    unit: str | None = None
    provenance: Literal["seller_confirmed", "seller_provided"] = "seller_confirmed"
    source_quote: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def numeric_values_as_text(cls, value):
        return str(value) if isinstance(value, (int, float, Decimal)) else value


class ResearchQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    applicable: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    fact_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_facts: list[str] = Field(default_factory=list, max_length=20)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=10)
    target_fields: list[str] = Field(default_factory=list, max_length=20)


class SellingPointPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=1000)
    strategy: Literal["use_confirmed_fact", "style_only", "needs_confirmation"]
    explanation: str = Field(min_length=1, max_length=1500)
    fact_ids: list[str] = Field(default_factory=list, max_length=20)
    target_fields: list[str] = Field(default_factory=list, max_length=10)


class ResearchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer_questions: list[ResearchQuestion] = Field(min_length=1, max_length=20)
    market_notes: list[str] = Field(default_factory=list, max_length=20)
    missing_facts: list[str] = Field(default_factory=list, max_length=20)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=20)
    selling_point_plan: list[SellingPointPlan] = Field(default_factory=list, max_length=20)


class ObservedPriceRange(BaseModel):
    low: Decimal
    high: Decimal
    currency: str
    evidence_ids: list[str]
    is_demo: bool


class ResearchResult(ResearchDraft):
    suggested_listing_price_range: ObservedPriceRange | None
    pricing_notes: str
    usage: dict[str, int]
    model: str
    elapsed_seconds: float
