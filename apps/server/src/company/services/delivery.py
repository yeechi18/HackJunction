"""Freeze a reviewed version, build its public package, then atomically release it."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.assets import Asset
from company.persistence.database import Database
from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import (
    Command,
    HumanDecision,
    InputSnapshot,
    Message,
    Order,
    OrderEvent,
    now,
)
from company.persistence.production import DeliverableVersion, ResearchPackage, ReviewReport
from company.providers.storage import LocalAssetStorage
from company.schemas.deliveries import DeliveryList, DeliveryView, VetoResult
from company.schemas.listings import ListingDraft
from company.services.orders import load_order
from company.services.production import digest as production_digest
from company.tools.build_package import build_package

CommitGuard = Callable[[AsyncSession], Awaitable[object]]
PACKAGE_KEY = re.compile(r"^[0-9a-f]{32}\.zip$")


class DeliveryError(HTTPException):
    def __init__(self, message: str):
        super().__init__(409, message)


@dataclass(frozen=True)
class PreparedDelivery:
    id: UUID
    order_id: UUID
    version_id: UUID
    command_id: UUID
    input_snapshot_id: UUID
    input_revision: int
    version_number: int
    snapshot: dict
    content_hash: str
    package_key: str
    delivered_at: datetime
    asset_records: list[dict]
    review_command_id: UUID
    review_operation_key: str
    review_result: dict
    fingerprint: str


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def _package_path(settings: Settings, key: str) -> Path:
    if not PACKAGE_KEY.fullmatch(key):
        raise DeliveryError("Delivery package reference is invalid.")
    root = (settings.asset_dir / "packages").resolve()
    path = (root / key).resolve()
    if path.parent != root:
        raise DeliveryError("Delivery package reference is invalid.")
    return path


def delivery_view(record: DeliverySnapshot) -> DeliveryView:
    return DeliveryView(
        id=record.id,
        order_id=record.order_id,
        version_id=record.version_id,
        version_number=record.version_number,
        content_hash=record.content_hash,
        listing=record.snapshot["listing"],
        faqs=record.snapshot["faqs"],
        pricing=record.snapshot["pricing"],
        images=record.snapshot["images"],
        delivered_at=record.delivered_at,
        download_url=f"/api/v1/orders/{record.order_id}/download?version_id={record.version_id}",
    )


def _asset_ids(values: list) -> list[UUID]:
    try:
        ids = [
            UUID(str(item.get("asset_id", item.get("id"))) if isinstance(item, dict) else str(item))
            for item in values
        ]
    except (ValueError, TypeError, AttributeError):
        raise DeliveryError("The version image manifest is invalid.") from None
    if not ids or len(ids) != len(set(ids)):
        raise DeliveryError("A complete reviewed image set is required before delivery.")
    return ids


async def _decision_records(
    session: AsyncSession,
    order_id: UUID,
    version_id: UUID,
    command_id: UUID,
    action: str,
) -> tuple[Order, DeliverableVersion, Command, HumanDecision]:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    version = await session.get(DeliverableVersion, version_id)
    command = await session.get(Command, command_id)
    decision = await session.scalar(
        select(HumanDecision).where(
            HumanDecision.order_id == order_id,
            HumanDecision.command_id == command_id,
            HumanDecision.action == action,
            HumanDecision.version_id == version_id,
        )
    )
    if (
        order is None
        or version is None
        or version.order_id != order_id
        or command is None
        or command.order_id != order_id
        or command.kind != action.upper()
        or decision is None
    ):
        raise DeliveryError("The owner decision does not match this order and version.")
    requested_version = command.payload.get("version_id")
    if requested_version is not None and str(requested_version) != str(version_id):
        raise DeliveryError("The owner decision names a different version.")
    if command.status in {"failed", "stale", "cancelled"}:
        raise DeliveryError("The owner decision is no longer applicable.")
    return order, version, command, decision


async def _prepare(
    session: AsyncSession,
    order_id: UUID,
    version_id: UUID,
    command_id: UUID,
) -> PreparedDelivery | DeliverySnapshot:
    order, version, command, _ = await _decision_records(
        session, order_id, version_id, command_id, "release"
    )
    existing = await session.scalar(
        select(DeliverySnapshot).where(
            DeliverySnapshot.order_id == order_id, DeliverySnapshot.version_id == version_id
        )
    )
    if existing is not None:
        if existing.release_command_id != command_id and (
            order.phase != "AWAITING_SELLER"
            or order.revision != existing.input_revision
            or command.expected_revision != order.revision
        ):
            raise DeliveryError("This historical version was already delivered; refresh the order.")
        return existing
    if order.phase != "AWAITING_RELEASE" or command.expected_revision != order.revision:
        raise DeliveryError("The order is not awaiting release for this input revision.")
    latest = await session.scalar(
        select(DeliverableVersion.id)
        .where(DeliverableVersion.order_id == order_id)
        .order_by(DeliverableVersion.version_number.desc())
        .limit(1)
    )
    snapshot = await session.scalar(
        select(InputSnapshot).where(
            InputSnapshot.order_id == order_id, InputSnapshot.revision == order.revision
        )
    )
    if latest != version.id or snapshot is None or version.input_snapshot_id != snapshot.id:
        raise DeliveryError(
            "Only the current version bound to current product inputs may be released."
        )
    if version.status != "review_passed" or not version.full_passed or not version.text_passed:
        raise DeliveryError("Complete text and image review must pass before release.")
    review = await session.scalar(
        select(ReviewReport)
        .where(ReviewReport.order_id == order_id, ReviewReport.version_id == version_id)
        .order_by(ReviewReport.created_at.desc(), ReviewReport.id)
        .limit(1)
    )
    if (
        review is None
        or review.scope != "text_and_images"
        or not review.full_passed
        or not review.text_passed
        or review.result.get("passed") is not True
        or review.result.get("requires_images") is not False
        or any(item.get("severity") == "blocking" for item in review.result.get("issues", []))
    ):
        raise DeliveryError(
            "The latest complete review has not approved this image-and-text bundle."
        )
    ids = _asset_ids(version.assets)
    images = list(
        await session.scalars(select(Asset).where(Asset.order_id == order_id, Asset.id.in_(ids)))
    )
    by_id = {image.id: image for image in images}
    if len(by_id) != len(ids):
        raise DeliveryError("Some reviewed images are unavailable or belong to another order.")
    images = [by_id[asset_id] for asset_id in ids]
    if any(
        image.version_id != version.id or image.kind == "reference" or image.is_demo
        for image in images
    ):
        raise DeliveryError("Generated images must belong to this exact version and mode.")
    research = await session.get(ResearchPackage, version.research_package_id)
    if (
        research is None
        or research.order_id != order_id
        or research.input_snapshot_id != snapshot.id
    ):
        raise DeliveryError("The version's research package does not match current inputs.")
    draft = ListingDraft.model_validate(
        {key: value for key, value in version.draft.items() if key in ListingDraft.model_fields}
    )
    price_range = research.result.get("suggested_listing_price_range")
    public_range = (
        {
            key: price_range[key]
            for key in ("low", "high", "currency", "is_demo")
            if key in price_range
        }
        if isinstance(price_range, dict)
        else None
    )
    payload = {
        "listing": {
            "title": draft.title,
            "bullets": draft.bullets,
            "description": draft.description,
        },
        "faqs": [item.model_dump() for item in draft.faqs],
        "pricing": {
            "suggested_listing_price_range": public_range,
            "notes": str(research.result.get("pricing_notes") or "Price guidance unavailable."),
        },
        "images": [
            {
                "id": str(image.id),
                "kind": image.kind,
                "sha256": image.sha256,
                "width": image.width,
                "height": image.height,
                "media_type": image.media_type,
                "url": f"/api/v1/orders/{order_id}/assets/{image.id}",
            }
            for image in images
        ],
        "marketplace": "AMAZON_US",
        "language": "en-US",
    }
    payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    asset_records = [
        {"id": str(image.id), "storage_key": image.storage_key, "sha256": image.sha256}
        for image in images
    ]
    fingerprint = _digest(
        {
            "revision": order.revision,
            "version": str(version.id),
            "input": str(snapshot.id),
            "draft": version.draft,
            "assets": asset_records,
            "review_id": str(review.id),
            "review_result": review.result,
            "review_key": review.operation_key,
            "research": research.result,
        }
    )
    delivery_id = uuid4()
    return PreparedDelivery(
        delivery_id,
        order_id,
        version_id,
        command_id,
        snapshot.id,
        order.revision,
        version.version_number,
        payload,
        _digest({"version_id": str(version_id), "input_snapshot_id": str(snapshot.id), **payload}),
        f"{delivery_id.hex}.zip",
        now(),
        asset_records,
        review.command_id,
        review.operation_key,
        review.result,
        fingerprint,
    )


def _verify_and_build(settings: Settings, prepared: PreparedDelivery) -> tuple[str, int]:
    storage = LocalAssetStorage(settings.asset_dir)
    paths = []
    dependencies = []
    for image in prepared.asset_records:
        path = storage.resolve(image["storage_key"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != image["sha256"]:
            raise DeliveryError("A reviewed image is missing or its content hash changed.")
        paths.append(path)
        dependencies.append((str(path.resolve()), image["sha256"]))
    image_hash = production_digest(dependencies)
    recorded_hashes = prepared.review_result.get("asset_hashes")
    if recorded_hashes is not None:
        expected = {image["id"]: image["sha256"] for image in prepared.asset_records}
        if recorded_hashes != expected:
            raise DeliveryError("The complete review belongs to a different image set.")
    else:
        expected_key = (
            f"{prepared.review_command_id}:review:{prepared.version_id}:{image_hash[:20]}"
        )
        if prepared.review_operation_key != expected_key:
            raise DeliveryError("The complete review does not match current image content.")
    package = _package_path(settings, prepared.package_key)
    pricing_notes = prepared.snapshot["pricing"]["notes"]
    price_range = prepared.snapshot["pricing"]["suggested_listing_price_range"]
    if price_range and all(key in price_range for key in ("low", "high", "currency")):
        pricing_notes = (
            f"Suggested product price range: {price_range['low']} - {price_range['high']} "
            f"{price_range['currency']}\n\n{pricing_notes}"
        )
        if price_range.get("is_demo"):
            pricing_notes += "\n\nThis price range uses fictional demo observations."
    try:
        build_package(
            package,
            listing=prepared.snapshot["listing"],
            faqs=prepared.snapshot["faqs"],
            pricing_notes=pricing_notes,
            manifest={
                "order_id": str(prepared.order_id),
                "version_id": str(prepared.version_id),
                "created_at": prepared.delivered_at.isoformat(),
                "marketplace": "AMAZON_US",
                "language": "en-US",
                "content_hash": prepared.content_hash,
            },
            assets=paths,
            asset_root=storage.root,
        )
        with ZipFile(package) as archive:
            zipped_hashes = json.loads(archive.read("manifest.json"))["asset_hashes"]
        if [zipped_hashes[name] for name in sorted(zipped_hashes)] != [
            image["sha256"] for image in prepared.asset_records
        ]:
            raise DeliveryError("Image content changed while building the delivery package.")
        return hashlib.sha256(package.read_bytes()).hexdigest(), package.stat().st_size
    except DeliveryError:
        raise
    except (ValueError, OSError, KeyError):
        raise DeliveryError("The reviewed public package could not be built safely.") from None


async def _verify_package(settings: Settings, record: DeliverySnapshot) -> Path:
    path = _package_path(settings, record.package_key)
    if not path.is_file():
        raise DeliveryError("The delivery package is unavailable; contact the owner.")
    digest = await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest())
    if digest != record.package_sha256:
        raise DeliveryError("The stored delivery package no longer matches its immutable snapshot.")
    return path


async def release_version(
    database: Database,
    settings: Settings,
    order_id: UUID,
    version_id: UUID,
    command_id: UUID,
    *,
    commit_guard: CommitGuard | None = None,
) -> DeliveryView:
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        prepared = await _prepare(session, order_id, version_id, command_id)
    if isinstance(prepared, DeliverySnapshot):
        await _verify_package(settings, prepared)
        async with database.sessions() as session, session.begin():
            if commit_guard is not None:
                await commit_guard(session)
            order, _, command, decision = await _decision_records(
                session, order_id, version_id, command_id, "release"
            )
            command.status, decision.status = "applied", "applied"
            if order.pending_command_id == command.id:
                order.pending_command_id = None
        return delivery_view(prepared)
    # All ZIP reads/writes and full-image hash checks happen outside ORM transactions.
    package_hash, package_size = await asyncio.to_thread(_verify_and_build, settings, prepared)
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        current = await _prepare(session, order_id, version_id, command_id)
        if isinstance(current, DeliverySnapshot):
            order, _, command, decision = await _decision_records(
                session, order_id, version_id, command_id, "release"
            )
            command.status, decision.status = "applied", "applied"
            if order.pending_command_id == command.id:
                order.pending_command_id = None
            return delivery_view(current)
        if current.fingerprint != prepared.fingerprint:
            raise DeliveryError(
                "Version inputs changed before commit; the package was not released."
            )
        order, version, command, decision = await _decision_records(
            session, order_id, version_id, command_id, "release"
        )
        receipt = DeliverySnapshot(
            id=prepared.id,
            order_id=order_id,
            version_id=version_id,
            release_command_id=command_id,
            input_snapshot_id=prepared.input_snapshot_id,
            input_revision=prepared.input_revision,
            version_number=prepared.version_number,
            snapshot=prepared.snapshot,
            content_hash=prepared.content_hash,
            package_key=prepared.package_key,
            package_sha256=package_hash,
            package_byte_size=package_size,
            delivered_at=prepared.delivered_at,
        )
        session.add(receipt)
        for asset_id in _asset_ids(version.assets):
            asset = await session.get(Asset, asset_id)
            asset.delivered_at = prepared.delivered_at
        version.status = "delivered"
        order.phase, order.updated_at = "AWAITING_SELLER", now()
        command.status, decision.status = "applied", "applied"
        if order.pending_command_id == command.id:
            order.pending_command_id = None
        session.add(
            Message(
                order_id=order_id,
                role="assistant",
                input_revision=order.revision,
                content=f"第 {version.version_number} 版上架包已交付，"
                "包含英文文案、商品图片和定价资料。"
                "请查看后提出修改意见，或确认成品和商品售价；目前尚未发布到 Amazon。",
            )
        )
        session.add(
            OrderEvent(
                order_id=order_id,
                kind="version_delivered",
                message=f"第 {version.version_number} 版已交付，等待卖家反馈或确认。",
                payload={
                    "version_id": str(version_id),
                    "delivery_id": str(receipt.id),
                    "content_hash": prepared.content_hash,
                },
            )
        )
        await session.flush()
        return delivery_view(receipt)


async def veto_version(
    database: Database,
    settings: Settings,
    order_id: UUID,
    version_id: UUID,
    command_id: UUID,
    *,
    reason: str,
    commit_guard: CommitGuard | None = None,
) -> VetoResult:
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise DeliveryError("A specific veto reason of at most 2000 characters is required.")
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        order, version, command, decision = await _decision_records(
            session, order_id, version_id, command_id, "veto"
        )
        if version.status == "vetoed" and decision.status == "applied":
            previous = await session.scalar(
                select(OrderEvent)
                .where(
                    OrderEvent.order_id == order_id,
                    OrderEvent.kind == "version_vetoed",
                    OrderEvent.payload["command_id"].astext == str(command_id),
                )
                .order_by(OrderEvent.id.desc())
                .limit(1)
            )
            return VetoResult(
                version_id=version_id, reason=previous.payload["reason"] if previous else reason
            )
        already_delivered = await session.scalar(
            select(DeliverySnapshot.id).where(DeliverySnapshot.version_id == version_id)
        )
        current = await session.scalar(
            select(InputSnapshot.id).where(
                InputSnapshot.order_id == order_id, InputSnapshot.revision == order.revision
            )
        )
        latest = await session.scalar(
            select(DeliverableVersion.id)
            .where(DeliverableVersion.order_id == order_id)
            .order_by(DeliverableVersion.version_number.desc())
            .limit(1)
        )
        if (
            already_delivered is not None
            or version.status == "delivered"
            or order.phase != "AWAITING_RELEASE"
            or version.input_snapshot_id != current
            or latest != version_id
            or command.expected_revision != order.revision
        ):
            raise DeliveryError("Only a current, undelivered candidate can be vetoed.")
        version.status = "vetoed"
        command.status, decision.status = "applied", "applied"
        if order.pending_command_id == command.id:
            order.pending_command_id = None
        session.add(
            OrderEvent(
                order_id=order_id,
                kind="version_vetoed",
                visibility="internal",
                message="负责人否决待交付版本，修改意见已记录，尚未向卖家交付。",
                payload={
                    "version_id": str(version_id),
                    "command_id": str(command_id),
                    "reason": reason,
                },
            )
        )
        # The graph, not this service, routes back into generation.
        return VetoResult(version_id=version_id, reason=reason)


async def list_deliveries(session: AsyncSession, order_id: UUID) -> DeliveryList:
    await load_order(session, order_id)
    records = await session.scalars(
        select(DeliverySnapshot)
        .where(DeliverySnapshot.order_id == order_id)
        .order_by(DeliverySnapshot.delivered_at.desc(), DeliverySnapshot.id)
    )
    return DeliveryList(items=[delivery_view(record) for record in records])


async def download_package(
    session: AsyncSession,
    settings: Settings,
    order_id: UUID,
    version_id: UUID | None = None,
) -> tuple[DeliverySnapshot, Path]:
    await load_order(session, order_id)
    query = select(DeliverySnapshot).where(DeliverySnapshot.order_id == order_id)
    if version_id is not None:
        query = query.where(DeliverySnapshot.version_id == version_id)
    record = await session.scalar(query.order_by(DeliverySnapshot.delivered_at.desc()).limit(1))
    if record is None:
        raise HTTPException(404, "Delivered version not found.")
    return record, await _verify_package(settings, record)
