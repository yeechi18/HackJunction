"""Asset persistence and visibility; these services never advance an order phase."""

import asyncio
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.assets import Asset
from company.providers.images import RenderedImage
from company.providers.storage import (
    ImageLimitError,
    ImageValidationError,
    LocalAssetStorage,
    UnsupportedImageError,
)
from company.schemas.assets import AssetList, AssetView
from company.services.orders import event, load_order


def asset_view(asset: Asset) -> AssetView:
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
    )


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
        is_demo=order.is_demo,
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
    return AssetList(items=[asset_view(asset) for asset in rows])


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
    order = await load_order(database, order_id)
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
            is_demo=order.is_demo,
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
