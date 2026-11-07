"""Structured offline fixtures for tests. No HTTP clients, billed tokens or quality claims."""

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from company.config import Settings
from company.domain.facts import canonical_key
from company.providers.mimo import ModelResult, ProviderError

T = TypeVar("T", bound=BaseModel)


def context_from_messages(messages: list[dict]) -> dict:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = next(
                (block.get("text") for block in content if block.get("type") == "text"), None
            )
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            raise ProviderError(
                "Offline fixtures require a structured JSON user context."
            ) from None
        if not isinstance(payload, dict):
            raise ProviderError("Offline fixture context must be an object.")
        return payload
    raise ProviderError("Offline fixture call is missing its user context.")


def fact_map(context: dict) -> dict[str, dict]:
    return {
        canonical_key(fact["key"]): {**fact, "key": canonical_key(fact["key"])}
        for fact in context.get("confirmed_facts", [])
    }


def fact_text(fact: dict) -> str:
    value, unit = str(fact["value"]), fact.get("unit")
    return f"{value} {unit}" if unit and not value.lower().endswith(str(unit).lower()) else value


def listing_fixture(context: dict) -> dict:
    facts = fact_map(context)
    count, dimensions = facts.get("pack_quantity"), facts.get("dimensions")
    material, scope = facts.get("material"), facts.get("use_cases")
    diameter = facts.get("cable_diameter")
    numeric_count = re.search(r"\d+(?:\.\d+)?", str(count["value"])) if count else None
    title = (
        f"{numeric_count.group()}-Pack Desktop Organizer" if numeric_count else "Desktop Organizer"
    )
    claims = []
    if numeric_count:
        claims.append({"field_path": "title", "text": title, "fact_ids": [count["id"]]})

    def bullet(text: str, fact: dict | None, index: int) -> str:
        if fact:
            claims.append(
                {"field_path": f"bullets[{index}]", "text": text, "fact_ids": [fact["id"]]}
            )
        return text

    translated_material = {"硅胶": "silicone", "塑料": "plastic", "木": "wood", "竹": "bamboo"}
    material_value = (
        translated_material.get(str(material["value"]), str(material["value"]))
        if material
        else None
    )
    bullets = [
        bullet(
            f"Material: {material_value}." if material else "Refer to seller specifications.",
            material,
            0,
        ),
        bullet(
            f"Dimensions: {fact_text(dimensions)}."
            if dimensions
            else "Verify dimensions before use.",
            dimensions,
            1,
        ),
        bullet(
            f"Cable size reference: {fact_text(diameter)}."
            if diameter
            else "Check your workspace before use.",
            diameter,
            2,
        ),
        bullet(
            f"Package: {numeric_count.group()} pieces."
            if numeric_count
            else "Check the included contents.",
            count if numeric_count else None,
            3,
        ),
        bullet(
            f"Use scope: {fact_text(scope)}."
            if scope
            else "Keep these specifications for reference.",
            scope,
            4,
        ),
    ]
    faqs = []
    for question, answer, fact in [
        (
            "What is included?",
            f"Package: {numeric_count.group()} pieces." if numeric_count else None,
            count,
        ),
        (
            "What are the dimensions?",
            f"Dimensions: {fact_text(dimensions)}." if dimensions else None,
            dimensions,
        ),
    ]:
        if fact and answer:
            index = len(faqs)
            faqs.append({"question": question, "answer": answer})
            claims.append(
                {"field_path": f"faqs[{index}].answer", "text": answer, "fact_ids": [fact["id"]]}
            )
    overlay = "\n".join(
        text
        for text in [
            f"Package: {numeric_count.group()} pieces." if numeric_count else "",
            f"Dimensions: {fact_text(dimensions)}." if dimensions else "",
        ]
        if text
    )
    overlay_ids = [fact["id"] for fact in (count if numeric_count else None, dimensions) if fact]
    if overlay:
        claims.append(
            {"field_path": "image_plan[1].text_overlay", "text": overlay, "fact_ids": overlay_ids}
        )
    return {
        "title": title,
        "bullets": bullets,
        "description": (
            "Keep your workspace organized."
            if context.get("feedback")
            else "A desktop organizer described by the seller's confirmed specifications."
        ),
        "faqs": faqs,
        "claims": claims,
        "image_plan": [
            {
                "purpose": "main",
                "layout": "Preserve the actual supplied product reference.",
                "text_overlay": "",
                "fact_ids": [],
            },
            {
                "purpose": "infographic",
                "layout": "Place only confirmed specifications beside the reference.",
                "text_overlay": overlay,
                "fact_ids": overlay_ids,
            },
        ],
    }


def feedback_fixture(context: dict) -> dict:
    # The application still independently applies its production guards after this fixture.
    from company.agents.feedback import FACT_CHANGE, REMOVE, SCOPE, SENSITIVE

    text = context.get("content", "")
    if context.get("fact_updates") is not None:
        category = "fact"
    elif SCOPE.search(text):
        category = "scope"
    elif any(
        SENSITIVE.search(part) and not REMOVE.search(part) for part in re.split(r"[。；;\n]", text)
    ):
        category = "new_claim"
    elif FACT_CHANGE.search(text):
        category = "fact"
    else:
        category = "style"
    return {
        "category": category,
        "reason": "离线测试按显式输入分类，未调用外部模型。",
        "questions": [],
        "summary": "离线测试已记录反馈，后续动作仍由程序校验。",
    }


