"""MiMo writes intake language; deterministic facts decide acceptance readiness."""

import json
from collections import defaultdict
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from company.domain.facts import canonical_key
from company.providers.mimo import MiMoProvider

REQUIRED_FACTS = ("material", "dimensions", "pack_quantity", "use_cases")
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


class _IntakeDraft(BaseModel):
    """Language draft only. Its decision fields are never authoritative."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ready_for_acceptance: bool
    missing_fields: list[str]
    conflicts: list[FactConflict]
    questions: list[str] = Field(max_length=12)
    summary: str = Field(min_length=1, max_length=2000)


class AcquisitionResult(_IntakeDraft):
    """Safe structured output plus actual provider usage, not model-estimated usage."""

    questions: list[str] = Field(max_length=112)
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
    confirmed_keys: set[str] = set()
    allowed_sources = SELLER_SOURCES | (
        {"fictional_seller"} if order_input.get("is_demo") is True else set()
    )
    for fact in facts:
        if not fact["value"] or _normalize(fact["value"]) in UNKNOWN_VALUES:
            continue
        # Competitor/model claims are reference material, never this SKU's confirmed facts.
        if fact["source_kind"] not in REFERENCE_ONLY_SOURCES:
            grouped[fact["key"]].append(fact)
        if fact["confirmed"] and fact["source_kind"] in allowed_sources:
            confirmed_keys.add(fact["key"])

    missing = [key for key in REQUIRED_FACTS if key not in confirmed_keys]
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


async def inspect_intake(order_input: dict[str, Any], provider: MiMoProvider) -> AcquisitionResult:
    """Inspect immutable input. Provider is injected; errors propagate to the workflow.

    ``facts`` from the seller form may omit provenance because that form is the source.
    If provenance is supplied, only an explicit seller-confirmed source can satisfy a
    required field. Image observations, documents, competitor data and model guesses
    require a separate seller-confirmation step. No model-generated fact patch is returned.
    """
    facts = _collect_facts(order_input)
    missing, conflicts = _program_findings(order_input, facts)
    ready = not missing and not conflicts
    context = {
        "product_name": order_input.get("product_name", ""),
        "facts": facts,
        "notes": order_input.get("notes"),
        "messages": order_input.get("messages", []),
        "program_findings": {
            "ready_for_acceptance": ready,
            "missing_fields": missing,
            "conflicts": [conflict.model_dump() for conflict in conflicts],
        },
    }
    prompt = files("company").joinpath("prompts/acquisition.md").read_text(encoding="utf-8")
    draft, call = await provider.generate_json(
        _IntakeDraft,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        max_tokens=2048,
    )
    # Mandatory questions survive even an incorrect model verdict or an empty question list.
    required_questions = [f"请补充并确认商品的{FIELD_LABELS[key]}。" for key in missing]
    required_questions.extend(
        f"{FIELD_LABELS.get(conflict.key, conflict.key)}有不同记录"
        f"（{'；'.join(conflict.values)}），请确认本次商品的正确值。"
        for conflict in conflicts
    )
    questions = list(dict.fromkeys([*required_questions, *draft.questions])) if not ready else []
    return AcquisitionResult(
        ready_for_acceptance=ready,
        missing_fields=missing,
        conflicts=conflicts,
        questions=questions,
        summary=draft.summary,
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
