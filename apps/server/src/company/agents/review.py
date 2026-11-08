"""A bounded internal rule set plus MiMo semantics; neither is an Amazon policy guarantee."""

import asyncio
import json
import re
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

from company.agents.research import read_confirmed_facts
from company.providers.mimo import MiMoProvider
from company.schemas.listings import ListingDraft, ListingIssue
from company.schemas.research import ConfirmedFact
from company.schemas.reviews import BuyerQuestionCoverage, ReviewDraft, ReviewResult

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
NUMBER = r"\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS)
COUNT_PATTERN = re.compile(
    rf"\b({NUMBER})\s*[- ]?\s*(?:pack|pieces?|pcs|clips?|units?|bottles?|count)\b"
    rf"|\b(?:pack|set)\s+of\s+({NUMBER})\b",
    re.I,
)
UNIT = r"mm|cm|inches|inch|in|ml|l"
MEASURE_PATTERN = re.compile(rf"\b(\d+(?:\.\d+)?)\s*({UNIT})\b", re.I)
AXES_PATTERN = re.compile(rf"\b((?:\d+(?:\.\d+)?\s*[x×]\s*)+\d+(?:\.\d+)?)\s*({UNIT})\b", re.I)
SENSITIVE_PATTERNS = {
    "universal_compatibility": re.compile(
        r"\b(?:fits?|compatible\s+with|works?\s+with|suitable\s+for)\s+(?:all|any|every)\b"
        r"|\buniversal(?:ly)?\s+(?:fit|compatib\w*)|\ball\s+(?:cables|devices|models)\b",
        re.I,
    ),
    "waterproof": re.compile(r"\bwater[- ]?proof\b|\bIPX?\d{1,2}\b|防水", re.I),
    "certification": re.compile(
        r"\b(?:FDA|FCC|CE|UL|ISO)\b(?:\s+(?:approved|certified))?|\bcertified\b|认证", re.I
    ),
}


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def issue(path: str, rule: str, reason: str, fact_ids: list[str] | None = None) -> ListingIssue:
    return ListingIssue(
        field_path=path,
        severity="blocking",
        rule_id=rule,
        reason=reason,
        fact_ids=fact_ids or [],
        suggested_fix="按已确认事实及原始单位修改；新增声明需先补证据并由卖家确认。",
    )


def text_fields(draft: ListingDraft) -> dict[str, str]:
    fields = {"title": draft.title, "description": draft.description}
    fields.update({f"bullets[{i}]": value for i, value in enumerate(draft.bullets)})
    for i, faq in enumerate(draft.faqs):
        fields[f"faqs[{i}].question"] = faq.question
        fields[f"faqs[{i}].answer"] = faq.answer
    for i, panel in enumerate(draft.image_plan):
        fields[f"image_plan[{i}].text_overlay"] = panel.text_overlay
        fields[f"image_plan[{i}].layout"] = panel.layout
    return fields


def number(value: str) -> Decimal:
    return (
        Decimal(NUMBER_WORDS[value.casefold()])
        if value.casefold() in NUMBER_WORDS
        else Decimal(value)
    )


def unit_name(value: str) -> str:
    return "in" if value.casefold() in {"in", "inch", "inches"} else value.casefold()


def denied(text: str) -> bool:
    return bool(
        re.search(r"\b(?:not|no|false|none|untested|unverified)\b|未经|未认证|不防水", text, re.I)
    )


def supported_sensitive(category: str, fragment: str, facts: list[ConfirmedFact]) -> set[str]:
    supported = set()
    for fact in facts:
        if denied(fact.value):
            continue
        combined = f"{fact.key} {fact.value}"
        if not SENSITIVE_PATTERNS[category].search(combined):
            continue
        if category == "certification":
            named = re.findall(r"\b(?:FDA|FCC|CE|UL|ISO)\b", fragment, re.I)
            if named and any(not re.search(rf"\b{token}\b", fact.value, re.I) for token in named):
                continue
        if category == "waterproof" and re.search(r"\bIP", fragment, re.I):
            if fragment.casefold() not in combined.casefold():
                continue
        supported.add(fact.id)
    return supported


