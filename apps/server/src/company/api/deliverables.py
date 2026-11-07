from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from company.api.dependencies import DB, Configuration
from company.api.orders import read_access
from company.schemas.deliveries import DeliveryList
from company.services.delivery import download_package, list_deliveries

router = APIRouter(prefix="/orders", tags=["deliverables"])


@router.get("/{order_id}/deliverables", response_model=DeliveryList)
async def delivered_versions(order_id: UUID, request: Request, database: DB):
    await read_access(request, database, order_id)
    return await list_deliveries(database, order_id)


@router.get("/{order_id}/download")
async def download(
    order_id: UUID,
    request: Request,
    database: DB,
    settings: Configuration,
    version_id: UUID | None = None,
):
    await read_access(request, database, order_id)
    record, path = await download_package(database, settings, order_id, version_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"listing-v{record.version_number}.zip",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
