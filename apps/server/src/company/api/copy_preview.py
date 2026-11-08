"""Versioned pre-delivery copy drafts; never create a deliverable or approval."""

import json
import re
from datetime import timedelta
from importlib.resources import files
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from company.agents.review import program_review
from company.api.assets import _write_access as seller_write_access
from company.api.dependencies import DB, Configuration
from company.api.integrations import preview_write_access as _write_access
from company.api.orders import read_access
from company.domain.brands import known_brand, missing_delivery_fields
from company.persistence.agent_runs import AgentRun
from company.persistence.models import Command, InputSnapshot, OrderEvent, now
from company.providers.factory import build_provider
from company.providers.mimo import ProviderError
from company.providers.usage import observed_call, record_call
from company.schemas.listings import FAQ, Claim, ListingDraft
from company.schemas.orders import SupplementInput
from company.services.model_budget import check_budget
from company.services.orders import add_supplement, load_order, summary
from company.services.production import digest

router = APIRouter(prefix="/orders", tags=["copy previews"])


class PreviewInput(BaseModel):
    expected_revision: int = Field(ge=1)


@router.post("/{order_id}/required-facts")
async def confirm_required_facts(
    order_id: UUID, body: SupplementInput, request: Request, database: DB, settings: Configuration
):
    await seller_write_access(request, database, settings, order_id)
    order = await load_order(database, order_id, lock=True)
    from company.domain.brands import required_keys

    allowed = set(required_keys({"product_name": order.product_name}))
    if not allowed or not body.facts or any(f.key not in allowed for f in body.facts):
        raise HTTPException(422, "Only category-specific required facts can be confirmed here.")
    snapshot = await database.scalar(
        select(InputSnapshot).where(
            InputSnapshot.order_id == order_id, InputSnapshot.revision == order.revision
        )
    )
    submitted = {f.key: f.model_dump() for f in body.facts}
    facts = [f for f in snapshot.facts if f["key"] not in submitted] + list(submitted.values())
    return await add_supplement(
        database, order_id, SupplementInput(expected_revision=body.expected_revision, facts=facts)
    )


