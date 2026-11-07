"""Full M5 graph with actual local images/ZIP, scoped HTTP, and an injected model stub."""

import io
import json
from contextlib import AsyncExitStack
from types import SimpleNamespace
from uuid import UUID
from zipfile import ZipFile

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from PIL import Image, ImageDraw
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from company.api.deliverables import router as delivery_router
from company.cli.init_db import initialize_database
from company.main import create_app
from company.persistence.assets import Asset
from company.persistence.database import checkpoint_conninfo
from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import AuthSession, Command, Job, Message, Order, now
from company.persistence.production import DeliverableVersion
from company.schemas.commands import DecisionInput
from company.schemas.orders import CreateOrder
from company.services.access import create_session, csrf_token, token_hash
from company.services.orders import create_order, enqueue_decision
from company.services.workflow import process_one_job

pytestmark = pytest.mark.integration


class BundleProvider:
    def __init__(self, *, reject_first_image=False):
        self.calls, self.generation_inputs = [], []
        self.reject_first_image = reject_first_image
        self.image_reviews = 0

    async def generate_json(self, schema, messages, **kwargs):
        self.calls.append(schema.__name__)
        if schema.__name__ == "_IntakeDraft":
            output = {
                "ready_for_acceptance": True,
                "missing_fields": [],
                "conflicts": [],
                "summary": "资料齐全，等待负责人接单。",
                "questions": [],
            }
        elif schema.__name__ == "ResearchDraft":
            output = {
                "buyer_questions": [
                    {"question": "How many clips?", "applicable": True, "target_fields": ["title"]}
                ],
                "market_notes": [],
                "missing_facts": [],
                "follow_up_questions": [],
            }
        elif schema.__name__ == "ListingDraft":
            self.generation_inputs.append(json.dumps(messages, ensure_ascii=False))
            output = {
                "title": "6-Pack Silicone Cable Clips",
                "bullets": [
                    "Keep desktop cables nearby.",
                    "Organize the desktop.",
                    "For indoor use.",
                    "Check cable size first.",
                    "Six clips included.",
                ],
                "description": "Silicone clips for organizing indoor desk cables.",
                "faqs": [],
                "claims": [
                    {
                        "field_path": "title",
                        "text": "6-Pack Silicone Cable Clips",
                        "fact_ids": ["fact:pack_quantity", "fact:material"],
                    },
                    {
                        "field_path": "bullets[4]",
                        "text": "Six clips",
                        "fact_ids": ["fact:pack_quantity"],
                    },
                ],
                "image_plan": [
                    {
                        "purpose": "main",
                        "layout": "Use the supplied reference.",
                        "text_overlay": "",
                        "fact_ids": ["fact:material"],
                    }
                ],
            }
        elif schema.__name__ == "ReviewDraft":
            contains_images = any(
                isinstance(message.get("content"), list)
                and any(part.get("type") == "image_url" for part in message["content"])
                for message in messages
            )
            if contains_images:
                self.image_reviews += 1
            rejected = contains_images and self.reject_first_image and self.image_reviews == 1
            output = {"passed": not rejected, "buyer_question_coverage": [], "issues": []}
            if rejected:
                output["issues"] = [
                    {
                        "field_path": "images[0]",
                        "severity": "blocking",
                        "rule_id": "subject_quantity_mismatch",
                        "reason": "图片商品数量与确认资料不符。",
                        "fact_ids": ["fact:pack_quantity"],
                        "suggested_fix": "上传正确参考图。",
                    }
                ]
        else:
            raise AssertionError(schema.__name__)
        return schema.model_validate(output), SimpleNamespace(
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            model="offline-m5-bundle",
            elapsed_seconds=0.01,
        )


