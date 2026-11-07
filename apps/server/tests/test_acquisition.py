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
async def test_optional_specs_do_not_block_owner_review():
    order = complete_input()
    order["facts"] = [fact for fact in order["facts"] if fact["key"] != "dimensions"]
    order["facts"][0]["confirmed"] = False
    provider = StubProvider()

    result = await inspect_intake(order, provider)

    assert result.ready_for_acceptance is True
    assert result.missing_fields == []
    assert result.questions == []
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

    assert result.ready_for_acceptance is True
    assert result.missing_fields == []
    assert result.conflicts[0].key == "pack_quantity"
    assert result.conflicts[0].values == ["6", "4"]
    assert result.questions == []  # Differences are recorded without canned question lists.
    assert order == before


@pytest.mark.asyncio
async def test_reference_information_cannot_satisfy_confirmed_product_facts():
    order = complete_input()
    order["facts"][0]["source_kind"] = "competitor"
    order["facts"][1]["source_kind"] = "image_observation"

    result = await inspect_intake(order, StubProvider())

    assert result.ready_for_acceptance is True
    assert result.missing_fields == []
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
    assert result.questions == provider.answer["questions"]
    assert result.summary == provider.answer["summary"]
    assert result.elapsed_seconds == 0.25
    assert len(provider.calls) == 1
    assert "不要再机械索取" in provider.calls[0][0][0]["content"]


async def test_jersey_description_with_actual_image_produces_brief_without_fixed_questions(
    tmp_path,
):
    from PIL import Image

    image = tmp_path / "jersey.png"
    Image.new("RGB", (50, 50), "lightblue").save(image)
    provider = StubProvider(
        summary="可以围绕蓝白配色和球迷观赛场景展开，文案保持友好。是否官方授权仍无法仅凭图片判断。",
        assessment="uncertain",
        image_observations=["参考图可见浅蓝配色。"],
        content_brief={
            "product_type": "球衣",
            "audience": "关注阿根廷队的球迷",
            "selling_points": ["蓝白配色", "球迷观赛场景"],
            "english_title": "Blue and White Football Jersey",
        },
        source_facts=[
            {
                "key": "color",
                "value": "蓝白",
                "source": "product_details",
                "source_quote": "蓝白，梅西，世界杯",
            }
        ],
    )
    result = await inspect_intake(
        {
            "product_name": "梅西主题球衣",
            "product_details": "蓝白，梅西，世界杯",
            "facts": [],
            "page_observation": {"url": "https://example.com/a", "status": "restricted"},
        },
        provider,
        image_paths=[image],
    )
    assert result.ready_for_acceptance and result.missing_fields == [] and result.questions == []
    assert result.content_brief.product_type == "球衣" and result.image_observations
    assert result.page_observation.status == "restricted"
    content = provider.calls[0][0][-1]["content"]
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


async def test_no_attached_image_cannot_report_visual_observations_or_invent_page_status():
    result = await inspect_intake(
        {
            "product_name": "球衣",
            "facts": [],
            "page_observation": {"url": "https://example.com/a", "status": "unavailable"},
        },
        StubProvider(
            image_observations=["我看到了图片"],
            page_observation={
                "url": "https://example.com/a",
                "status": "ok",
                "excerpt": "invented",
            },
        ),
    )
    assert result.image_observations == [] and result.page_observation.status == "unavailable"
