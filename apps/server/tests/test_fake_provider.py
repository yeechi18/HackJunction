"""Offline fixtures are explicit test infrastructure and never a live-model fallback."""

from uuid import uuid4

import pytest
from pydantic import BaseModel

from company.agents.acquisition import inspect_intake
from company.agents.feedback import inspect_feedback
from company.agents.generation import generate_listing
from company.agents.publishing import prepare_publish_plan
from company.agents.research import research_product
from company.agents.review import review_listing
from company.config import Settings
from company.providers import factory
from company.providers.factory import build_provider
from company.providers.fake import FakeProvider
from company.providers.mimo import ProviderError
from company.services import model_budget
from company.services.model_budget import BudgetedProvider
from company.services.support import usage_by_mode


def fake_settings():
    return Settings(_env_file=None, app_env="test", mimo_mode="fake", mimo_api_key=None)


def facts():
    return [
        {"id": "count", "key": "pack_quantity", "value": "4", "confirmed": True},
        {"id": "material", "key": "material", "value": "silicone", "confirmed": True},
        {
            "id": "size",
            "key": "dimensions",
            "value": "25 x 25 x 15",
            "unit": "mm",
            "confirmed": True,
        },
        {"id": "diameter", "key": "cable_diameter", "value": "6", "unit": "mm", "confirmed": True},
        {"id": "scope", "key": "use_cases", "value": "indoor desk cables", "confirmed": True},
    ]


def context():
    return {
        "product_name": "虚构桌面理线夹",
        "is_demo": True,
        "confirmed_facts": facts(),
        "evidence": [],
    }


def test_fake_requires_both_explicit_test_switches_even_if_settings_validation_is_bypassed():
    configuration = fake_settings()
    assert isinstance(build_provider(configuration), FakeProvider)
    for invalid in [
        configuration.model_copy(update={"app_env": "production"}),
        configuration.model_copy(update={"app_env": "development"}),
        configuration.model_copy(update={"mimo_mode": "live"}),
    ]:
        with pytest.raises(ProviderError):
            FakeProvider(invalid)
    with pytest.raises(ProviderError):
        build_provider(configuration.model_copy(update={"app_env": "production"}))


def test_missing_live_key_does_not_select_fixture():
    configuration = Settings(_env_file=None, app_env="test", mimo_mode="live", mimo_api_key=None)
    with pytest.raises(ProviderError, match="MIMO_API_KEY"):
        build_provider(configuration)


def test_live_provider_error_is_never_caught_to_fall_back(monkeypatch):
    def unavailable(settings):
        raise ProviderError("Injected live initialization failure")

    def forbidden_fixture(settings):
        raise AssertionError("Live errors cannot select a fixture")

    monkeypatch.setattr(factory, "MiMoProvider", unavailable)
    monkeypatch.setattr(factory, "FakeProvider", forbidden_fixture)
    with pytest.raises(ProviderError, match="live initialization"):
        build_provider(fake_settings().model_copy(update={"mimo_mode": "live"}))


async def test_fake_runs_current_agent_schemas_without_key_or_reported_api_usage(monkeypatch):
    def forbidden_live(*args, **kwargs):
        raise AssertionError("Offline fixture must not initialize a live client")

    monkeypatch.setattr(factory, "MiMoProvider", forbidden_live)
    provider = build_provider(fake_settings())
    provider.trace_context = {"order_id": str(uuid4()), "role": "research"}
    intake = await inspect_intake({"product_name": "虚构桌面理线夹", "facts": facts()}, provider)
    research = await research_product(context(), provider)
    generated = await generate_listing(context(), provider)
    review = await review_listing(
        {
            **context(),
            "draft": generated.model_dump(),
            "buyer_questions": [question.model_dump() for question in research.buyer_questions],
        },
        provider,
    )
    assert intake.ready_for_acceptance
    assert research.suggested_listing_price_range is None
    assert generated.title.startswith("4-Pack") and "6 mm" in generated.bullets[2]
    assert generated.program_issues == []
    assert review.text_passed and not review.passed
    for result in [intake, research, generated, review]:
        assert (
            result.model == "offline-fixture"
            and result.usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            and result.elapsed_seconds == 0
        )
    assert provider.trace_context["role"] == "research"
    await provider.close()
    assert provider.closed


