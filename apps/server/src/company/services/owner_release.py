"""Version-bound owner review acknowledgements; AI findings are advisory."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from company.domain.brands import missing_delivery_fields
from company.persistence.assets import Asset
from company.persistence.models import InputSnapshot, Order
from company.persistence.production import DeliverableVersion, ResearchPackage, ReviewReport

MANUAL_PHASES = {"AWAITING_RELEASE", "REVIEW", "NEEDS_IMAGE"}


async def release_review(session, order_id: UUID, version_id: UUID) -> dict:
    order = await session.get(Order, order_id)
    version = await session.get(DeliverableVersion, version_id)
    if order is None or version is None or version.order_id != order_id:
        raise HTTPException(404, "Candidate not found.")
    snapshot = await session.scalar(
        select(InputSnapshot).where(
            InputSnapshot.order_id == order_id, InputSnapshot.revision == order.revision
        )
    )
    latest = await session.scalar(
        select(DeliverableVersion.id)
        .where(DeliverableVersion.order_id == order_id)
        .order_by(DeliverableVersion.version_number.desc())
        .limit(1)
    )
    reports = list(
        await session.scalars(
            select(ReviewReport)
            .where(ReviewReport.version_id == version_id, ReviewReport.order_id == order_id)
            .order_by(ReviewReport.created_at.desc(), ReviewReport.id)
        )
    )
    review = reports[0] if reports else None
    issues, blockers = [], []
    if order.phase not in MANUAL_PHASES:
        blockers.append("当前订单阶段不支持放行。")
    if latest != version_id or snapshot is None or snapshot.id != version.input_snapshot_id:
        blockers.append("候选版本或商品资料已更新，请打开最新版本重新确认。")
    if version.status in {"vetoed", "delivered"}:
        blockers.append("该版本已经处理，不能重复放行。")
    if review:
        for issue in review.result.get("issues", []):
            issues.append(
                {
                    "field": str(issue.get("field_path", "")),
                    "message": str(issue.get("reason", "未提供说明")),
                    "suggestion": str(issue.get("suggested_fix", "")),
                }
            )
    if not review or review.scope != "text_and_images":
        issues.append({"field": "review", "message": "当前版本尚无完整图文审校结论。"})
    elif not review.full_passed:
        issues.append({"field": "review", "message": "当前版本未通过完整图文审校。"})
    if not version.text_passed:
        issues.append({"field": "text", "message": "当前版本未通过文字审校。"})
    missing = missing_delivery_fields(order.product_name, snapshot.facts) if snapshot else []
    if missing:
        issues.append({"field": "facts", "message": "尚未确认的商品资料：" + "、".join(missing)})
    try:
        ids = [
            UUID(str(item.get("asset_id", item.get("id"))) if isinstance(item, dict) else str(item))
            for item in version.assets
        ]
    except (ValueError, TypeError):
        ids = []
    images = list(
        await session.scalars(select(Asset).where(Asset.order_id == order_id, Asset.id.in_(ids)))
    )
    if not ids or len(ids) != len(set(ids)) or len(images) != len(ids):
        blockers.append("当前候选尚未绑定完整图片，请先将已回传图片提交为新候选。")
    if len(ids) != len(version.draft.get("image_plan", [])):
        blockers.append("候选图片数量与图片计划不一致，请先补齐本版图片。")
    if any(
        image.version_id != version_id or image.kind in {"reference", "concept"} or image.is_demo
        for image in images
    ):
        blockers.append("存在未绑定本版的图片或概念/演示素材，不能作为正式商品图交付。")
    research = await session.get(ResearchPackage, version.research_package_id)
    if (
        research is None
        or research.order_id != order_id
        or (snapshot and research.input_snapshot_id != snapshot.id)
    ):
        blockers.append("研究资料与当前商品版本不匹配。")
    binding = {
        "version_id": str(version_id),
        "revision": order.revision,
        "input": str(version.input_snapshot_id),
        "facts": snapshot.facts if snapshot else [],
        "product": order.product_name,
        "details": order.product_details,
        "notes": snapshot.notes if snapshot else None,
        "draft": version.draft,
        "assets": version.assets,
        "images": sorted(
            [
                (str(i.id), i.sha256, i.storage_key, str(i.version_id), i.kind, i.is_demo)
                for i in images
            ]
        ),
        "review": [
            {
                "id": str(r.id),
                "result": r.result,
                "scope": r.scope,
                "text_passed": r.text_passed,
                "full_passed": r.full_passed,
            }
            for r in reports
        ],
        "version_review": [version.text_passed, version.full_passed],
        "research": research.result if research else None,
    }
    token = hashlib.sha256(
        json.dumps(binding, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return {
        "version_id": str(version_id),
        "revision": order.revision,
        "token": token,
        "issues": issues,
        "blockers": blockers,
        "review_id": str(review.id) if review else None,
    }


async def verify_manual_release(session, order_id, version_id, token):
    current = await release_review(session, order_id, version_id)
    if current["token"] != token:
        raise HTTPException(409, "商品、图片或审校问题已变化，请重新查看并确认当前问题。")
    if current["blockers"]:
        raise HTTPException(409, "；".join(current["blockers"]))
    return current
