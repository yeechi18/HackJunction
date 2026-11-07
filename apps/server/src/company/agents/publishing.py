"""MiMo prepares a plan; only the deterministic gateway can submit the frozen snapshot."""

import json
from importlib.resources import files

from company.providers.mimo import MiMoProvider
from company.schemas.publication import PublishingResult, PublishPlan


async def prepare_publish_plan(snapshot: dict, provider: MiMoProvider) -> PublishingResult:
    prompt = files("company").joinpath("prompts/publishing.md").read_text(encoding="utf-8")
    plan, call = await provider.generate_json(
        PublishPlan,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
        ],
        max_tokens=2048,
    )
    return PublishingResult(
        **plan.model_dump(),
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