def program_review(context: dict[str, Any], draft: ListingDraft) -> list[ListingIssue]:
    """Read-only checks. Never globally replace a number or rewrite the submitted version."""
    facts = read_confirmed_facts(context)
    known = {fact.id for fact in facts}
    fields = text_fields(draft)
    issues: list[ListingIssue] = []
    references: dict[str, set[str]] = {}
    for claim in draft.claims:
        references.setdefault(claim.field_path, set()).update(claim.fact_ids)
        if set(claim.fact_ids) - known:
            issues.append(issue(claim.field_path, "unknown_fact_id", "声明引用了未确认事实ID。"))
        if claim.field_path not in fields:
            issues.append(issue(claim.field_path, "invalid_field_path", "声明对应字段不存在。"))
        elif normalize(claim.text) not in normalize(fields[claim.field_path]):
            issues.append(
                issue(claim.field_path, "claim_text_mismatch", "引用声明未出现在所指字段。")
            )
    for i, panel in enumerate(draft.image_plan):
        if set(panel.fact_ids) - known:
            issues.append(
                issue(f"image_plan[{i}]", "unknown_fact_id", "图片计划引用了未知事实ID。")
            )
        references.setdefault(f"image_plan[{i}].text_overlay", set()).update(panel.fact_ids)
        references.setdefault(f"image_plan[{i}].layout", set()).update(panel.fact_ids)
    for i, bullet in enumerate(draft.bullets):
        if not bullet.strip() or len(bullet) > 1000:
            issues.append(
                issue(f"bullets[{i}]", "internal_field_limit", "五点字段为空或超出演示预算。")
            )

    quantity_facts = [fact for fact in facts if fact.key == "pack_quantity"]
    quantities = {
        number(match.group())
        for fact in quantity_facts
        if (match := re.search(r"\d+(?:\.\d+)?", fact.value))
    }
    if len(quantities) > 1:
        issues.append(issue("confirmed_facts", "conflicting_quantity", "已确认包装数量仍有冲突。"))
    quantity_ids = {fact.id for fact in quantity_facts}
    measurements: dict[tuple[Decimal, str], set[str]] = {}
    axes: set[tuple[tuple[Decimal, ...], str]] = set()
    for fact in facts:
        if fact.key == "pack_quantity":
            continue
        known_unit = fact.unit or (
            match.group(1) if (match := re.search(rf"\b({UNIT})\b", fact.value, re.I)) else ""
        )
        if not re.fullmatch(UNIT, known_unit, re.I):
            continue
        values = tuple(Decimal(raw) for raw in re.findall(r"\d+(?:\.\d+)?", fact.value))
        for value in values:
            measurements.setdefault((value, unit_name(known_unit)), set()).add(fact.id)
        if len(values) > 1:
            axes.add((values, unit_name(known_unit)))

    for path, content in fields.items():
        # Questions and operator-only layout instructions are not product assertions.
        # MiMo still reviews presuppositions and misleading question wording semantically.
        if path.endswith(".question") or path.endswith(".layout"):
            continue
        linked = references.get(path, set())
        if re.search(r"(?:\$|USD\s*)\d", content, re.I) and not any(
            fact.key == "listing_price" for fact in facts
        ):
            issues.append(
                issue(
                    path,
                    "unsupported_listing_price",
                    "商品正文含未经卖家确认的价格；竞品区间只能作为配套定价资料。",
                )
            )
        for match in COUNT_PATTERN.finditer(content):
            value = number(match.group(1) or match.group(2))
            if quantities != {value}:
                issues.append(
                    issue(
                        path,
                        "quantity_mismatch",
                        f"包装数量声明 {match.group()} 与确认事实不一致。",
                    )
                )
            elif not linked.intersection(quantity_ids):
                issues.append(issue(path, "missing_fact_link", "数量声明没有关联包装数量事实。"))
        for match in MEASURE_PATTERN.finditer(content):
            key = (Decimal(match.group(1)), unit_name(match.group(2)))
            if key not in measurements:
                issues.append(
                    issue(path, "dimension_mismatch", f"尺寸声明 {match.group()} 无确认依据。")
                )
            elif not linked.intersection(measurements[key]):
                issues.append(issue(path, "missing_fact_link", "尺寸声明没有关联对应尺寸事实。"))
        for match in AXES_PATTERN.finditer(content):
            values = tuple(Decimal(raw) for raw in re.findall(r"\d+(?:\.\d+)?", match.group(1)))
            if (values, unit_name(match.group(2))) not in axes:
                issues.append(issue(path, "dimension_mismatch", "多维尺寸组合与确认事实不一致。"))
        for category, pattern in SENSITIVE_PATTERNS.items():
            for match in pattern.finditer(content):
                if re.search(r"\b(?:not|no|without)\s*$", content[: match.start()], re.I):
                    continue
                supporting = supported_sensitive(category, match.group(), facts)
                if not supporting or not linked.intersection(supporting):
                    issues.append(
                        issue(
                            path,
                            f"unsupported_{category}",
                            f"声明“{match.group()}”缺少明确事实支持。",
                        )
                    )
    return list({(item.field_path, item.rule_id, item.reason): item for item in issues}.values())


