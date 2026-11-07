"""Generate a traceable text draft and image plan, never pretend a plan is an image."""

import json
from importlib.resources import files
from typing import Any

from company.agents.research import read_confirmed_facts
from company.agents.review import program_review
from company.providers.mimo import MiMoProvider
from company.schemas.listings import GenerationResult, ListingDraft


async def generate_listing(context: dict[str, Any], provider: MiMoProvider) -> GenerationResult:
    facts = read_confirmed_facts(context)
    payload = {
        **context,
        "confirmed_facts": [fact.model_dump() for fact in facts],
        "notice": "Images are plans only; actual assets and complete review are pending.",
    }
    prompt = files("company").joinpath("prompts/generation.md").read_text(encoding="utf-8")
    draft, call = await provider.generate_json(
        ListingDraft,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        max_tokens=6144,
    )
    return GenerationResult(
        **draft.model_dump(),
        program_issues=program_review(context, draft),
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
