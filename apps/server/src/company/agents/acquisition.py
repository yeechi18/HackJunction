"""Multimodal initial analysis and content direction; the owner decides acceptance."""

import asyncio
import json
from collections import defaultdict
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict

from company.domain.facts import canonical_key
from company.providers.mimo import MiMoProvider
from company.schemas.intake import IntakeAnalysis, PageObservation, SourceFact

FIELD_LABELS = {
    "material": "材质",
    "dimensions": "尺寸及单位",
    "pack_quantity": "每包商品数量",
    "use_cases": "适用范围和使用限制",
    "product_name": "商品名称",
}
SELLER_SOURCES = {
    "",
    "seller",
    "seller_input",
    "seller_confirmed",
    "seller_confirmation",
    "user",
    "user_confirmed",
}
REFERENCE_ONLY_SOURCES = {
    "competitor",
    "competitor_info",
    "competitor_information",
    "competitor_fact",
    "market_research",
    "model",
    "model_inference",
    "model_inferred",
    "inference",
    "model_guess",
}
UNKNOWN_VALUES = {"unknown", "n/a", "待确认", "未知", "不清楚", "未确认"}


class FactConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    values: list[str]
    message: str


class _IntakeDraft(IntakeAnalysis):
    """Model analysis; source availability and routing remain application-owned."""


class AcquisitionResult(IntakeAnalysis):
    ready_for_acceptance: bool
    missing_fields: list[str]
    conflicts: list[FactConflict]
    usage: dict[str, int]
    model: str
    elapsed_seconds: float


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _source_kind(fact: dict[str, Any]) -> str:
    source = fact.get("source_kind", fact.get("source_type", fact.get("source", "")))
    if isinstance(source, dict):
        source = source.get("kind", source.get("type", "unknown_source"))
    return _normalize(source) if isinstance(source, str) else "unknown_source"


def _collect_facts(order_input: dict[str, Any]) -> list[dict[str, Any]]:
    records = order_input.get("facts", [])
    if not isinstance(records, list) or len(records) > 100:
        raise ValueError("Intake facts must be a list containing at most 100 records.")
    facts: list[dict[str, Any]] = []
    for fact in records:
        if not isinstance(fact, dict) or not isinstance(fact.get("key"), str):
            raise ValueError("Each intake fact must contain a string key.")
        value = fact.get("value")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError("Intake fact values must be strings.")
        unit = fact.get("unit")
        if unit is not None and not isinstance(unit, str):
            raise ValueError("Intake fact units must be strings when supplied.")
        facts.append(
            {
                "key": canonical_key(fact["key"]),
                "value": value.strip(),
                "unit": (unit or "").strip(),
                "confirmed": fact.get("confirmed") is True,
                "source_kind": _source_kind(fact),
            }
        )
    return facts


def _program_findings(
    order_input: dict[str, Any], facts: list[dict[str, Any]]
) -> tuple[list[str], list[FactConflict]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if not fact["value"] or _normalize(fact["value"]) in UNKNOWN_VALUES:
            continue
        # Competitor/model claims are reference material, never this SKU's confirmed facts.
        if fact["source_kind"] not in REFERENCE_ONLY_SOURCES:
            grouped[fact["key"]].append(fact)

    missing = []
    product_name = order_input.get("product_name")
    if not isinstance(product_name, str) or not product_name.strip():
        missing.insert(0, "product_name")

    conflicts = []
    for key, candidates in grouped.items():
        values: dict[tuple[str, str], str] = {}
        for fact in candidates:
            signature = (_normalize(fact["value"]), _normalize(fact["unit"]))
            values.setdefault(signature, " ".join(filter(None, (fact["value"], fact["unit"]))))
        if len(values) > 1:
            label = FIELD_LABELS.get(key, key)
            conflicts.append(
                FactConflict(
                    key=key,
                    values=list(values.values()),
                    message=f"{label}存在不同值，请卖家确认本次商品适用的值后重新提交。",
                )
            )
    return missing, conflicts


def grounded_suggestions(order_input, facts, candidates) -> list[SourceFact]:
    """Keep source descriptions without treating them as authenticity evidence."""
    occupied = {fact["key"] for fact in facts if fact["value"] and fact["confirmed"]}
    page = order_input.get("page_observation") or {}
    sources = {
        "product_name": [order_input.get("product_name") or ""],
        "product_details": [order_input.get("product_details") or ""],
        "seller_notes": [order_input.get("notes") or ""],
        "seller_message": [
            item.get("content", "")
            for item in order_input.get("messages", [])
            if item.get("role") == "seller"
        ],
        "product_page": [page.get("excerpt", "")] if page.get("status") == "ok" else [],
    }
    result, signatures = [], set()
    for candidate in candidates:
        key = canonical_key(candidate.key)
        if key in occupied:
            continue
        if not any(candidate.source_quote in text for text in sources[candidate.source]):
            continue
        if candidate.value not in candidate.source_quote or (
            candidate.unit and candidate.unit not in candidate.source_quote
        ):
            continue
        signature = (key, candidate.value, candidate.unit)
        if signature not in signatures:
            result.append(candidate.model_copy(update={"key": key}))
            signatures.add(signature)
    return result


async def inspect_intake(
    order_input: dict[str, Any], provider: MiMoProvider, *, image_paths=None
) -> AcquisitionResult:
    """Analyze supplied information and actual attachments without a fixed attribute gate."""
    facts = _collect_facts(order_input)
    missing, conflicts = _program_findings(order_input, facts)
    context = {
        **order_input,
        "facts": facts,
        "program_conflicts": [item.model_dump() for item in conflicts],
    }
    prompt = files("company").joinpath("prompts/acquisition.md").read_text(encoding="utf-8")
    content = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
    for path in (image_paths or [])[:3]:
        content.append(await asyncio.to_thread(MiMoProvider.image_content, path))
    draft, call = await provider.generate_json(
        _IntakeDraft,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content if len(content) > 1 else content[0]["text"]},
        ],
        max_tokens=4096,
    )
    payload = draft.model_dump()
    payload.update(
        source_facts=grounded_suggestions(order_input, facts, draft.source_facts),
        page_observation=PageObservation.model_validate(order_input["page_observation"])
        if order_input.get("page_observation")
        else None,
        strategy_version="evidence-intake-v1",
    )
    if not image_paths:
        payload["image_observations"] = []
    # The owner judges whether to accept; omitted optional specs never hide the decision buttons.
    return AcquisitionResult(
        **payload,
        ready_for_acceptance=not missing,
        missing_fields=missing,
        conflicts=conflicts,
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