async def review_listing(context: dict[str, Any], provider: MiMoProvider) -> ReviewResult:
    raw = context.get("draft", context.get("listing"))
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        raise ValueError("Review requires a draft/listing object.")
    draft = ListingDraft.model_validate(
        {key: raw[key] for key in ListingDraft.model_fields if key in raw}
    )
    program_issues = program_review(context, draft)
    if context.get("image_paths") and len(context["image_paths"]) != len(draft.image_plan):
        program_issues.append(
            issue(
                "image_plan",
                "incomplete_images",
                "图片计划与实际成品数量不一致，不能通过完整审校。",
            )
        )
    payload = {
        key: value
        for key, value in context.items()
        if key not in {"image_paths", "draft", "listing"}
    }
    payload.update(
        draft=draft.model_dump(), program_issues=[item.model_dump() for item in program_issues]
    )
    images = [
        await asyncio.to_thread(MiMoProvider.image_content, Path(path))
        for path in context.get("image_paths", [])
    ]
    payload["review_scope"] = "text_and_images" if images else "text_review_only"
    content = [
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)},
        *images,
    ]
    prompt = files("company").joinpath("prompts/review.md").read_text(encoding="utf-8")
    semantic, call = await provider.generate_json(
        ReviewDraft,
        [{"role": "system", "content": prompt}, {"role": "user", "content": content}],
        max_tokens=4096,
    )
    issues = [*program_issues, *semantic.issues]
    if not semantic.passed and not any(item.severity == "blocking" for item in semantic.issues):
        issues.append(
            issue("draft", "semantic_rejected", "语义审校未通过，需补充具体修复意见后再审。")
        )
    known = {fact.id for fact in read_confirmed_facts(context)}
    coverage = []
    for item in semantic.buyer_question_coverage:
        if set(item.fact_ids) - known or (item.status == "supported" and not item.fact_ids):
            issues.append(
                issue(
                    "buyer_question_coverage", "unsupported_coverage", "有据标签缺少有效事实依据。"
                )
            )
            item = item.model_copy(update={"status": "missing_evidence"})
        coverage.append(item)
    questions = context.get(
        "buyer_questions", context.get("research", {}).get("buyer_questions", [])
    )
    answered = {item.question for item in coverage}
    for question in questions:
        text = question.get("question") if isinstance(question, dict) else str(question)
        if text not in answered:
            coverage.append(
                BuyerQuestionCoverage(
                    question=text,
                    status="unanswered",
                    reason="本次语义审校未覆盖该买家问题，不能假定已回答。",
                )
            )
    text_passed = semantic.passed and not any(item.severity == "blocking" for item in issues)
    return ReviewResult(
        passed=text_passed and bool(images),
        text_passed=text_passed,
        scope="text_and_images" if images else "text_review_only",
        requires_images=not images,
        issues=issues,
        buyer_question_coverage=coverage,
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
