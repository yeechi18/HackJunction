"""Version-bound image preparation; no graph routing or model calls are hidden here."""

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.assets import Asset
from company.persistence.database import Database
from company.persistence.models import Command, InputSnapshot, Job, Order, OrderEvent, now
from company.persistence.production import DeliverableVersion
from company.providers.images import ImageProvider
from company.providers.mimo import ProviderError
from company.providers.storage import LocalAssetStorage
from company.services.assets import register_rendered_assets
from company.services.model_budget import check_budget
from company.services.production import CommitGuard, accepted_revision, digest, source_facts
from company.tools.image_layout import MissingReferenceImage


class ImageWorkflowError(ProviderError):
    pass


@dataclass(frozen=True)
class ImageStepResult:
    version_id: UUID
    paths: list[Path]
    asset_ids: list[UUID]
    reference_id: UUID
    reference_sha256: str
    cache_hit: bool


async def _image_records(session: AsyncSession, version: DeliverableVersion) -> list[Asset]:
    try:
        ids = [UUID(str(value)) for value in version.assets]
    except (ValueError, TypeError):
        raise ImageWorkflowError("The version image manifest is invalid.") from None
    if len(ids) != len(set(ids)):
        raise ImageWorkflowError("The version image manifest contains duplicate images.")
    rows = list(
        await session.scalars(
            select(Asset).where(
                Asset.order_id == version.order_id,
                Asset.version_id == version.id,
                Asset.id.in_(ids),
            )
        )
    )
    by_id = {row.id: row for row in rows}
    if len(rows) != len(ids):
        raise ImageWorkflowError("The immutable version is missing some image records.")
    return [by_id[value] for value in ids]


def _check_bytes(storage: LocalAssetStorage, source: dict, assets: list[dict]) -> list[Path]:
    for record in [source, *assets]:
        path = storage.resolve(record["storage_key"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ImageWorkflowError(
                "An image file changed or is unavailable; request a verified upload."
            )
    return [storage.resolve(record["storage_key"]) for record in assets]


async def render_version_images(
    database: Database,
    settings: Settings,
    order_id: UUID,
    version_id: UUID,
    command_id: UUID,
    *,
    reference_asset_id: UUID | None = None,
    round_index: int = 0,
    commit_guard: CommitGuard | None = None,
) -> ImageStepResult:
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        command = await session.get(Command, command_id)
        version = await session.get(DeliverableVersion, version_id)
        if (
            order is None
            or command is None
            or command.order_id != order_id
            or order.revision != command.expected_revision
            or command.status in {"failed", "stale", "cancelled"}
            or version is None
            or version.order_id != order_id
            or not version.text_passed
            or version.status in {"vetoed", "delivered"}
        ):
            raise ImageWorkflowError("Images require a current candidate with approved text.")
        snapshot = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.id == version.input_snapshot_id,
                InputSnapshot.order_id == order_id,
                InputSnapshot.revision == order.revision,
            )
        )
        if snapshot is None:
            raise ImageWorkflowError("The candidate no longer matches current product facts.")
        query = select(Asset).where(Asset.order_id == order_id, Asset.kind == "reference")
        if reference_asset_id is not None:
            query = query.where(Asset.id == reference_asset_id)
        reference = await session.scalar(query.order_by(Asset.created_at.desc(), Asset.id).limit(1))
        if reference is None:
            raise MissingReferenceImage("请补充可核实的商品参考图，再制作并审校完整上架包。")
        if reference.is_demo:
            raise ImageWorkflowError("Reference image mode does not match this order.")
        source = {
            "id": reference.id,
            "storage_key": reference.storage_key,
            "sha256": reference.sha256,
        }
        previous_images = await _image_records(session, version) if version.assets else []
        unchanged_source = previous_images and all(
            image.image_metadata.get("reference_sha256") == reference.sha256
            for image in previous_images
        )
        if previous_images and not unchanged_source:
            operation_key = f"images:{command_id}:{reference.id}:{reference.sha256}:{version.id}"
            child = await session.scalar(
                select(DeliverableVersion).where(DeliverableVersion.operation_key == operation_key)
            )
            if child is None:
                await check_budget(database, settings, order_id, new_candidate=True)
                latest = await session.scalar(
                    select(func.max(DeliverableVersion.version_number)).where(
                        DeliverableVersion.order_id == order_id
                    )
                )
                child = DeliverableVersion(
                    order_id=order_id,
                    command_id=command_id,
                    version_number=(latest or 0) + 1,
                    round_index=round_index,
                    parent_version_id=version.id,
                    input_snapshot_id=version.input_snapshot_id,
                    research_package_id=version.research_package_id,
                    draft=version.draft,
                    assets=[],
                    status="text_review_passed",
                    text_passed=True,
                    full_passed=False,
                    operation_key=operation_key,
                )
                session.add(child)
                await session.flush()
            version = child
            previous_images = await _image_records(session, version) if version.assets else []
        selected_version = version.id
        facts = await source_facts(session, order, snapshot)
        plan = version.draft.get("image_plan", [])
        input_snapshot_id, expected_revision = snapshot.id, order.revision
        draft_hash = digest(version.draft)
        cached = [
            {"id": image.id, "storage_key": image.storage_key, "sha256": image.sha256}
            for image in previous_images
        ]

    storage = LocalAssetStorage(settings.asset_dir)
    if cached:
        paths = await asyncio.to_thread(_check_bytes, storage, source, cached)
        return ImageStepResult(
            selected_version,
            paths,
            [item["id"] for item in cached],
            source["id"],
            source["sha256"],
            True,
        )
    await asyncio.to_thread(_check_bytes, storage, source, [])
    rendered = await ImageProvider(settings).render_bundle(
        storage.resolve(source["storage_key"]),
        facts,
        plan,
        is_demo=False,
    )
    async with database.sessions() as session, session.begin():
        if commit_guard is not None:
            await commit_guard(session)
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        version = await session.get(DeliverableVersion, selected_version)
        reference = await session.get(Asset, source["id"])
        command = await session.get(Command, command_id)
        if (
            order is None
            or order.revision != expected_revision
            or version is None
            or version.input_snapshot_id != input_snapshot_id
            or digest(version.draft) != draft_hash
            or version.status in {"vetoed", "delivered"}
            or reference is None
            or reference.sha256 != source["sha256"]
            or command is None
            or command.status in {"failed", "stale", "cancelled"}
        ):
            raise ImageWorkflowError(
                "Image inputs changed during rendering; files were not applied."
            )
        if reference_asset_id is None:
            newest = await session.scalar(
                select(Asset.id)
                .where(Asset.order_id == order_id, Asset.kind == "reference")
                .order_by(Asset.created_at.desc(), Asset.id)
                .limit(1)
            )
            if newest != source["id"]:
                raise ImageWorkflowError(
                    "A newer reference was uploaded during rendering; retry selection."
                )
        if version.assets:
            existing = await _image_records(session, version)
            if not all(
                image.image_metadata.get("reference_sha256") == source["sha256"]
                for image in existing
            ):
                raise ImageWorkflowError("A different image set already belongs to this version.")
            rows = existing
        else:
            rows = await register_rendered_assets(
                session, order_id, rendered, version_id=version.id
            )
            version.assets = [str(image.id) for image in rows]
            version.full_passed = False
            version.status = "text_review_passed"
            session.add(
                OrderEvent(
                    order_id=order_id,
                    kind="version_images_created",
                    visibility="internal",
                    message="本版实际图片已生成，等待完整图文审校。",
                    payload={
                        "version_id": str(version.id),
                        "reference_asset_id": str(source["id"]),
                        "asset_ids": [str(image.id) for image in rows],
                    },
                )
            )
        result = ImageStepResult(
            version.id,
            [storage.resolve(row.storage_key) for row in rows],
            [row.id for row in rows],
            source["id"],
            source["sha256"],
            False,
        )
    return result


