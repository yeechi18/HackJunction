from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from company.schemas.listings import ListingIssue


class BuyerQuestionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    status: Literal["supported", "missing_evidence", "unanswered", "not_applicable"]
    reason: str
    fact_ids: list[str] = Field(default_factory=list)


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[ListingIssue] = Field(default_factory=list, max_length=100)
    buyer_question_coverage: list[BuyerQuestionCoverage] = Field(
        default_factory=list, max_length=30
    )


class ReviewResult(ReviewDraft):
    text_passed: bool
    scope: Literal["text_review_only", "text_and_images"]
    requires_images: bool
    ruleset: Literal["internal-demo-v2"] = "internal-demo-v2"
    usage: dict[str, int]
    model: str
    elapsed_seconds: float
