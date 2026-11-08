import io
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import select
from test_assets import asset_context as _asset_context
from test_assets import image_bytes
from test_delivery_workflow import BundleProvider, accepted, candidates, upload
from test_delivery_workflow import bundle_context as _bundle_context

from company.persistence.assets import Asset
from company.persistence.models import InputSnapshot, Order
from company.services.image_tasks import plan_images
from company.services.workflow import process_one_job
from company.tools.image_layout import _dimension_label, finish_external_image

asset_context = _asset_context
bundle_context = _bundle_context


def test_product_specific_plans_only_use_confirmed_labels():
    facts = [
        {"key": "dimensions", "value": "24 x 10 x 9", "unit": "cm", "confirmed": True},
        {"key": "pack_quantity", "value": "9", "confirmed": False},
    ]
    desk = plan_images("桌面收纳盒", "", "", facts, [{"id": "a"}])
    assert [p["kind"] for p in desk["proposals"]] == ["main", "lifestyle", "infographic"]
    assert "书桌" in desk["proposals"][1]["prompt"]
    assert len(desk["proposals"][2]["text_layers"]) == 1
    kitchen = plan_images("厨房收纳架", "", "", [], [{"id": "a"}])
    assert "厨房台面" in kitchen["proposals"][1]["prompt"]
    plain = plan_images("Unspecified product", "", "", [], [])
    assert plain["concept_only"] and len(plain["proposals"]) == 1
    assert plan_images("收纳盒", "", "上传图片为 AI 生成的概念样图", [], [{}])["concept_only"]
    assert _dimension_label({"value": "24 cm x 10 cm", "unit": "cm"}) == "24 cm x 10 cm"


def test_program_watermark_is_mandatory_and_real_main_keeps_pixels():
    data = image_bytes(size=(1024, 1024))
    result, layers = finish_external_image(
        data, "image/png", {"kind": "main", "concept_only": True}
    )
    assert layers[0]["kind"] == "concept_watermark"
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.getpixel((20, 1000)) != image.getpixel((20, 20))
    result, layers = finish_external_image(
        data, "image/png", {"kind": "main", "concept_only": False}
    )
    assert result.data == data and not layers


@pytest.mark.integration
async def test_task_state_permissions_concept_visibility_and_stale_approval(asset_context):
    settings, app, client, new_order = asset_context
    oid = await new_order()
    owner, csrf = await client(owner=True)
    seller, seller_csrf = await client(oid)
    root = f"/api/v1/internal/orders/{oid}"
    headers = {"x-csrf-token": csrf}
    brief = (await owner.get(root + "/image-brief")).json()
    body = dict(
        expected_revision=brief["revision"], input_digest=brief["input_digest"], kind="main"
    )
    assert (
        await seller.post(root + "/image-tasks", json=body, headers={"x-csrf-token": seller_csrf})
    ).status_code == 401
    response = await owner.post(root + "/image-tasks", json=body, headers=headers)
    assert response.status_code == 200, response.text
    task = response.json()
    path = root + "/image-tasks/" + task["id"]
    assert task["status"] == "prepared" and task["concept_only"]
    file = {"file": ("output.png", image_bytes(size=(1024, 1024)), "image/png")}
    assert (await owner.post(path + "/result", files=file, headers=headers)).status_code == 409
    update = dict(action="started", note="Actual external call recorded", tool="Test image tool")
    assert (await owner.post(path + "/status", json=update, headers=headers)).status_code == 200
    # Failure remains recoverable and its exact report survives retries.
    update["action"] = "failed"
    update["note"] = "Tool failed: rendering timeout"
    assert (await owner.post(path + "/status", json=update, headers=headers)).status_code == 200
    update["action"] = "started"
    assert (await owner.post(path + "/status", json=update, headers=headers)).status_code == 200
    result = await owner.post(path + "/result", files=file, headers=headers)
    assert result.status_code == 200, result.text
    returned = result.json()
    assert returned["status"] == "returned"
    assert (await owner.post(path + "/result", files=file, headers=headers)).status_code == 409
    assert (await seller.get(returned["asset_url"])).status_code == 404
    update.update(action="approve", note="Checked concept only, no real product evidence")
    approved = await owner.post(path + "/status", json=update, headers=headers)
    assert approved.status_code == 200, approved.text
    async with app.state.database.sessions() as session:
        asset = await session.get(Asset, UUID(returned["asset_id"]))
        assert asset.is_demo and asset.kind == "concept"
        assert not asset.image_metadata["owner_verified"]
        assert asset.version_id is None
    # Changing a reference without changing revision still expires the task.
    response = await owner.post(root + "/image-tasks", json=body, headers=headers)
    second = root + "/image-tasks/" + response.json()["id"]
    await seller.post(
        f"/api/v1/orders/{oid}/assets", files=file, headers={"x-csrf-token": seller_csrf}
    )
    update["action"] = "started"
    assert (await owner.post(second + "/status", json=update, headers=headers)).status_code == 409
    assert all(t["stale"] for t in (await owner.get(root + "/image-tasks")).json()["items"])


