"""Shared persistence for manual returns and automatic image-provider results."""

import asyncio
import hashlib

from company.persistence.assets import Asset
from company.persistence.models import now
from company.providers.storage import LocalAssetStorage
from company.services.assets import asset_view
from company.tools.image_layout import finish_external_image


async def save_image_result(
    database,
    settings,
    order_id,
    task,
    brief,
    data,
    content_type,
    *,
    method="external_image_task",
    provenance=None,
):
    finished, layers = await asyncio.to_thread(
        finish_external_image, data, content_type, task.payload
    )
    stored = await asyncio.to_thread(LocalAssetStorage(settings.asset_dir).store, finished)
    try:
        asset = Asset(
            order_id=order_id,
            kind="concept" if task.payload["concept_only"] else "reference_edit",
            method=method,
            storage_key=stored.storage_key,
            sha256=stored.sha256,
            media_type=stored.media_type,
            byte_size=stored.byte_size,
            width=stored.width,
            height=stored.height,
            is_demo=task.payload["concept_only"],
            image_metadata={
                "task_id": str(task.id),
                "original_output_sha256": hashlib.sha256(data).hexdigest(),
                "provider": task.payload["tool"],
                "prompt": task.payload["prompt"],
                "input_digest": brief["input_digest"],
                "revision": brief["revision"],
                "references": brief["references"],
                "owner_verified": False,
                "review_status": "pending",
                "text_layers": layers,
                "image_kind": task.payload["kind"],
                **(provenance or {}),
            },
        )
        database.add(asset)
        await database.flush()
        task.payload = {
            **task.payload,
            "status": "returned",
            "note": "图片已保存，等待核对；尚未获得交付批准。",
            "asset_id": str(asset.id),
            "asset_url": asset_view(asset).url,
            "history": [*task.payload["history"], {"status": "returned", "at": now().isoformat()}],
        }
        await database.commit()
    except Exception:
        await database.rollback()
        await asyncio.to_thread(stored.path.unlink, missing_ok=True)
        raise
