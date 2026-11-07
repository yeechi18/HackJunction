"""Evidence-based initial analysis, independent of fixed product attribute checklists."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    unit: str | None = Field(default=None, max_length=100)
    source: Literal[
        "product_name", "product_details", "seller_notes", "seller_message", "product_page"
    ]
    source_quote: str = Field(min_length=1, max_length=2000)


class IntakeIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["warning", "concern"]
    description: str = Field(min_length=1, max_length=1000)
    basis: str = Field(min_length=1, max_length=1000)


class ContentBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_type: str = Field(default="", max_length=200)
    audience: str = Field(default="", max_length=500)
    selling_points: list[str] = Field(default_factory=list, max_length=6)
    english_title: str = Field(default="", max_length=300)
    english_description: str = Field(default="", max_length=1500)


class PageObservation(BaseModel):
    url: str
    status: str
    title: str = ""
    excerpt: str = ""
    retrieved_at: str = ""


class IntakeAnalysis(BaseModel):
    summary: str = Field(min_length=1, max_length=1200)
    assessment: Literal["consistent", "uncertain", "inconsistent"] = "uncertain"
    issues: list[IntakeIssue] = Field(default_factory=list, max_length=10)
    image_observations: list[str] = Field(default_factory=list, max_length=6)
    source_facts: list[SourceFact] = Field(default_factory=list, max_length=30)
    content_brief: ContentBrief = Field(default_factory=ContentBrief)
    page_observation: PageObservation | None = None
    questions: list[str] = Field(default_factory=list, max_length=2)
    strategy_version: str = "evidence-intake-v1"