def reference_bytes(color="#506c8c"):
    """Fictional fixture: six simple shapes; every created order is explicitly DEMO."""
    image = Image.new("RGB", (600, 420), "white")
    draw = ImageDraw.Draw(image)
    for index in range(6):
        x, y = 70 + (index % 3) * 180, 65 + (index // 3) * 180
        draw.rounded_rectangle((x, y, x + 100, y + 100), radius=20, fill=color)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
async def bundle_context(postgres_test_settings, tmp_path):
    settings = postgres_test_settings.model_copy(
        update={
            "asset_dir": tmp_path / "assets",
            "session_secret": SecretStr("m5-graph-private-session-secret-over-thirty-two-bytes"),
        }
    )
    await initialize_database(settings)
    app = create_app(settings)
    if "/api/v1/orders/{order_id}/deliverables" not in app.openapi()["paths"]:
        app.include_router(delivery_router, prefix="/api/v1")
        app.openapi_schema = None
    order_ids, session_tokens = [], []
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))

        async def client(order_id=None, *, owner=False):
            result = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=settings.public_app_url,
                )
            )
            async with app.state.database.sessions() as session:
                token, _ = await create_session(
                    session, settings, "owner" if owner else "seller", order_id
                )
                await session.commit()
            session_tokens.append(token)
            result.cookies.set("owner_session" if owner else "seller_session", token)
            return result, {"x-csrf-token": csrf_token(settings, token)}

        async def new_order():
            async with app.state.database.sessions() as session:
                created = await create_order(
                    session,
                    settings,
                    CreateOrder(
                        seller_name="M5 graph fixture",
                        product_name="Fictional desk clips",
                        is_demo=True,
                        facts=[
                            {"key": "material", "value": "silicone", "confirmed": True},
                            {"key": "pack_quantity", "value": "6", "confirmed": True},
                            {
                                "key": "dimensions",
                                "value": "25 x 25 x 15",
                                "unit": "mm",
                                "confirmed": True,
                            },
                            {"key": "use_cases", "value": "indoor desk cables", "confirmed": True},
                        ],
                    ),
                )
            order_ids.append(created.id)
            return created.id

        yield settings, app.state.database, client, new_order
        async with AsyncPostgresSaver.from_conn_string(checkpoint_conninfo(settings)) as saver:
            for order_id in order_ids:
                await saver.adelete_thread(str(order_id))
        async with app.state.database.sessions() as session, session.begin():
            await session.execute(delete(Order).where(Order.id.in_(order_ids)))
            await session.execute(
                delete(AuthSession).where(
                    AuthSession.token_hash.in_([token_hash(token) for token in session_tokens])
                )
            )


async def candidates(database, order_id):
    async with database.sessions() as session:
        return list(
            await session.scalars(
                select(DeliverableVersion)
                .where(DeliverableVersion.order_id == order_id)
                .order_by(DeliverableVersion.version_number)
            )
        )


async def accepted(settings, database, order_id, provider):
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        await enqueue_decision(
            session, order_id, DecisionInput(action="accept", expected_revision=1), "m5-accept"
        )
    assert await process_one_job(settings, provider=provider, order_id=order_id)


async def upload(client, headers, order_id, color="#506c8c"):
    response = await client.post(
        f"/api/v1/orders/{order_id}/assets",
        headers=headers,
        files={"file": ("fixture.png", reference_bytes(color), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_missing_image_upload_full_review_release_and_private_download(bundle_context):
    settings, database, client, create = bundle_context
    order_id, other_id = await create(), await create()
    seller, headers = await client(order_id)
    other, _ = await client(other_id)
    owner, owner_headers = await client(owner=True)
    provider = BundleProvider()
    await accepted(settings, database, order_id, provider)
    initial = (await candidates(database, order_id))[0]
    async with database.sessions() as session:
        order = await session.get(Order, order_id)
        assert order.phase == "NEEDS_IMAGE" and order.pending_command_id is None
    assert initial.text_passed and not initial.full_passed and initial.assets == []
    assert (await seller.get(f"/api/v1/orders/{order_id}/deliverables")).json()["items"] == []
    source = await upload(seller, headers, order_id)
    async with database.sessions() as session:
        order = await session.get(Order, order_id)
        command = await session.get(Command, order.pending_command_id)
        assert command.kind == "IMAGES" and command.payload["reference_asset_id"] == source["id"]
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    version = (await candidates(database, order_id))[0]
    assert version.full_passed and version.text_passed and len(version.assets) == 2
    assert provider.calls.count("ResearchDraft") == provider.calls.count("ListingDraft") == 1
    private_image = f"/api/v1/orders/{order_id}/assets/{version.assets[0]}"
    assert (await seller.get(private_image)).status_code == 404
    assert (await owner.get(private_image)).status_code == 200
    url = f"/api/v1/internal/orders/{order_id}/decisions"
    queued = await owner.post(
        url,
        headers={**owner_headers, "idempotency-key": "m5-release"},
        json={"action": "release", "expected_revision": 1, "version_id": str(version.id)},
    )
    assert queued.status_code == 202, queued.text
    assert (await seller.get(f"/api/v1/orders/{order_id}/deliverables")).json()["items"] == []
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    result = await seller.get(f"/api/v1/orders/{order_id}/deliverables")
    assert result.status_code == 200, result.text
    delivered = result.json()["items"][0]
    download = await seller.get(delivered["download_url"])
    assert download.status_code == 200, download.text
    assert (await other.get(delivered["download_url"])).status_code == 404
    assert (await seller.get(private_image)).status_code == 200
    with ZipFile(io.BytesIO(download.content)) as package:
        assert set(package.namelist()) == {
            "listing.json",
            "listing.txt",
            "faq.md",
            "pricing-notes.md",
            "manifest.json",
            "images/image-01.png",
            "images/image-02.png",
        }
        assert b"claims" not in package.read("listing.json")
        assert b"usage" not in package.read("manifest.json")
    async with database.sessions() as session, session.begin():
        assert (await session.get(Order, order_id)).phase == "AWAITING_SELLER"
        before = await session.scalar(
            select(func.count()).select_from(Message).where(Message.order_id == order_id)
        )
        session.add(
            Job(order_id=order_id, command_id=UUID(queued.json()["command_id"]), kind="RESUME")
        )
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DeliverySnapshot)
                .where(DeliverySnapshot.order_id == order_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Message).where(Message.order_id == order_id)
            )
            == before
        )


