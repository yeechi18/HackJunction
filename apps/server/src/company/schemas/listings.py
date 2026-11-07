"""Internal demo field limits; these are not Amazon policy or acceptance guarantees."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ListingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    severity: Literal["blocking", "warning"]
    reason: str
    fact_ids: list[str] = Field(default_factory=list)
    suggested_fix: str
    rule_id: str = "semantic"


class FAQ(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=1500)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)
    fact_ids: list[str] = Field(min_length=1, max_length=20)


class ImagePanel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["main", "infographic", "detail"]
    layout: str = Field(min_length=1, max_length=2000)
    text_overlay: str = Field(default="", max_length=1000)
    fact_ids: list[str] = Field(default_factory=list, max_length=20)


class ListingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    bullets: list[str] = Field(min_length=5, max_length=5)
    description: str = Field(min_length=1, max_length=10000)
    faqs: list[FAQ] = Field(default_factory=list, max_length=15)
    claims: list[Claim] = Field(default_factory=list, max_length=100)
    image_plan: list[ImagePanel] = Field(min_length=1, max_length=8)


class GenerationResult(ListingDraft):
    program_issues: list[ListingIssue]
    usage: dict[str, int]
    model: str
    elapsed_seconds: float
