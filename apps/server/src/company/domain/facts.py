"""Canonical fact keys shared by forms, saved snapshots and agent boundaries."""

FACT_KEY_ALIASES = {"package_quantity": "pack_quantity", "usage_scope": "use_cases"}


def canonical_key(key: str) -> str:
    normalized = key.strip()
    return FACT_KEY_ALIASES.get(normalized, normalized)
