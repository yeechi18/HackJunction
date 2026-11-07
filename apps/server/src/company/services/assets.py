"""Asset persistence and visibility; these services never advance an order phase."""

import asyncio
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.assets import Asset
from company.persistence.models import Command, Job
from company.providers.images import RenderedImage
from company.providers.storage import (
    ImageLimitError,
    ImageValidationError,
    LocalAssetStorage,
    UnsupportedImageError,
)
from company.schemas.assets import AssetList, AssetView
from company.services.orders import event, load_order


def asset_view(asset: Asset, *, deletion_block_reason: str | None = None) -> AssetView:
    return AssetView(
        id=asset.id,
        order_id=asset.order_id,
        version_id=asset.version_id,
        kind=asset.kind,
        method=asset.method,
        sha256=asset.sha256,
        media_type=asset.media_type,
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        is_demo=asset.is_demo,
        delivered_at=asset.delivered_at,
        created_at=asset.created_at,
        url=f"/api/v1/orders/{asset.order_id}/assets/{asset.id}",
        can_delete=asset.kind == "reference" and deletion_block_reason is None,
        deletion_block_reason=deletion_block_reason,
    )


async def reference_deletion_state(database: AsyncSession, order_id: UUID) -> tuple[set[str], bool]:
    metadata = await database.scalars(
        select(Asset.image_metadata).where(Asset.order_id == order_id, Asset.kind != "reference")
    )
    used_hashes = {item["reference_sha256"] for item in metadata if item.get("reference_sha256")}
    pending_production = await database.scalar(
        select(Job.id)
        .join(Command, Command.id == Job.command_id)
        .where(
            Job.order_id == order_id,
            Job.status.in_(["queued", "running"]),
            Command.kind.not_in(["START", "MESSAGE", "SUPPLEMENT"]),
        )
        .limit(1)
    )
    return used_hashes, pending_production is not None


def deletion_reason(asset: Asset, used_hashes: set[str], pending: bool) -> str | None:
    if asset.kind != "reference":
        return "成品图片随版本保留，不能单独删除。"
    if asset.sha256 in used_hashes:
        return "此参考图已用于图片制作，需保留版本依据；可上传新图用于后续版本。"
    if pending:
        return "生产任务正在排队或处理中，请等待处理完成后再删除参考图。"
    return None


async def delete_reference_asset(
    database: AsyncSession, settings: Settings, order_id: UUID, asset_id: UUID
) -> None:
    # Image selection and production commits use the same order lock.
    await load_order(database, order_id, lock=True)
    asset = await database.scalar(
        select(Asset).where(Asset.id == asset_id, Asset.order_id == order_id).with_for_update()
    )
    if asset is None:
        raise HTTPException(404, "Asset not found.")
    used, pending = await reference_deletion_state(database, order_id)
    reason = deletion_reason(asset, used, pending)
    if reason:
        raise HTTPException(409, reason)
    path = LocalAssetStorage(settings.asset_dir).resolve(asset.storage_key)
    await database.delete(asset)
    event(database, order_id, "reference_image_deleted", "未用于生产的商品参考图已删除。")
    await database.commit()
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError:
        # A failed file cleanup must not replay a committed deletion; the record is inaccessible.
        pass


async def save_reference_asset(
    database: AsyncSession,
    settings: Settings,
    order_id: UUID,
    data: bytes,
    content_type: str | None,
) -> AssetView:
    order = await load_order(database, order_id)
    storage = LocalAssetStorage(settings.asset_dir)
    try:
        stored = await asyncio.to_thread(storage.put_image, data, content_type)
    except ImageLimitError as error:
        raise HTTPException(413, str(error)) from None
    except UnsupportedImageError as error:
        raise HTTPException(415, str(error)) from None
    except ImageValidationError as error:
        raise HTTPException(422, str(error)) from None
    asset = Asset(
        order_id=order.id,
        version_id=None,
        kind="reference",
        method="upload_normalized_png",
        storage_key=stored.storage_key,
        sha256=stored.sha256,
        media_type=stored.media_type,
        byte_size=stored.byte_size,
        width=stored.width,
        height=stored.height,
        is_demo=False,
        image_metadata={"exif_removed": True},
        delivered_at=None,
    )
    database.add(asset)
    event(database, order.id, "reference_image_uploaded", "商品参考图已保存，尚未作为交付成品。")
    try:
        await database.commit()
    except Exception:
        await database.rollback()
        await asyncio.to_thread(stored.path.unlink, missing_ok=True)
        raise HTTPException(503, "Asset metadata could not be saved.") from None
    return asset_view(asset)


async def list_assets(database: AsyncSession, order_id: UUID, *, owner: bool) -> AssetList:
    await load_order(database, order_id)
    query = select(Asset).where(Asset.order_id == order_id)
    if not owner:
        query = query.where(
            or_(
                Asset.kind == "reference",
                Asset.delivered_at.is_not(None) & Asset.version_id.is_not(None),
            )
        )
    rows = await database.scalars(query.order_by(Asset.created_at, Asset.id))
    used, pending = await reference_deletion_state(database, order_id)
    return AssetList(
        items=[
            asset_view(
                asset,
                deletion_block_reason="负责人仅可查看素材，不能修改。"
                if owner
                else deletion_reason(asset, used, pending),
            )
            for asset in rows
        ]
    )


async def get_asset_path(
    database: AsyncSession,
    settings: Settings,
    order_id: UUID,
    asset_id: UUID,
    *,
    owner: bool,
) -> tuple[Asset, Path]:
    await load_order(database, order_id)
    query = select(Asset).where(Asset.id == asset_id, Asset.order_id == order_id)
    if not owner:
        query = query.where(
            or_(
                Asset.kind == "reference",
                Asset.delivered_at.is_not(None) & Asset.version_id.is_not(None),
            )
        )
    asset = await database.scalar(query)
    if asset is None:
        raise HTTPException(404, "Asset not found.")
    try:
        path = LocalAssetStorage(settings.asset_dir).resolve(asset.storage_key)
    except ValueError:
        raise HTTPException(404, "Asset file is unavailable.") from None
    if not path.is_file():
        raise HTTPException(404, "Asset file is unavailable.")
    return asset, path


async def register_rendered_assets(
    database: AsyncSession,
    order_id: UUID,
    rendered: list[RenderedImage],
    *,
    version_id: UUID | None = None,
) -> list[Asset]:
    """Flush but do not commit: the caller commits assets with its exact version snapshot."""
    await load_order(database, order_id)
    records = []
    for image in rendered:
        asset = Asset(
            order_id=order_id,
            version_id=version_id,
            kind=image.kind,
            method=image.method,
            storage_key=image.storage_key,
            sha256=image.sha256,
            media_type="image/png",
            byte_size=image.path.stat().st_size,
            width=image.width,
            height=image.height,
            is_demo=False,
            delivered_at=None,
            image_metadata={
                "text_layers": image.text_layers,
                "reference_sha256": image.reference_sha256,
                "requires_subject_verification": True,
            },
        )
        database.add(asset)
        records.append(asset)
    await database.flush()
    return records
