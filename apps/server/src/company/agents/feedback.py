"""Seller feedback belongs to the acquisition role; only explicit snapshots change facts."""

import json
import re
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from company.agents.acquisition import _collect_facts, _program_findings

SCOPE = re.compile(
    r"新增.{0,12}(?:SKU|商品)|(?:第二|另一个|多款).{0,6}(?:SKU|商品)|"
    r"(?:改|切换|换).{0,8}(?:平台|站点|marketplace)|"
    r"(?:add|another|second|multiple|new)\s+(?:SKU|product)|"
    r"(?:switch|change).{0,15}(?:platform|marketplace)|淘宝|拼多多|抖音|Shopify|eBay",
    re.I,
)
FACT_CHANGE = re.compile(
    r"数量|件装|个装|每包|材质|尺寸|直径|规格|适用|承重|兼容|"
    r"\b(?:quantity|pack|material|dimensions?|diameter|capacity|compatibility)\b|"
    r"\d\s*(?:mm|cm|inches)\b|\d\s*(?:改为?|变为?|→|->)\s*\d",
    re.I,
)
SENSITIVE = re.compile(
    r"防水|认证|通用|适配所有|兼容所有|\bwater[- ]?proof\b|\bIPX?\d{1,2}\b|"
    r"\b(?:FDA|FCC|CE|UL|ISO)\b|\bcertif\w*\b|\buniversal\w*\b|"
    r"\b(?:fits?|compatible\s+with|works?\s+with)\s+(?:all|any|every)\b",
    re.I,
)
REMOVE = re.compile(r"删除|去掉|不要|不写|移除|\b(?:remove|omit|delete|avoid)\b", re.I)


class FeedbackDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Literal["style", "fact", "new_claim", "scope"]
    reason: str = Field(min_length=1, max_length=2000)
    questions: list[str] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=2000)


class FeedbackResult(FeedbackDraft):
    questions: list[str] = Field(default_factory=list, max_length=16)
    actionable: bool
    requires_research: bool
    program_overrides: list[str]
    usage: dict[str, int]
    model: str
    elapsed_seconds: float


def _fact_signature(facts):
    return sorted((fact["key"], str(fact["value"]), fact.get("unit") or "") for fact in facts)


async def inspect_feedback(context: dict, provider) -> FeedbackResult:
    text = context["content"]
    updates = context.get("fact_updates")
    source = files("company").joinpath("prompts/feedback.md").read_text(encoding="utf-8")
    draft, call = await provider.generate_json(
        FeedbackDraft,
        [
            {"role": "system", "content": source},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
        ],
        max_tokens=2048,
    )
    overrides, required_questions = [], []
    category = draft.category
    scope = bool(SCOPE.search(text))
    sensitive = any(
        SENSITIVE.search(part) and not REMOVE.search(part) for part in re.split(r"[。；;\n]", text)
    )
    if updates is not None:
        category = "fact"
        overrides.append("explicit_fact_snapshot")
    elif scope:
        category = "scope"
        overrides.append("scope_requires_owner")
    elif sensitive:
        category = "new_claim"
        overrides.append("new_sensitive_claim")
    elif FACT_CHANGE.search(text):
        category = "fact"
        overrides.append("unconfirmed_fact_change")
    actionable = category == "style"
    if category == "fact":
        actionable = updates is not None
        if updates is None:
            required_questions.append(
                "请通过事实确认表提交完整商品参数，并明确确认修改值；聊天文字不会自动成为商品事实。"
            )
        else:
            facts = _collect_facts({"facts": updates, "is_demo": context.get("is_demo", False)})
            missing, conflicts = _program_findings({"product_name": context["product_name"]}, facts)
            if missing or conflicts or any(fact.get("confirmed") is not True for fact in updates):
                actionable = False
                required_questions.append(
                    "请补齐并确认材质、尺寸及单位、每包数量和适用范围，消除事实冲突后重交完整快照。"
                )
            base = {fact["key"]: fact for fact in context.get("base_facts", [])}
            added_claim = any(
                SENSITIVE.search(f"{fact['key']} {fact['value']}") and base.get(fact["key"]) != fact
                for fact in updates
            )
            if added_claim:
                sensitive = True
    if category == "style" and _fact_signature(context.get("facts", [])) != _fact_signature(
        context.get("base_facts", [])
    ):
        actionable = False
        required_questions.append(
            "当前事实与已交付版本不同，请先通过完整事实确认表明确本次参数，再继续修改。"
        )
        overrides.append("changed_facts_require_explicit_confirmation")
    if sensitive or category == "new_claim":
        actionable = False
        required_questions.append(
            "新增防水、认证或通用适配声明需要本商品的有效证据及卖家确认，请提交材料交负责人核实后再生产。"
        )
    if scope or category == "scope":
        actionable = False
        required_questions.append("新增 SKU 或切换平台属于范围变更，请联系负责人重新确认任务范围。")
    questions = (
        list(dict.fromkeys([*required_questions, *draft.questions])) if not actionable else []
    )
    return FeedbackResult(
        **{**draft.model_dump(), "category": category, "questions": questions},
        actionable=actionable,
        requires_research=actionable and category == "fact",
        program_overrides=overrides,
        usage=dict(call.usage),
        model=call.model,
        elapsed_seconds=call.elapsed_seconds,
    )
