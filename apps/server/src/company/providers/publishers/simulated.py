"""Durable local simulation; it never contacts Amazon or claims an actual sale."""

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from company.config import Settings
from company.persistence.database import Database
from company.persistence.publication import SimulatedListing
from company.providers.mimo import ProviderError
from company.providers.publishers.base import PublisherResult, ValidationResult


class SimulatedPublisher:
    mode = "simulated"

    def __init__(self, settings: Settings, database: Database):
        self.settings, self.database = settings, database

    async def validate(self, snapshot: dict) -> ValidationResult:
        required = {
            "order_id",
            "version_id",
            "listing",
            "images",
            "listing_price",
            "currency",
            "seller_sku",
            "marketplace",
            "delivery_hash",
        }
        issues = []
        if not required.issubset(snapshot) or not snapshot.get("images"):
            issues.append("A complete confirmed listing and image manifest is required.")
        if snapshot.get("currency") != "USD" or snapshot.get("marketplace") != "AMAZON_US":
            issues.append("This simulation supports only the configured US listing contract.")
        return ValidationResult(not issues, tuple(issues))

    def result(self, record: SimulatedListing) -> PublisherResult:
        return PublisherResult(
            status="SIMULATED",
            external_id=record.public_id,
            listing_url=(
                f"{self.settings.public_app_url.rstrip('/')}/demo-listings/{record.public_id}"
                if record.snapshot.get("is_demo") is True
                else None
            ),
        )

    async def submit(self, snapshot: dict, *, operation_key: str) -> PublisherResult:
        validation = await self.validate(snapshot)
        if not validation.valid:
            raise ProviderError("Simulation rejected an incomplete confirmation snapshot.")
        snapshot_hash = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        async with self.database.sessions() as session, session.begin():
            public_id = uuid5(NAMESPACE_URL, "hackjunction-simulation:" + operation_key).hex
            await session.execute(
                insert(SimulatedListing)
                .values(
                    order_id=UUID(snapshot["order_id"]),
                    operation_key=operation_key,
                    public_id=public_id,
                    snapshot=snapshot,
                    snapshot_hash=snapshot_hash,
                )
                .on_conflict_do_nothing(index_elements=["operation_key"])
            )
            record = await session.scalar(
                select(SimulatedListing).where(
                    SimulatedListing.operation_key == operation_key,
                )
            )
            if record.snapshot_hash != snapshot_hash:
                raise ProviderError(
                    "Simulation operation key already belongs to a different snapshot."
                )
            return self.result(record)

    async def get_status(self, *, operation_key: str) -> PublisherResult:
        async with self.database.sessions() as session:
            record = await session.scalar(
                select(SimulatedListing).where(
                    SimulatedListing.operation_key == operation_key,
                )
            )
            return self.result(record) if record else PublisherResult(status="NOT_FOUND")
