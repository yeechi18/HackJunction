"""Version-bound image preparation; no graph routing or model calls are hidden here."""

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.assets import Asset
from company.persistence.database import Database
from company.persistence.models import Command, InputSnapshot, Job, Order, OrderEvent, now
from company.persistence.production import DeliverableVersion
from company.providers.images import ImageProvider, RenderedImage
from company.providers.mimo import ProviderError
from company.providers.storage import LocalAssetStorage
from company.services.assets import register_rendered_assets
from company.services.image_tasks import plan_images
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
        query = select(Asset).where(
            Asset.order_id == order_id,
            or_(
                Asset.kind == "reference",
                and_(
                    Asset.kind == "reference_edit",
                    Asset.image_metadata["revision"].as_integer() == order.revision,
                    Asset.image_metadata["owner_verified"].as_boolean().is_(True),
                ),
            ),
        )
        if reference_asset_id is not None:
            query = query.where(Asset.id == reference_asset_id)
        reference = await session.scalar(query.order_by(Asset.created_at.desc(), Asset.id).limit(1))
        if reference is None:
            raise MissingReferenceImage("请补充可核实的商品参考图，再制作并审校完整上架包。")
        if reference.is_demo:
            raise ImageWorkflowError("Reference image mode does not match this order.")
        original_references = list(
            await session.scalars(
                select(Asset)
                .where(
                    Asset.order_id == order_id,
                    Asset.kind == "reference",
                )
                .order_by(Asset.created_at, Asset.id)
            )
        )
        confirmed = [f for f in snapshot.facts if f.get("confirmed") and f.get("value")]
        current_digest = digest(
            {
                "revision": order.revision,
                "product_name": order.product_name,
                "facts": confirmed,
                "references": [{"id": str(a.id), "sha256": a.sha256} for a in original_references],
                "product_details": order.product_details,
                "notes": snapshot.notes,
            }
        )
        if plan_images(
            order.product_name,
            order.product_details,
            snapshot.notes,
            confirmed,
            [{"is_demo": a.is_demo} for a in original_references],
        )["concept_only"]:
            raise MissingReferenceImage(
                "当前参考为概念或虚拟演示，需补充真实实物图后才能制作正式交付。"
            )
        external = []
        if reference.kind == "reference_edit" and reference.image_metadata.get("task_id"):
            candidates = await session.scalars(
                select(Asset)
                .where(
                    Asset.order_id == order_id,
                    Asset.kind == "reference_edit",
                    Asset.image_metadata["input_digest"].as_string() == current_digest,
                    Asset.image_metadata["owner_verified"].as_boolean().is_(True),
                )
                .order_by(Asset.created_at.desc(), Asset.id)
            )
            selected = {}
            for candidate in candidates:
                kind = candidate.image_metadata.get("image_kind")
                if kind in {"main", "lifestyle", "infographic"} and kind not in selected:
                    selected[kind] = candidate
            external = [selected[k] for k in ("main", "lifestyle", "infographic") if k in selected]
            if reference not in external or "main" not in selected:
                raise ImageWorkflowError(
                    "外部成图已过期或缺少已核对主图；请回传并核对本版主图后重试。"
                )
        external_records = [
            {
                "id": str(a.id),
                "storage_key": a.storage_key,
                "sha256": a.sha256,
                "kind": a.image_metadata["image_kind"],
                "method": a.method,
                "width": a.width,
                "height": a.height,
                "metadata": a.image_metadata,
            }
            for a in external
        ]
        if settings.seedream_enabled and not external_records:
            raise MissingReferenceImage(
                "当前使用 Seedream 制图，请在研究与制图中查看自动任务；"
                "主图及所需附图回传并核对后，提交新候选继续完整图文审校。"
            )
        source = {
            "id": reference.id,
            "storage_key": reference.storage_key,
            "sha256": reference.sha256,
        }
        previous_images = await _image_records(session, version) if version.assets else []
        external_signature = digest(external_records) if external else None
        unchanged_source = previous_images and all(
            image.image_metadata.get("reference_sha256") == reference.sha256
            and image.image_metadata.get("external_signature") == external_signature
            for image in previous_images
        )
        if (previous_images and not unchanged_source) or (external and not previous_images):
            operation_key = "images:" + digest(
                [str(command_id), str(version.id), reference.sha256, external_signature]
            )
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
                    draft={
                        **version.draft,
                        "image_plan": [
                            {
                                "purpose": "detail" if a["kind"] == "lifestyle" else a["kind"],
                                "layout": "Owner-verified reference-preserving product image.",
                                "text_overlay": "",
                                "fact_ids": [],
                            }
                            for a in external_records
                        ],
                    }
                    if external
                    else version.draft,
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
    if external_records:
        await asyncio.to_thread(_check_bytes, storage, source, external_records)
        rendered = []
        for record in external_records:
            # Copy immutable output into a new version; do not re-layout the product.
            stored = await asyncio.to_thread(
                storage.put_image, storage.resolve(record["storage_key"]).read_bytes(), "image/png"
            )
            rendered.append(
                RenderedImage(
                    path=stored.path,
                    storage_key=stored.storage_key,
                    kind="main_candidate" if record["kind"] == "main" else record["kind"],
                    method=record["method"],
                    sha256=stored.sha256,
                    width=stored.width,
                    height=stored.height,
                    text_layers=record["metadata"].get("text_layers", []),
                    reference_sha256=source["sha256"],
                    metadata={
                        **record["metadata"],
                        "source_asset_id": record["id"],
                        "external_signature": external_signature,
                        "review_status": "pending_full_review",
                    },
                )
            )
    else:
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
        if reference_asset_id is None and not external_records:
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
        if external_records:
            current_refs = list(
                await session.execute(
                    select(Asset.id, Asset.sha256)
                    .where(
                        Asset.order_id == order_id,
                        Asset.kind == "reference",
                    )
                    .order_by(Asset.created_at, Asset.id)
                )
            )
            if [(str(r.id), r.sha256) for r in current_refs] != [
                (str(r.id), r.sha256) for r in original_references
            ]:
                raise ImageWorkflowError("参考图在制作期间变化；外部图片未应用，请重新生成任务。")
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


