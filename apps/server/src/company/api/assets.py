"""Bounded multipart uploads and order-authorized image reads."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from company.api.dependencies import DB, Configuration
from company.api.orders import read_access
from company.providers.storage import MAX_IMAGE_BYTES
from company.schemas.assets import AssetList, AssetView
from company.services import assets
from company.services.access import (
    OWNER_COOKIE,
    check_csrf,
    check_origin,
    get_session,
    seller_order_session,
)
from company.services.orders import load_order

router = APIRouter(prefix="/orders", tags=["assets"])
MAX_MULTIPART_BYTES = MAX_IMAGE_BYTES + 256 * 1024


async def _write_access(request: Request, database: DB, settings: Configuration, order_id: UUID):
    check_origin(request, settings)
    owner_error = None
    owner_token = request.cookies.get(OWNER_COOKIE)
    if owner_token:
        try:
            await get_session(database, owner_token, "owner")
            check_csrf(request, settings, owner_token)
            return "owner"
        except HTTPException as error:
            if error.status_code not in {401, 403}:
                raise
            owner_error = error
    try:
        seller_token, _ = await seller_order_session(request, database, order_id)
        check_csrf(request, settings, seller_token)
        return "seller"
    except HTTPException:
        if owner_error is not None and owner_error.status_code == 403:
            raise owner_error from None
        raise


async def _bounded_upload(request: Request) -> tuple[bytes, str | None]:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_MULTIPART_BYTES:
                raise HTTPException(413, "Upload request exceeds the application limit.")
        except ValueError:
            raise HTTPException(400, "Invalid upload Content-Length.") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_MULTIPART_BYTES:
            raise HTTPException(413, "Upload request exceeds the application limit.")
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    bounded_request = Request(request.scope, receive=receive)
    async with bounded_request.form(
        max_files=1, max_fields=0, max_part_size=MAX_IMAGE_BYTES
    ) as form:
        uploaded = form.get("file")
        if not isinstance(uploaded, UploadFile) or len(form) != 1:
            raise HTTPException(422, "Supply exactly one image in the file form field.")
        data = await uploaded.read(MAX_IMAGE_BYTES + 1)
        return data, uploaded.content_type


@router.post(
    "/{order_id}/assets",
    response_model=AssetView,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload(order_id: UUID, request: Request, database: DB, settings: Configuration):
    await _write_access(request, database, settings, order_id)
    await load_order(database, order_id)
    # Authentication has only read state. Do not hold its DB transaction while receiving a body.
    await database.rollback()
    data, content_type = await _bounded_upload(request)
    result = await assets.save_reference_asset(database, settings, order_id, data, content_type)
    from company.services.image_workflow import queue_image_resume

    await queue_image_resume(database, order_id, result.id)
    return result


@router.get("/{order_id}/assets", response_model=AssetList)
async def list_order_assets(order_id: UUID, request: Request, database: DB):
    role = await read_access(request, database, order_id)
    return await assets.list_assets(database, order_id, owner=role == "owner")


@router.delete("/{order_id}/assets/{asset_id}", status_code=204)
async def delete_reference(
    order_id: UUID, asset_id: UUID, request: Request, database: DB, settings: Configuration
) -> None:
    await _write_access(request, database, settings, order_id)
    await assets.delete_reference_asset(database, settings, order_id, asset_id)


@router.get("/{order_id}/assets/{asset_id}")
async def download(
    order_id: UUID, asset_id: UUID, request: Request, database: DB, settings: Configuration
):
    role = await read_access(request, database, order_id)
    asset, path = await assets.get_asset_path(
        database, settings, order_id, asset_id, owner=role == "owner"
    )
    return FileResponse(
        path,
        media_type=asset.media_type,
        filename=f"{asset.kind}-{asset.id}.png",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
