"""Production-agent invariants with deterministic provider stubs; no remote model calls."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from company.agents.generation import generate_listing
from company.agents.research import research_product
from company.agents.review import program_review, review_listing
from company.providers.mimo import ProviderError
from company.schemas.listings import ListingDraft


class StubProvider:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def generate_json(self, schema, messages, **kwargs):
        self.calls.append((schema, messages, kwargs))
        return schema.model_validate(self.answer), SimpleNamespace(
            usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            model="actual-stub-model",
            elapsed_seconds=0.125,
        )


def context():
    return {
        "product_name": "桌面理线夹",
        "is_demo": True,
        "confirmed_facts": [
            {"id": "count", "key": "pack_quantity", "value": "4", "unit": None},
            {"id": "diameter", "key": "cable_diameter", "value": "6", "unit": "mm"},
            {"id": "material", "key": "material", "value": "silicone", "unit": None},
            {"id": "scope", "key": "use_cases", "value": "indoor desk cables", "unit": None},
        ],
        "evidence": [
            {
                "id": "market-a",
                "kind": "manual_observation",
                "content": "Desk cable clips",
                "source_url": "https://example.invalid/demo-a",
                "observed_at": "2026-11-07",
                "price": "8.99",
                "currency": "USD",
                "is_demo": True,
            },
            {
                "id": "market-b",
                "kind": "manual_observation",
                "content": "Desk cable clips",
                "source_url": "https://example.invalid/demo-b",
                "observed_at": "2026-11-07",
                "price": "11.99",
                "currency": "USD",
                "is_demo": True,
            },
        ],
    }


def draft():
    return {
        "title": "4-Pack Silicone Cable Clips",
        "bullets": [
            "For cables up to 6 mm in diameter.",
            "Organize your desk.",
            "Keep cables within reach.",
            "Designed for indoor use.",
            "Check your cable size before use.",
        ],
        "description": "Keep desktop cables in place.",
        "faqs": [{"question": "How many clips?", "answer": "A pack of 4 clips."}],
        "claims": [
            {
                "field_path": "title",
                "text": "4-Pack Silicone Cable Clips",
                "fact_ids": ["count", "material"],
            },
            {"field_path": "bullets[0]", "text": "6 mm", "fact_ids": ["diameter"]},
            {"field_path": "faqs[0].answer", "text": "4 clips", "fact_ids": ["count"]},
        ],
        "image_plan": [
            {
                "purpose": "main",
                "layout": "Use the actual seller reference on a plain background.",
                "text_overlay": "",
                "fact_ids": ["count", "material"],
            }
        ],
    }


def semantic_pass():
    return {"passed": True, "issues": [], "buyer_question_coverage": []}


def research_answer():
    return {
        "buyer_questions": [
            {
                "question": "How many clips are supplied?",
                "applicable": True,
                "evidence_ids": [],
                "missing_facts": [],
                "follow_up_questions": [],
                "target_fields": ["title", "faqs"],
            }
        ],
        "market_notes": ["Use supplied observations only; verify comparability."],
        "missing_facts": [],
        "follow_up_questions": [],
    }


async def test_research_prices_are_from_supplied_evidence_and_metadata_from_provider():
    source = context()
    before = deepcopy(source)
    provider = StubProvider(research_answer())
    result = await research_product(source, provider)
    assert str(result.suggested_listing_price_range.low) == "8.99"
    assert str(result.suggested_listing_price_range.high) == "11.99"
    assert result.suggested_listing_price_range.evidence_ids == ["market-a", "market-b"]
    assert result.suggested_listing_price_range.is_demo
    assert result.usage["total_tokens"] == 30
    assert result.model == "actual-stub-model" and result.elapsed_seconds == 0.125
    assert source == before and len(provider.calls) == 1


async def test_research_without_price_evidence_returns_null_not_a_guessed_range():
    source = context()
    source["evidence"] = []
    result = await research_product(source, StubProvider(research_answer()))
    assert result.suggested_listing_price_range is None
    assert "缺少" in result.pricing_notes
    answer = research_answer()
    answer["buyer_questions"][0]["evidence_ids"] = ["invented-source"]
    with pytest.raises(ProviderError, match="not supplied"):
        await research_product(source, StubProvider(answer))


async def test_research_keeps_known_fact_references_separate_from_market_evidence():
    source = context()
    answer = research_answer()
    answer["buyer_questions"][0]["evidence_ids"] = ["count", "market-a"]
    result = await research_product(context(), StubProvider(answer))
    assert result.buyer_questions[0].fact_ids == ["count"]
    assert result.buyer_questions[0].evidence_ids == ["market-a"]
    answer = research_answer()
    answer["market_notes"] = ["The market price is $999.00."]
    with pytest.raises(ProviderError, match="price absent"):
        await research_product(source, StubProvider(answer))


def test_questions_and_layout_instructions_are_not_pack_or_certification_assertions():
    value = draft()
    value["faqs"][0]["question"] = "Is one clip certified?"
    value["image_plan"][0]["layout"] = "Show one clip beside the package in the layout."
    issues = program_review(context(), ListingDraft.model_validate(value))
    assert not any(item.field_path.endswith((".question", ".layout")) for item in issues)
    value["description"] = "Only $9.99, based on our competitor observations."
    issues = program_review(context(), ListingDraft.model_validate(value))
    assert any(item.rule_id == "unsupported_listing_price" for item in issues)


async def test_demo_and_failed_sources_do_not_support_real_market_prices():
    source = context()
    source["is_demo"] = False
    result = await research_product(source, StubProvider(research_answer()))
    assert result.suggested_listing_price_range is None
    source["is_demo"] = True
    for evidence in source["evidence"]:
        evidence["status"] = "failed"
    result = await research_product(source, StubProvider(research_answer()))
    assert result.suggested_listing_price_range is None


async def test_generation_keeps_six_mm_when_six_pack_is_wrong_for_four_pack():
    source = context()
    output = draft()
    output["title"] = "6-Pack Silicone Cable Clips"
    output["claims"][0]["text"] = output["title"]
    original = deepcopy(output)
    result = await generate_listing(source, StubProvider(output))
    assert result.title == original["title"]  # Review issues, never a global numeric replacement.
    assert result.bullets[0] == original["bullets"][0] and "6 mm" in result.bullets[0]
    assert any(item.rule_id == "quantity_mismatch" for item in result.program_issues)
    assert not any(item.rule_id == "dimension_mismatch" for item in result.program_issues)
    assert result.usage["total_tokens"] == 30


async def test_semantic_pass_cannot_override_unsupported_new_claims():
    source = context()
    output = draft()
    output["description"] = "Waterproof, FDA certified and fits all cables."
    output["claims"].append(
        {"field_path": "description", "text": output["description"], "fact_ids": ["material"]}
    )
    result = await review_listing({**source, "draft": output}, StubProvider(semantic_pass()))
    assert result.text_passed is False and result.passed is False
    rules = {item.rule_id for item in result.issues}
    assert {
        "unsupported_waterproof",
        "unsupported_certification",
        "unsupported_universal_compatibility",
    } <= rules


async def test_text_only_pass_never_claims_actual_images_or_complete_delivery_pass():
    provider = StubProvider(semantic_pass())
    result = await review_listing({**context(), "draft": draft()}, provider)
    assert result.text_passed and result.scope == "text_review_only"
    assert result.passed is False and result.requires_images is True
    assert result.issues == []
    assert result.model == "actual-stub-model"


def test_unknown_ids_field_location_and_dimension_combination_are_checked():
    source = context()
    output = draft()
    output["claims"][0]["fact_ids"] = ["competitor-fact-id"]
    output["claims"].append({"field_path": "hidden", "text": "text", "fact_ids": ["material"]})
    output["claims"][1]["text"] = "This sentence does not appear in that bullet."
    issues = program_review(source, ListingDraft.model_validate(output))
    assert {"unknown_fact_id", "invalid_field_path", "claim_text_mismatch"} <= {
        item.rule_id for item in issues
    }
    source["confirmed_facts"].append(
        {"id": "size", "key": "dimensions", "value": "25 x 25 x 15", "unit": "mm"}
    )
    output = draft()
    output["description"] = "Dimensions: 26 x 25 x 15 mm."
    output["claims"].append(
        {"field_path": "description", "text": "26 x 25 x 15 mm", "fact_ids": ["size"]}
    )
    assert any(
        item.rule_id == "dimension_mismatch"
        for item in program_review(source, ListingDraft.model_validate(output))
    )


async def test_coverage_supported_label_needs_real_fact_ids():
    answer = semantic_pass()
    answer["buyer_question_coverage"] = [
        {
            "question": "Is it waterproof?",
            "status": "supported",
            "reason": "The model said so.",
            "fact_ids": ["invented-fact"],
        }
    ]
    result = await review_listing({**context(), "draft": draft()}, StubProvider(answer))
    assert not result.text_passed
    assert result.buyer_question_coverage[0].status == "missing_evidence"


def test_legacy_quantity_key_keeps_fact_id_and_does_not_change_dimensions():
    source = context()
    source["confirmed_facts"][0]["key"] = "package_quantity"
    assert program_review(source, ListingDraft.model_validate(draft())) == []