class CopyDraft(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    bullets: list[str] = Field(min_length=5, max_length=5)
    description: str = Field(min_length=1, max_length=10000)
    faqs: list[FAQ] = Field(default_factory=list, max_length=8)
    # Claims enable the existing deterministic provenance/numeric checks.
    claims: list[Claim] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def english_only(self):
        # JSON array paths may arrive as bullets.0 or bullets[0]; canonicalize syntax,
        # then run the same provenance checks without changing content or evidence.
        for claim in self.claims:
            claim.field_path = re.sub(r"\.(\d+)(?=\.|$)", r"[\1]", claim.field_path)
        texts = [self.title, *self.bullets, self.description]
        texts.extend(text for faq in self.faqs for text in [faq.question, faq.answer])
        if any(re.search(r"[\u3400-\u9fff]", text) for text in texts):
            raise ValueError("Buyer-facing copy must be English; translate Chinese source text.")
        return self


def preview_view(run):
    if run is None:
        return None
    result = run.result or {}
    draft = result.get("draft")
    status = run.status
    if draft:
        try:
            CopyDraft.model_validate(draft)
        except ValueError:
            draft, status = None, "needs_review"
    return {
        "id": str(run.id),
        "revision": run.input_revision,
        "status": status,
        "draft": draft,
        "issues": result.get("issues", []),
        "missing_fields": result.get("missing_fields", []),
        "brand_profile": result.get("brand_profile"),
        "message": "草稿暂未生成，请稍后重试或联系负责人。" if run.status == "failed" else None,
    }


async def latest(database, order_id, revision):
    return await database.scalar(
        select(AgentRun)
        .where(
            AgentRun.order_id == order_id,
            AgentRun.input_revision == revision,
            AgentRun.role == "copy_preview",
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id)
        .limit(1)
    )


@router.get("/{order_id}/copy-preview")
async def get_preview(order_id: UUID, request: Request, database: DB):
    await read_access(request, database, order_id)
    order = await load_order(database, order_id)
    return {
        "preview": preview_view(await latest(database, order_id, order.revision)),
        "brand_profile": known_brand(order.product_name),
    }


@router.post("/{order_id}/brand-basics")
async def apply_brand_basics(
    order_id: UUID, body: PreviewInput, request: Request, database: DB, settings: Configuration
):
    await _write_access(request, database, settings, order_id)
    order = await load_order(database, order_id, lock=True)
    if order.revision != body.expected_revision:
        raise HTTPException(409, "Product facts changed.")
    profile = known_brand(order.product_name)
    if not profile:
        raise HTTPException(422, "No reviewed brand profile matches this product.")
    snapshot = await database.scalar(
        select(InputSnapshot).where(
            InputSnapshot.order_id == order_id, InputSnapshot.revision == order.revision
        )
    )
    facts = list(snapshot.facts)
    for fact in profile["facts"]:
        if not any(item["key"] == fact["key"] for item in facts):
            facts.append(fact)
    if facts == snapshot.facts:
        return await summary(database, order)
    notes = (
        (snapshot.notes or "")
        + "\n品牌与类别按官网核对补入，不代表本瓶规格已核实。来源："
        + "；".join(profile["sources"])
    )[:10000]
    return await add_supplement(
        database,
        order_id,
        SupplementInput(expected_revision=order.revision, facts=facts, notes=notes),
    )


@router.post("/{order_id}/copy-preview")
async def create_preview(
    order_id: UUID, body: PreviewInput, request: Request, database: DB, settings: Configuration
):
    await _write_access(request, database, settings, order_id)
    order = await load_order(database, order_id, lock=True)
    if order.revision != body.expected_revision:
        raise HTTPException(409, "Product facts changed; reload before drafting.")
    prior = await latest(database, order_id, order.revision)
    if prior and prior.status == "running" and now() - prior.created_at > timedelta(minutes=2):
        prior.status = "failed"
        prior.finished_at = now()
        prior.error_summary = "上次草稿生成已中断，可重新尝试。"
        await database.flush()
    if (
        prior
        and prior.status in {"running", "succeeded"}
        and preview_view(prior)["status"] != "needs_review"
    ):
        return preview_view(prior)
    count = await database.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.order_id == order_id,
            AgentRun.role == "copy_preview",
            AgentRun.input_revision == order.revision,
        )
    )
    if count >= 5:
        raise HTTPException(429, "Draft attempt budget reached; ask the owner to review.")
    snapshot = await database.scalar(
        select(InputSnapshot).where(
            InputSnapshot.order_id == order_id, InputSnapshot.revision == order.revision
        )
    )
    profile = known_brand(order.product_name)
    facts = [dict(f) for f in snapshot.facts if f.get("value") and f.get("confirmed")]
    # Public basics are inserted only if no seller value exists; no volume is inferred.
    for fact in (profile or {}).get("facts", []):
        if not any(f["key"] == fact["key"] for f in snapshot.facts):
            facts.append(fact)
    facts = [{**f, "id": f"fact:{f['key']}"} for f in facts]
    missing = missing_delivery_fields(order.product_name, facts)
    # Translate reviewed catalog basics deterministically for the English-only draft.
    # Stored seller facts and provenance are unchanged.
    translations = {
        "Ganten / 百岁山": "Ganten",
        "天然矿泉水": "Natural mineral water",
        "饮用": "Drinking",
    }
    english_facts = [
        {
            **f,
            "value": translations.get(f["value"], f["value"]),
            "unit": "bottles" if f.get("unit") == "瓶" else f.get("unit"),
        }
        for f in facts
    ]
    context = {
        "product_name": "Ganten Natural Mineral Water" if profile else order.product_name,
        "confirmed_facts": english_facts,
        "missing_fields": missing,
        "official_brand_sources": (profile or {}).get("sources", []),
        "previous_issues": (prior.result or {}).get("issues", []) if prior else [],
        "output_language": "English (en-US); translate Chinese facts. English buyer copy only.",
    }
    command = Command(
        order_id=order_id,
        kind="COPY_PREVIEW",
        expected_revision=order.revision,
        idempotency_key=f"preview:{uuid4()}",
        request_hash=digest(context),
        payload={},
        status="running",
    )
    database.add(command)
    await database.flush()
    run = AgentRun(
        order_id=order_id,
        command_id=command.id,
        input_snapshot_id=snapshot.id,
        input_revision=order.revision,
        role="copy_preview",
        status="running",
        result={"missing_fields": missing, "brand_profile": profile},
    )
    database.add(run)
    await database.commit()
    provider, call = None, None
    try:
        await check_budget(request.app.state.database, settings, order_id)
        provider = build_provider(settings.model_copy(update={"request_timeout_seconds": 25}))
        if hasattr(provider, "trace_context"):
            provider.trace_context = {
                "order_id": str(order_id),
                "command_id": str(command.id),
                "role": "copy_preview",
            }
        output, call = await provider.generate_json(
            CopyDraft,
            [
                {
                    "role": "system",
                    "content": files("company")
                    .joinpath("prompts/copy_preview.md")
                    .read_text(encoding="utf-8"),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            max_tokens=4096,
        )
        draft = ListingDraft(
            **output.model_dump(),
            image_plan=[
                {
                    "purpose": "main",
                    "layout": "Await actual image review.",
                    "text_overlay": "",
                    "fact_ids": [],
                }
            ],
        )
        issues = program_review(context, draft)
        blocked = any(i.severity == "blocking" for i in issues)
        run.result = {
            **run.result,
            "draft": None if blocked else output.model_dump(),
            "rejected_draft": output.model_dump() if blocked else None,
            "issues": [i.model_dump() for i in issues],
        }
        run.status = "needs_review" if blocked else "succeeded"
        await database.refresh(order)
        if order.revision != run.input_revision:
            run.status = "stale"
        command.status = "applied" if run.status != "stale" else "stale"
        database.add(
            OrderEvent(
                order_id=order_id,
                kind="copy_preview_created",
                message="英文初稿已生成，未批准交付；缺失参数仍需补充核对。",
            )
        )
    except Exception as error:
        run.status, command.status = "failed", "failed"
        run.error_summary = str(error) if isinstance(error, ProviderError) else type(error).__name__
        if isinstance(error, ProviderError) and error.call_usage:
            record_call(run, "usage", error.call_usage)
    finally:
        if call:
            record_call(run, "usage", observed_call(call))
        run.finished_at = now()
        await database.commit()
        if provider:
            await provider.close()
    return preview_view(run)