@pytest.mark.integration
async def test_reference_result_is_not_verified_until_separate_review(asset_context):
    settings, app, client, new_order = asset_context
    oid = await new_order()
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        order.product_name = "Cable clip with real reference"
        await session.commit()
    owner, csrf = await client(owner=True)
    seller, seller_csrf = await client(oid)
    file = {"file": ("output.png", image_bytes(size=(1024, 1024)), "image/png")}
    await seller.post(
        f"/api/v1/orders/{oid}/assets", files=file, headers={"x-csrf-token": seller_csrf}
    )
    root = f"/api/v1/internal/orders/{oid}"
    headers = {"x-csrf-token": csrf}
    brief = (await owner.get(root + "/image-brief")).json()
    body = dict(
        expected_revision=brief["revision"], input_digest=brief["input_digest"], kind="main"
    )
    task = (await owner.post(root + "/image-tasks", json=body, headers=headers)).json()
    path = root + "/image-tasks/" + task["id"]
    update = dict(action="started", note="Actual tool call recorded", tool="Test tool")
    await owner.post(path + "/status", json=update, headers=headers)
    result = await owner.post(path + "/result", files=file, headers=headers)
    assert result.status_code == 200, result.text
    async with app.state.database.sessions() as session:
        asset = await session.get(Asset, UUID(result.json()["asset_id"]))
        assert not asset.image_metadata["owner_verified"]
        snapshot = await session.scalar(select(InputSnapshot).where(InputSnapshot.order_id == oid))
        snapshot.notes = (snapshot.notes or "") + "Changed seller request"
        await session.commit()
    update.update(action="approve", note="Checked against original real product")
    assert (await owner.post(path + "/status", json=update, headers=headers)).status_code == 409


@pytest.mark.integration
async def test_external_candidate_full_review_release_keeps_old_delivery(
    bundle_context, monkeypatch
):
    settings, database, client, create = bundle_context
    oid = await create()
    owner, headers = await client(owner=True)
    seller, seller_headers = await client(oid)
    provider = BundleProvider()
    # Establish a real workflow version and delivery using an explicit offline reviewer.
    await upload(seller, seller_headers, oid)
    await accepted(settings, database, oid, provider)
    old = (await candidates(database, oid))[-1]
    assert old.full_passed
    root = f"/api/v1/internal/orders/{oid}"

    async def release(version, key):
        response = await owner.post(
            root + "/decisions",
            headers={**headers, "idempotency-key": key},
            json={"action": "release", "expected_revision": 1, "version_id": str(version.id)},
        )
        assert response.status_code == 202, response.text
        assert await process_one_job(settings, provider=provider, order_id=oid)

    await release(old, "original-release")
    old_images = list(old.assets)
    brief = (await owner.get(root + "/image-brief")).json()
    task = (
        await owner.post(
            root + "/image-tasks",
            headers=headers,
            json={
                "expected_revision": 1,
                "input_digest": brief["input_digest"],
                "kind": "main",
            },
        )
    ).json()
    path = root + "/image-tasks/" + task["id"]
    update = dict(
        action="started", note="Actual test renderer execution recorded", tool="Offline fixture"
    )
    assert (await owner.post(path + "/status", headers=headers, json=update)).status_code == 200
    output = image_bytes(size=(1024, 1024))
    returned = await owner.post(
        path + "/result", headers=headers, files={"file": ("main.png", output, "image/png")}
    )
    assert returned.status_code == 200, returned.text
    update.update(action="approve", note="Owner checked product identity and quantity")
    assert (await owner.post(path + "/status", headers=headers, json=update)).status_code == 200
    queued = await owner.post(path + "/submit", headers=headers)
    assert queued.status_code == 200, queued.text
    from company.services import workflow
    from company.services.image_workflow import ImageWorkflowError

    original_renderer = workflow.render_version_images

    async def unavailable(*args, **kwargs):
        raise ImageWorkflowError("Temporary external image file unavailable; retry required.")

    monkeypatch.setattr(workflow, "render_version_images", unavailable)
    original_attempts = workflow.MAX_JOB_ATTEMPTS
    monkeypatch.setattr(workflow, "MAX_JOB_ATTEMPTS", 1)
    assert await process_one_job(settings, provider=provider, order_id=oid)
    monkeypatch.setattr(workflow, "render_version_images", original_renderer)
    monkeypatch.setattr(workflow, "MAX_JOB_ATTEMPTS", original_attempts)
    retried = await owner.post(path + "/submit", headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["command_id"] != queued.json()["command_id"]
    assert await process_one_job(settings, provider=provider, order_id=oid)
    versions = await candidates(database, oid)
    new = versions[-1]
    assert new.id != old.id and new.full_passed and len(new.assets) == 1
    assert versions[0].assets == old_images and versions[0].status == "delivered"
    image_url = f"/api/v1/orders/{oid}/assets/{new.assets[0]}"
    assert (await seller.get(image_url)).status_code == 404
    actual = await owner.get(image_url)
    assert actual.content == output  # No resizing, borders or second template rendering.
    async with database.sessions() as session:
        image = await session.get(Asset, UUID(new.assets[0]))
        assert image.method == "external_image_task"
        assert image.image_metadata["task_id"] == task["id"]
    await release(new, "new-release")
    assert (await seller.get(image_url)).status_code == 200
    deliveries = (await seller.get(f"/api/v1/orders/{oid}/deliverables")).json()["items"]
    assert len(deliveries) == 2