async def queue_image_resume(session: AsyncSession, order_id: UUID, asset_id: UUID) -> UUID | None:
    """A successfully uploaded reference only wakes an already accepted image wait."""
    async with session.begin():
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if (
            order is None
            or order.phase != "NEEDS_IMAGE"
            or order.pending_command_id is not None
            or not await accepted_revision(session, order)
        ):
            return None
        snapshot = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == order_id,
                InputSnapshot.revision == order.revision,
            )
        )
        version = await session.scalar(
            select(DeliverableVersion)
            .where(DeliverableVersion.order_id == order_id)
            .order_by(DeliverableVersion.version_number.desc())
            .limit(1)
        )
        reference = await session.get(Asset, asset_id)
        if (
            version is None
            or snapshot is None
            or version.input_snapshot_id != snapshot.id
            or not version.text_passed
            or version.status in {"vetoed", "delivered"}
            or reference is None
            or reference.order_id != order_id
            or reference.kind != "reference"
        ):
            return None
        key = f"system:images:{asset_id}"
        prior = await session.scalar(
            select(Command).where(
                Command.order_id == order_id,
                Command.idempotency_key == key,
            )
        )
        if prior is not None:
            return prior.id
        source_command = await session.get(Command, version.command_id)
        production_command_id = (
            source_command.payload.get("production_command_id", str(source_command.id))
            if source_command
            else str(version.command_id)
        )
        payload = {
            "version_id": str(version.id),
            "reference_asset_id": str(asset_id),
            "production_command_id": production_command_id,
        }
        command = Command(
            order_id=order_id,
            kind="IMAGES",
            expected_revision=order.revision,
            idempotency_key=key,
            request_hash=digest(payload),
            payload=payload,
        )
        session.add(command)
        await session.flush()
        session.add(Job(order_id=order_id, command_id=command.id, kind="RESUME"))
        order.pending_command_id, order.updated_at = command.id, now()
        session.add(
            OrderEvent(
                order_id=order_id,
                kind="image_resume_queued",
                message="补充参考图已排队，将继续制作和审校图片。",
                payload={"version_id": str(version.id), "command_id": str(command.id)},
            )
        )
        return command.id
