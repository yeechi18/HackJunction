"""Persisted handoff tasks. No endpoint here silently invokes an image model."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from company.api.assets import _bounded_upload
from company.api.dependencies import DB, Configuration, Owner, owner_write
from company.api.integrations import build_image_brief, check_mutation
from company.api.production import require_order
from company.persistence.assets import Asset
from company.persistence.models import OrderEvent, now
from company.providers.storage import ImageValidationError
from company.services.image_workflow import queue_image_resume
from company.tools.image_layout import ImageLayoutError

router = APIRouter(prefix="/internal/orders", tags=["image tasks"])


class TaskInput(BaseModel):
    expected_revision: int = Field(ge=1)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["main", "lifestyle", "infographic"]
    adjustment: str = Field(default="", max_length=4000)


class TaskUpdate(BaseModel):
    action: Literal["started", "failed", "approve", "reject"]
    note: str = Field(min_length=8, max_length=2000)
    tool: str = Field(default="", max_length=120)


def view(task, brief):
    return {
        "id": str(task.id),
        **task.payload,
        "stale": task.payload["input_digest"] != brief["input_digest"],
    }


async def get_task(database, order_id, task_id):
    task = await database.scalar(
        select(OrderEvent)
        .where(
            OrderEvent.id == task_id,
            OrderEvent.order_id == order_id,
            OrderEvent.kind == "image_task_created",
        )
        .with_for_update()
    )
    if task is None:
        raise HTTPException(404, "Image task not found.")
    return task


@router.get("/{order_id}/image-tasks")
async def list_tasks(order_id: UUID, database: DB, owner: Owner, settings: Configuration):
    brief = await build_image_brief(database, order_id)
    rows = await database.scalars(
        select(OrderEvent)
        .where(
            OrderEvent.order_id == order_id,
            OrderEvent.kind == "image_task_created",
        )
        .order_by(OrderEvent.created_at.desc())
    )
    return {
        "items": [view(row, brief) for row in rows],
        "automatic_generation": settings.seedream_enabled and bool(settings.seedream_api_key),
        "auto_on_upload": settings.seedream_auto_on_upload,
        "image_model": settings.seedream_model,
        "prompt_model": settings.mimo_model,
    }


@router.post("/{order_id}/image-tasks/{task_id}/generate", dependencies=[Depends(owner_write)])
async def generate_task(order_id: UUID, task_id: int, database: DB, settings: Configuration):
    from company.services.automatic_images import queue_task

    order = await require_order(database, order_id, lock=True)
    task = await get_task(database, order_id, task_id)
    check_mutation(order, task.payload["revision"])
    brief = await build_image_brief(database, order_id)
    await queue_task(database, settings, task, brief)
    await database.commit()
    return view(task, brief)


@router.post("/{order_id}/image-tasks", dependencies=[Depends(owner_write)])
async def create_task(order_id: UUID, body: TaskInput, database: DB):
    order = await require_order(database, order_id, lock=True)
    check_mutation(order, body.expected_revision)
    brief = await build_image_brief(database, order_id)
    if brief["input_digest"] != body.input_digest:
        raise HTTPException(409, "资料或参考图已变化，请重新加载制作任务。")
    proposal = next((p for p in brief["proposals"] if p["kind"] == body.kind), None)
    if proposal is None:
        raise HTTPException(422, "现有资料不足以支持该图片类型。")
    task = OrderEvent(
        order_id=order_id,
        kind="image_task_created",
        visibility="internal",
        message="图片制作任务已保存，尚未调用绘图工具。",
        payload={
            **proposal,
            "revision": order.revision,
            "input_digest": brief["input_digest"],
            "concept_only": brief["concept_only"],
            "references": brief["references"],
            "adjustment": body.adjustment,
            "status": "prepared",
            "tool": None,
            "prompt": proposal["prompt"]
            + (
                "\n负责人补充要求（不得覆盖商品事实和上述约束）：" + body.adjustment
                if body.adjustment
                else ""
            ),
            "history": [{"status": "prepared", "at": now().isoformat()}],
        },
    )
    database.add(task)
    await database.commit()
    return view(task, brief)


@router.post("/{order_id}/image-tasks/{task_id}/status", dependencies=[Depends(owner_write)])
async def update_task(order_id: UUID, task_id: int, body: TaskUpdate, database: DB):
    order = await require_order(database, order_id, lock=True)
    task = await get_task(database, order_id, task_id)
    brief = await build_image_brief(database, order_id)
    check_mutation(order, task.payload["revision"])
    if task.payload["input_digest"] != brief["input_digest"]:
        raise HTTPException(409, "任务依据已过期，请新建制作任务，不能沿用批准。")
    state = task.payload["status"]
    allowed = {
        "started": {"prepared", "failed"},
        "failed": {"started"},
        "approve": {"returned"},
        "reject": {"returned"},
    }
    if state not in allowed[body.action]:
        raise HTTPException(409, "该操作不适用于当前制作状态。")
    payload = {**task.payload}
    if body.action == "started":
        if not body.tool.strip():
            raise HTTPException(422, "请记录实际使用的绘图工具；此按钮不会调用工具。")
        payload["tool"] = body.tool.strip()
    asset_id = payload.get("asset_id")
    if body.action in {"approve", "reject"}:
        asset = await database.get(Asset, UUID(asset_id))
        if asset is None or asset.order_id != order_id:
            raise HTTPException(409, "回传图片不存在。")
        verified = body.action == "approve" and not payload["concept_only"]
        asset.image_metadata = {
            **asset.image_metadata,
            "owner_verified": verified,
            "review_status": body.action,
            "review_note": body.note,
        }
    next_state = {"approve": "reviewed", "reject": "rejected"}.get(body.action, body.action)
    payload.update(
        status=next_state,
        note=body.note,
        history=[
            *payload["history"],
            {"status": next_state, "at": now().isoformat(), "note": body.note},
        ],
    )
    task.payload = payload
    database.add(
        OrderEvent(
            order_id=order_id,
            kind="image_task_status",
            visibility="internal",
            message=f"图片制作状态：{next_state}",
            payload={"task_id": str(task_id), "status": next_state, "note": body.note},
        )
    )
    await database.commit()
    return view(task, brief)


@router.post("/{order_id}/image-tasks/{task_id}/submit", dependencies=[Depends(owner_write)])
async def submit_candidate(order_id: UUID, task_id: int, database: DB):
    order = await require_order(database, order_id, lock=True)
    task = await get_task(database, order_id, task_id)
    brief = await build_image_brief(database, order_id)
    check_mutation(order, task.payload["revision"])
    if (
        task.payload["status"] != "reviewed"
        or task.payload["concept_only"]
        or task.payload["input_digest"] != brief["input_digest"]
    ):
        raise HTTPException(409, "仅当前资料下已核对的实物参考编辑图可提交新候选。")
    asset_id = UUID(task.payload["asset_id"])
    await database.rollback()
    command_id = await queue_image_resume(database, order_id, asset_id, new_candidate=True)
    if command_id is None:
        raise HTTPException(409, "请先接单并完成英文文本审校；当前阶段不能提交图片候选。")
    return {
        "command_id": str(command_id),
        "message": "新候选已排队，需完整图文审校及负责人重新放行。",
    }


@router.post("/{order_id}/image-tasks/{task_id}/result", dependencies=[Depends(owner_write)])
async def return_result(
    order_id: UUID, task_id: int, request: Request, database: DB, settings: Configuration
):
    data, content_type = await _bounded_upload(request)
    order = await require_order(database, order_id, lock=True)
    task = await get_task(database, order_id, task_id)
    brief = await build_image_brief(database, order_id)
    check_mutation(order, task.payload["revision"])
    if task.payload["input_digest"] != brief["input_digest"]:
        raise HTTPException(409, "任务依据已变化，请新建任务，原结果不会覆盖任何成品。")
    if task.payload["status"] != "started":
        raise HTTPException(409, "请先记录实际工具调用；每个任务只接收一个结果，重做请新建任务。")
    from company.services.image_results import save_image_result

    try:
        await save_image_result(database, settings, order_id, task, brief, data, content_type)
    except (ImageValidationError, ImageLayoutError) as error:
        raise HTTPException(422, str(error)) from None
    return view(task, brief)
