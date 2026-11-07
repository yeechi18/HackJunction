"""Research uses only supplied facts and sources; it never performs hidden web research."""

import json
import re
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from typing import Any

from company.domain.facts import canonical_key
from company.providers.mimo import MiMoProvider, ProviderError
from company.schemas.research import (
    ConfirmedFact,
    ObservedPriceRange,
    ResearchDraft,
    ResearchResult,
)


def read_confirmed_facts(context: dict[str, Any]) -> list[ConfirmedFact]:
    facts = [ConfirmedFact.model_validate(value) for value in context.get("confirmed_facts", [])]
    if len(facts) > 100 or len({fact.id for fact in facts}) != len(facts):
        raise ValueError("Confirmed facts require at most 100 unique fact IDs.")
    return [fact.model_copy(update={"key": canonical_key(fact.key)}) for fact in facts]


def supplied_evidence(context: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = context.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > 100:
        raise ValueError("Evidence must contain at most 100 supplied records.")
    usable = [
        dict(item)
        for item in evidence
        if item.get("id")
        and item.get("status", "provided") not in {"failed", "failure"}
        and (context.get("is_demo") is True or item.get("is_demo") is not True)
    ]
    if len({item["id"] for item in usable}) != len(usable):
        raise ValueError("Evidence IDs must be unique.")
    return usable


def observed_price_range(evidence: list[dict[str, Any]]) -> ObservedPriceRange | None:
    observations = []
    for item in evidence:
        # No currency conversion or comparability assertion is invented here.
        if item.get("currency", "").upper() != "USD" or item.get("price") is None:
            continue
        try:
            price = Decimal(str(item["price"]))
        except InvalidOperation:
            continue
        if price.is_finite() and price > 0:
            observations.append((item, price))
    if not observations:
        return None
    return ObservedPriceRange(
        low=min(price for _, price in observations),
        high=max(price for _, price in observations),
        currency="USD",
        evidence_ids=[item["id"] for item, _ in observations],
        is_demo=any(item.get("is_demo") is True for item, _ in observations),
    )


async def research_product(context: dict[str, Any], provider: MiMoProvider) -> ResearchResult:
    facts = read_confirmed_facts(context)
    evidence = supplied_evidence(context)
    prompt = files("company").joinpath("prompts/research.md").read_text(encoding="utf-8")
    price_range = observed_price_range(evidence)
    payload = {
        "product_name": context.get("product_name"),
        "confirmed_facts": [fact.model_dump() for fact in facts],
        "evidence": evidence,
        "program_observed_price_range": price_range.model_dump() if price_range else None,
    }
    draft, call = await provider.generate_json(
        ResearchDraft,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        max_tokens=4096,
    )
    source_ids = {item["id"] for item in evidence}
    fact_ids = {fact.id for fact in facts}
    for question in draft.buyer_questions:
        # A known seller fact is still a valid source, but belongs in its own namespace.
        # Preserve the reference rather than confusing it with external market evidence.
        referenced_facts = set(question.evidence_ids) & fact_ids
        question.fact_ids = sorted(set(question.fact_ids) | referenced_facts)
        question.evidence_ids = [key for key in question.evidence_ids if key not in fact_ids]
        if set(question.fact_ids) - fact_ids:
            raise ProviderError("Research cited product facts that were not supplied.")
        if set(question.evidence_ids) - source_ids:
            raise ProviderError("Research cited evidence that was not supplied or usable.")
        if any(
            not re.fullmatch(r"(?:title|bullets|description|faqs|image_plan)(?:\[\d+\])?", target)
            for target in question.target_fields
        ):
            raise ProviderError("Research targeted an unsupported listing field.")
    supported_prices = {
        Decimal(str(item["price"]))
        for item in evidence
        if item.get("price") is not None and re.fullmatch(r"\d+(?:\.\d+)?", str(item["price"]))
    }
    for note in draft.market_notes:
        for raw in re.findall(r"(?:\$|USD\s*)(\d+(?:\.\d+)?)", note, re.I):
            if Decimal(raw) not in supported_prices:
                raise ProviderError(
                    "Research introduced a price absent from the supplied evidence."
                )
    return ResearchResult(
        **draft.model_dump(),
        suggested_listing_price_range=price_range,
        pricing_notes=(
            "仅为已提供 USD 竞品观察的价格范围；需核对规格可比性、日期、成本及卖家最终售价。"
            if price_range
            else "缺少可用的 USD 价格证据，无法形成可靠价格区间；请补充带来源的竞品观察。"
        ),
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
