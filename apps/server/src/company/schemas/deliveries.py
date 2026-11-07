from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from company.schemas.listings import FAQ


class PublicListing(BaseModel):
    title: str
    bullets: list[str]
    description: str


class DeliveryImage(BaseModel):
    id: UUID
    kind: str
    sha256: str
    width: int
    height: int
    media_type: str
    url: str


class DeliveryView(BaseModel):
    id: UUID
    order_id: UUID
    version_id: UUID
    version_number: int
    content_hash: str
    listing: PublicListing
    faqs: list[FAQ]
    pricing: dict
    images: list[DeliveryImage]
    delivered_at: datetime
    download_url: str


class DeliveryList(BaseModel):
    items: list[DeliveryView]


class VetoResult(BaseModel):
    version_id: UUID
    status: str = "vetoed"
    reason: str