async def test_veto_queues_new_child_version_with_reason_without_new_research(bundle_context):
    settings, database, client, create = bundle_context
    order_id = await create()
    seller, headers = await client(order_id)
    owner, owner_headers = await client(owner=True)
    await upload(seller, headers, order_id)
    provider = BundleProvider()
    await accepted(settings, database, order_id, provider)
    first = (await candidates(database, order_id))[0]
    reason = "请优化标题措辞并保持六件装事实。"
    url = f"/api/v1/internal/orders/{order_id}/decisions"
    rejected = await owner.post(
        url,
        headers={**owner_headers, "idempotency-key": "veto-empty"},
        json={"action": "veto", "expected_revision": 1, "version_id": str(first.id)},
    )
    assert rejected.status_code == 409
    queued = await owner.post(
        url,
        headers={**owner_headers, "idempotency-key": "m5-veto"},
        json={
            "action": "veto",
            "expected_revision": 1,
            "version_id": str(first.id),
            "reason": reason,
        },
    )
    assert queued.status_code == 202, queued.text
    assert len(await candidates(database, order_id)) == 1
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    first, second = await candidates(database, order_id)
    assert first.status == "vetoed" and second.parent_version_id == first.id
    assert second.full_passed and second.assets != first.assets
    assert provider.calls.count("ResearchDraft") == 1 and provider.calls.count("ListingDraft") == 2
    assert reason in provider.generation_inputs[-1]
    assert (await seller.get(f"/api/v1/orders/{order_id}/deliverables")).json()["items"] == []
    assert (
        await seller.get(f"/api/v1/orders/{order_id}/assets/{first.assets[0]}")
    ).status_code == 404


async def test_subject_mismatch_waits_for_new_reference_and_preserves_old_asset_set(bundle_context):
    settings, database, client, create = bundle_context
    order_id = await create()
    seller, headers = await client(order_id)
    await upload(seller, headers, order_id)
    provider = BundleProvider(reject_first_image=True)
    await accepted(settings, database, order_id, provider)
    first = (await candidates(database, order_id))[0]
    assert first.text_passed and not first.full_passed and len(first.assets) == 2
    async with database.sessions() as session:
        assert (await session.get(Order, order_id)).phase == "NEEDS_IMAGE"
        hashes = list(
            await session.scalars(select(Asset.sha256).where(Asset.version_id == first.id))
        )
    await upload(seller, headers, order_id, color="#744a5e")
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    old, replacement = await candidates(database, order_id)
    assert replacement.parent_version_id == old.id and replacement.full_passed
    assert old.assets == first.assets and old.assets != replacement.assets
    assert provider.calls.count("ResearchDraft") == provider.calls.count("ListingDraft") == 1
    async with database.sessions() as session:
        assert (
            list(await session.scalars(select(Asset.sha256).where(Asset.version_id == first.id)))
            == hashes
        )
        assert (await session.get(Order, order_id)).phase == "AWAITING_RELEASE"


@pytest.mark.parametrize("action", ["release", "veto"])
async def test_owner_effect_survives_checkpoint_failure_without_duplicate_artifacts(
    bundle_context, monkeypatch, action
):
    settings, database, client, create = bundle_context
    order_id = await create()
    seller, headers = await client(order_id)
    await upload(seller, headers, order_id)
    provider = BundleProvider()
    await accepted(settings, database, order_id, provider)
    first = (await candidates(database, order_id))[0]
    async with database.sessions() as session:
        queued = await enqueue_decision(
            session,
            order_id,
            DecisionInput(
                action=action,
                expected_revision=1,
                version_id=first.id,
                reason="请优化标题措辞。" if action == "veto" else None,
            ),
            f"checkpoint-{action}",
        )
    original = AsyncPostgresSaver.aput
    injected = False

    async def fail_after_effect(self, config, checkpoint, metadata, new_versions):
        nonlocal injected
        values = checkpoint.get("channel_values", {})
        expected_phase = "AWAITING_SELLER" if action == "release" else "GENERATION"
        if (
            not injected
            and values.get("phase") == expected_phase
            and values.get("command_id") == str(queued.command_id)
        ):
            injected = True
            raise RuntimeError("Injected checkpoint failure after durable owner decision")
        return await original(self, config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(AsyncPostgresSaver, "aput", fail_after_effect)
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert injected
    async with database.sessions() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        assert job.status == "queued"
        job.available_at = now()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        assert (await session.get(Command, queued.command_id)).status == "applied"
        if action == "release":
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DeliverySnapshot)
                    .where(DeliverySnapshot.order_id == order_id)
                )
                == 1
            )
            assert len(list((settings.asset_dir / "packages").glob("*.zip"))) == 1
        else:
            assert len(await candidates(database, order_id)) == 2
            assert provider.calls.count("ListingDraft") == 2