def research_fixture(context: dict) -> dict:
    facts = fact_map(context)
    questions = []
    for key, question, fields in [
        ("pack_quantity", "What quantity is included?", ["title", "faqs"]),
        ("dimensions", "What are the dimensions?", ["bullets", "faqs"]),
        ("use_cases", "What is the use scope?", ["bullets"]),
    ]:
        if key not in facts:
            continue
        questions.append(
            {
                "question": question,
                "applicable": True,
                "evidence_ids": [],
                "missing_facts": [] if key in facts else [key],
                "follow_up_questions": [] if key in facts else [f"Please confirm {key}."],
                "target_fields": fields,
            }
        )
    if not questions:
        fact = next(iter(facts.values()), None)
        questions.append(
            {
                "question": "What product is described?",
                "applicable": True,
                "fact_ids": [fact["id"]] if fact else [],
                "evidence_ids": [],
                "missing_facts": [],
                "follow_up_questions": [],
                "target_fields": ["description"],
            }
        )
    return {
        "buyer_questions": questions,
        "market_notes": ["Offline fixture: supplied observations are not a live market search."],
        "missing_facts": [],
        "follow_up_questions": [],
        "selling_point_plan": [
            {
                "request": context["seller_requirements"][:1000],
                "strategy": "style_only",
                "explanation": "离线fixture只记录表达偏好，不证明商品性能。",
                "fact_ids": [],
                "target_fields": ["description"],
            }
        ]
        if context.get("seller_requirements")
        else [],
    }


def review_fixture(context: dict) -> dict:
    facts = fact_map(context)
    coverage = []
    for item in context.get("buyer_questions", []):
        question = item.get("question", "")
        key = (
            "pack_quantity"
            if "quantity" in question.lower() or "included" in question.lower()
            else "dimensions"
            if "dimension" in question.lower()
            else "use_cases"
        )
        fact = facts.get(key)
        if item.get("fact_ids"):
            fact = next(
                (value for value in facts.values() if value["id"] in item["fact_ids"]), fact
            )
        supported = fact is not None and not item.get("missing_facts")
        coverage.append(
            {
                "question": question,
                "status": "not_applicable"
                if item.get("applicable") is False
                else "supported"
                if supported
                else "missing_evidence",
                "reason": "离线语义fixture；真实内容质量仍需真实模型及人工验收。",
                "fact_ids": [fact["id"]]
                if supported and item.get("applicable") is not False
                else [],
            }
        )
    return {"passed": True, "issues": [], "buyer_question_coverage": coverage}


class FakeProvider:
    def __init__(self, settings: Settings):
        if settings.app_env != "test" or settings.mimo_mode != "fake":
            raise ProviderError("Offline fixtures require APP_ENV=test and MIMO_MODE=fake.")
        self.settings = settings
        self.trace_context: dict[str, str] = {}
        self.calls: list[str] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def generate_json(
        self,
        schema: type[T],
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> tuple[T, ModelResult]:
        if self.closed:
            raise ProviderError("Offline fixture provider is closed.")
        context = context_from_messages(messages)
        name = schema.__name__
        if name in {"_IntakeDraft", "DialogueDraft"}:
            findings = context.get("program_findings", {})
            answer = {
                "ready_for_acceptance": findings.get("ready_for_acceptance", False),
                "missing_fields": findings.get("missing_fields", []),
                "conflicts": findings.get("conflicts", []),
                "questions": [],
                "summary": "我会根据现有资料整理内容方向，由负责人决定是否接单。",
                "assessment": "uncertain",
                "content_brief": {
                    "product_type": context.get("product_name", "商品"),
                    "audience": "对该商品有需求的买家",
                    "selling_points": ["围绕已有描述组织内容", "突出可见外观与使用场景"],
                    "english_title": "Product Listing",
                    "english_description": "Explore the product described by the seller.",
                },
                "source_facts": [
                    {
                        "key": match[0],
                        "value": match[1],
                        "unit": None,
                        "source": "product_details",
                        "source_quote": f"{match[0]}: {match[1]}",
                    }
                    for match in re.findall(
                        r"(?m)^(material|dimensions|pack_quantity|use_cases|color): ([^\n]+)$",
                        context.get("product_details") or "",
                    )
                ],
            }
        elif name == "IntakeDraft":
            answer = {
                "reply": "当前是离线测试模式。请在资料卡补充并核对商品资料。",
                "updates": {},
            }
        elif name == "ResearchDraft":
            answer = research_fixture(context)
        elif name == "ListingDraft":
            answer = listing_fixture(context)
        elif name == "ReviewDraft":
            answer = review_fixture(context)
        elif name in {"FeedbackDraft", "FeedbackClassification"}:
            answer = feedback_fixture(context)
        elif name == "PublishPlan":
            answer = {key: context[key] for key in schema.model_fields if key in context}
        else:
            raise ProviderError("Offline provider has no fixture for this application schema.")
        try:
            parsed = schema.model_validate(answer)
        except ValidationError:
            raise ProviderError(
                "Offline fixture does not match the current application schema."
            ) from None
        self.calls.append(name)
        content = parsed.model_dump_json()
        return parsed, ModelResult(
            content=content,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            elapsed_seconds=0,
            model="offline-fixture",
            message={"role": "assistant", "content": content},
        )