@pytest.mark.parametrize(
    ("text", "updates", "category", "actionable"),
    [
        ("请把语气改得更简洁。", None, "style", True),
        ("包装数量改为4个装。", facts(), "fact", True),
        ("新增防水和FDA认证卖点。", None, "new_claim", False),
        ("请再增加第二个SKU。", None, "scope", False),
    ],
)
async def test_fake_feedback_categories_still_run_application_guards(
    text, updates, category, actionable
):
    provider = build_provider(fake_settings())
    result = await inspect_feedback(
        {
            "content": text,
            "fact_updates": updates,
            "product_name": "虚构理线夹",
            "facts": facts(),
            "base_facts": facts(),
            "is_demo": True,
        },
        provider,
    )
    assert result.category == category and result.actionable is actionable
    assert (
        result.usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        and result.model == "offline-fixture"
    )


async def test_fake_semantic_pass_cannot_bypass_program_review():
    provider = build_provider(fake_settings())
    generated = await generate_listing(context(), provider)
    draft = generated.model_dump()
    draft["description"] = "Waterproof and fits all cables."
    result = await review_listing({**context(), "draft": draft}, provider)
    assert not result.text_passed and not result.passed
    assert "unsupported_waterproof" in {issue.rule_id for issue in result.issues}


async def test_fake_publish_plan_echoes_frozen_version_hash_price_and_sku():
    provider = build_provider(fake_settings())
    frozen = {
        "version_id": str(uuid4()),
        "confirmation_hash": "c" * 64,
        "delivery_hash": "d" * 64,
        "listing_price": "12.99",
        "currency": "USD",
        "seller_sku": "OFFLINE-4",
        "marketplace": "AMAZON_US",
    }
    result = await prepare_publish_plan(frozen, provider)
    assert {
        key: value
        for key, value in result.model_dump(mode="json").items()
        if key not in {"usage", "model", "elapsed_seconds"}
    } == frozen
    assert result.usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


async def test_budget_wrapper_delegates_to_fake_without_requiring_a_live_key(monkeypatch):
    calls = []

    async def budget(database, settings, order_id, *, new_candidate=False):
        calls.append(new_candidate)

    monkeypatch.setattr(model_budget, "check_budget", budget)
    settings = fake_settings()
    wrapped = BudgetedProvider(build_provider(settings), None, settings, uuid4())
    result = await generate_listing(context(), wrapped)
    assert result.model == "offline-fixture" and calls == [True]
    await wrapped.close()


async def test_unknown_schema_is_explicitly_unsupported():
    class OtherSchema(BaseModel):
        unexpected: str

    provider = build_provider(fake_settings())
    with pytest.raises(ProviderError, match="no fixture"):
        await provider.generate_json(OtherSchema, [{"role": "user", "content": "{}"}])


def test_metrics_distinguish_offline_zero_tokens_from_unknown_live_usage():
    mode, calls, tokens = usage_by_mode(
        [
            {
                "model": "offline-fixture",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "_model_calls": 2,
                },
            },
            {"model": "mimo-v2.6-pro", "usage": {"_model_calls": 1}},
        ]
    )
    assert mode == "mixed" and calls == {"offline-fixture": 2, "live": 1}
    assert tokens["offline-fixture"].total_tokens == 0
    assert tokens["live"].total_tokens is None


def test_metrics_only_offline_are_explicit_and_keep_cumulative_call_count():
    mode, calls, tokens = usage_by_mode(
        [
            {
                "model": "offline-fixture",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "_model_calls": 3,
                },
            }
        ]
    )
    assert mode == "offline" and calls["offline-fixture"] == 3
    assert tokens["offline-fixture"].model_dump() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
