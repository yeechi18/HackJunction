"""Order-scoped seller actions and explicitly public fictional demo listings."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from company.api.dependencies import DB, Configuration
from company.api.orders import read_access, seller_write
from company.persistence.assets import Asset
from company.providers.storage import LocalAssetStorage
from company.schemas.publication import (
    ConfirmationInput,
    ConfirmationQueued,
    DemoListingView,
    FeedbackInput,
    FeedbackQueued,
    PublicationView,
)
from company.services.publication import (
    PublicationError,
    _verify_assets,
    public_demo,
    publication_view,
    queue_confirmation,
    queue_feedback,
)

router = APIRouter(tags=["seller publication"])


@router.post("/orders/{order_id}/confirmations", response_model=ConfirmationQueued, status_code=202)
async def confirm(
    order_id: UUID,
    body: ConfirmationInput,
    request: Request,
    database: DB,
    settings: Configuration,
    idempotency_key: str = Header(min_length=1, max_length=128),
) -> ConfirmationQueued:
    await seller_write(request, database, settings, order_id)
    try:
        return await queue_confirmation(database, settings, order_id, body, idempotency_key)
    except PublicationError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/orders/{order_id}/feedback", response_model=FeedbackQueued, status_code=202)
async def feedback(
    order_id: UUID,
    body: FeedbackInput,
    request: Request,
    database: DB,
    settings: Configuration,
    idempotency_key: str = Header(min_length=1, max_length=128),
) -> FeedbackQueued:
    await seller_write(request, database, settings, order_id)
    try:
        return await queue_feedback(database, order_id, body, idempotency_key)
    except PublicationError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/orders/{order_id}/publication", response_model=PublicationView)
async def publication(order_id: UUID, request: Request, database: DB) -> PublicationView:
    await read_access(request, database, order_id)
    return await publication_view(database, order_id)


@router.get("/demo-listings/{public_id}", response_model=DemoListingView)
async def demo_listing(public_id: str, database: DB) -> DemoListingView:
    _, view = await public_demo(database, public_id)
    return view


@router.get("/demo-listings/{public_id}/assets/{asset_id}")
async def demo_image(
    public_id: str,
    asset_id: UUID,
    database: DB,
    settings: Configuration,
):
    record, _ = await public_demo(database, public_id)
    manifest = next(
        (image for image in record.snapshot["images"] if image["id"] == str(asset_id)), None
    )
    image = await database.get(Asset, asset_id)
    if (
        manifest is None
        or image is None
        or image.order_id != record.order_id
        or not image.is_demo
        or image.delivered_at is None
        or str(image.version_id) != record.snapshot["version_id"]
        or image.sha256 != manifest["sha256"]
    ):
        raise HTTPException(404, "Demo image not found.")
    try:
        await asyncio.to_thread(
            _verify_assets,
            settings,
            [{"storage_key": image.storage_key, "sha256": manifest["sha256"]}],
        )
    except PublicationError:
        raise HTTPException(404, "Demo image not found.") from None
    path = LocalAssetStorage(settings.asset_dir).resolve(image.storage_key)
    return FileResponse(
        path,
        media_type=image.media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