async def queue_image_resume(
    session: AsyncSession, order_id: UUID, asset_id: UUID, *, new_candidate: bool = False
) -> UUID | None:
    """A successfully uploaded reference only wakes an already accepted image wait."""
    async with session.begin():
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if (
            order is None
            or order.phase
            not in (
                {"NEEDS_IMAGE", "AWAITING_RELEASE", "AWAITING_SELLER", "REVIEW"}
                if new_candidate
                else {"NEEDS_IMAGE"}
            )
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
            or version.status == "vetoed"
            or (version.status == "delivered" and not new_candidate)
            or reference is None
            or reference.order_id != order_id
            or reference.kind not in {"reference", "reference_edit"}
            or (
                new_candidate
                and (
                    reference.kind != "reference_edit"
                    or reference.is_demo
                    or not reference.image_metadata.get("owner_verified")
                )
            )
        ):
            return None
        key = f"system:images:{asset_id}"
        if new_candidate:
            # A newly reviewed companion image changes the bundle even when the
            # owner presses submit on the same main-image task again.
            bundle_ids = list(
                await session.scalars(
                    select(Asset.id)
                    .where(
                        Asset.order_id == order_id,
                        Asset.kind == "reference_edit",
                        Asset.image_metadata["input_digest"].as_string()
                        == reference.image_metadata.get("input_digest"),
                        Asset.image_metadata["owner_verified"].as_boolean().is_(True),
                    )
                    .order_by(Asset.id)
                )
            )
            key = "external-images:" + digest([str(asset_id), *map(str, bundle_ids)])
        prior = await session.scalar(
            select(Command)
            .where(
                Command.order_id == order_id,
                Command.idempotency_key.startswith(key),
            )
            .order_by(Command.created_at.desc())
            .limit(1)
        )
        if prior is not None:
            if not new_candidate or prior.status not in {"failed", "stale", "cancelled"}:
                return prior.id
            key += f":retry:{uuid4()}"
        if version.status == "delivered":
            # Preserve the delivered manifest and its approval. The worker will bind
            # actual external outputs to a fresh candidate and perform full review.
            version = DeliverableVersion(
                order_id=order_id,
                command_id=version.command_id,
                version_number=version.version_number + 1,
                round_index=0,
                parent_version_id=version.id,
                input_snapshot_id=version.input_snapshot_id,
                research_package_id=version.research_package_id,
                draft=version.draft,
                assets=[],
                status="text_review_passed",
                text_passed=True,
                full_passed=False,
                operation_key=f"external-base:{asset_id}",
            )
            session.add(version)
            await session.flush()
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
        order.phase = "NEEDS_IMAGE"
        session.add(
            OrderEvent(
                order_id=order_id,
                kind="image_resume_queued",
                message="补充参考图已排队，将继续制作和审校图片。",
                payload={"version_id": str(version.id), "command_id": str(command.id)},
            )
        )
        return command.id
