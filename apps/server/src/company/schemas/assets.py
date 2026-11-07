from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AssetView(BaseModel):
    id: UUID
    order_id: UUID
    version_id: UUID | None
    kind: str
    method: str
    sha256: str
    media_type: str
    byte_size: int
    width: int
    height: int
    is_demo: bool
    delivered_at: datetime | None
    created_at: datetime
    url: str
    can_delete: bool = False
    deletion_block_reason: str | None = None


class AssetList(BaseModel):
    items: list[AssetView]
