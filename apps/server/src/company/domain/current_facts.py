"""Resolve historical UNKNOWN placeholders without rewriting stored sources."""

import re
from copy import deepcopy


def resolve_current_facts(context: dict) -> dict:
    resolved = deepcopy(context)
    volumes = [
        f
        for f in context.get("confirmed_facts", [])
        if f.get("key") == "net_volume"
        and f.get("provenance", "seller_confirmed") == "seller_confirmed"
    ]
    if len(volumes) != 1:
        return resolved
    fact = volumes[0]
    if not re.fullmatch(r"\d+(?:\.\d+)?", fact["value"]) or float(fact["value"]) <= 0:
        return resolved
    value = f"{fact['value']} {fact.get('unit') or ''}".strip()
    replacement = f"单瓶净含量为 {value}（本版卖家已确认）"
    patterns = [
        r"(?:单瓶净含量|净含量|单瓶容量|容量)\s*(?:尚未确认|尚未提供|未确认|未提供|未知|待确认|不明)",
        r"(?:卖家)?(?:尚未|未)(?:确认|提供)(?:单瓶净含量|净含量|单瓶容量|容量)",
        r"\b(?:net\s+volume|capacity|volume)\s*(?:is\s+)?(?:unknown|unconfirmed|not\s+provided)\b",
    ]

    def render(item):
        if isinstance(item, str):
            for pattern in patterns:
                item = re.sub(pattern, lambda match: replacement, item, flags=re.I)
            return item
        if isinstance(item, list):
            return [render(value) for value in item]
        if isinstance(item, dict):
            return {
                key: value
                if key in {"evidence", "confirmed_facts", "source_quote"}
                else render(value)
                for key, value in item.items()
            }
        return item

    resolved = render(resolved)
    resolved["fact_precedence"] = {
        "policy": "Current seller-confirmed facts override earlier UNKNOWN placeholders. "
        "An older source saying unknown is not evidence against a later confirmation. "
        "Conflicting concrete numeric values must still be flagged, never overwritten.",
        "current_net_volume": value,
        "fact_id": fact["id"],
        "evidence_scope": "Evidence is historical and preserved verbatim; it does not override "
        "this revision's seller confirmation.",
    }
    return resolved
