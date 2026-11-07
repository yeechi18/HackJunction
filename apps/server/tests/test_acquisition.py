"""Intake correctness is independent of the language model's proposed verdict."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from company.agents.acquisition import inspect_intake


class StubProvider:
    def __init__(self, **overrides):
        self.answer = {
            "ready_for_acceptance": True,
            "missing_fields": [],
            "conflicts": [],
            "questions": [],
            "summary": "已整理商品资料，等待后续处理。",
            **overrides,
        }
        self.calls = []

    async def generate_json(self, schema, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return schema.model_validate(self.answer), SimpleNamespace(
            usage={"input_tokens": 17, "output_tokens": 9, "total_tokens": 26},
            model="test-model",
            elapsed_seconds=0.25,
        )


def complete_input():
    return {
        "product_name": "桌面理线夹",
        "facts": [
            {"key": "material", "value": "silicone", "confirmed": True},
            {"key": "dimensions", "value": "25 x 25 x 15", "unit": "mm", "confirmed": True},
            {"key": "pack_quantity", "value": "6", "confirmed": True},
            {"key": "use_cases", "value": "indoor desk cable organization", "confirmed": True},
        ],
        "notes": None,
        "messages": [],
    }


@pytest.mark.asyncio
async def test_missing_or_unconfirmed_facts_block_even_if_model_says_complete():
    order = complete_input()
    order["facts"] = [fact for fact in order["facts"] if fact["key"] != "dimensions"]
    order["facts"][0]["confirmed"] = False
    provider = StubProvider()

    result = await inspect_intake(order, provider)

    assert result.ready_for_acceptance is False
    assert result.missing_fields == ["material", "dimensions"]
    assert any("材质" in question for question in result.questions)
    assert any("尺寸" in question for question in result.questions)
    assert len(provider.calls) == 1
    assert result.usage["total_tokens"] == 26
    assert result.model == "test-model"


@pytest.mark.asyncio
async def test_conflicting_values_require_confirmation_and_never_mutate_facts():
    order = complete_input()
    order["facts"].append(
        {
            "key": "pack_quantity",
            "value": "4",
            "confirmed": False,
            "source_kind": "image_observation",
        }
    )
    before = deepcopy(order)

    result = await inspect_intake(order, StubProvider())

    assert result.ready_for_acceptance is False
    assert result.missing_fields == []
    assert result.conflicts[0].key == "pack_quantity"
    assert result.conflicts[0].values == ["6", "4"]
    assert any("6" in question and "4" in question for question in result.questions)
    assert order == before


@pytest.mark.asyncio
async def test_reference_information_cannot_satisfy_confirmed_product_facts():
    order = complete_input()
    order["facts"][0]["source_kind"] = "competitor"
    order["facts"][1]["source_kind"] = "image_observation"

    result = await inspect_intake(order, StubProvider())

    assert result.ready_for_acceptance is False
    assert result.missing_fields == ["material", "dimensions"]
    assert result.conflicts == []


@pytest.mark.asyncio
async def test_complete_input_calls_model_for_summary_but_ignores_false_missing_fields():
    order = complete_input()
    order["facts"][2]["key"] = "package_quantity"
    order["facts"][3]["key"] = "usage_scope"
    provider = StubProvider(
        ready_for_acceptance=False,
        missing_fields=["dimensions"],
        questions=["请再次提供已有尺寸。"],
        summary="商品资料已具备，下一步由负责人决定是否接单。",
    )

    result = await inspect_intake(order, provider)

    assert result.ready_for_acceptance is True
    assert result.missing_fields == []
    assert result.conflicts == []
    assert result.questions == []
    assert result.summary == provider.answer["summary"]
    assert result.elapsed_seconds == 0.25
    assert len(provider.calls) == 1
    assert "程序已提供" in provider.calls[0][0][0]["content"]
