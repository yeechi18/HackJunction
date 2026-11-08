import base64
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_assets import asset_context as _asset_context
from test_assets import image_bytes

from company.persistence.agent_runs import AgentRun
from company.persistence.assets import Asset
from company.persistence.models import Order, OrderEvent, now
from company.providers.mimo import ProviderError
from company.providers.seedream import ImageCallUncertain, SeedreamResult, generate_image
from company.services.automatic_images import process_one_image_task
from company.services.model_budget import budget_state

asset_context = _asset_context


async def test_seedream_sends_inline_reference_and_never_exposes_key(settings):
    settings = settings.model_copy(
        update={"seedream_enabled": True, "seedream_api_key": SecretStr("private-test-key")}
    )
    data = image_bytes(size=(1024, 1024))

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "doubao-seedream-5-0-pro-260628"
        assert body["response_format"] == "b64_json" and body["watermark"]
        assert body["image"][0].startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": base64.b64encode(data).decode()}],
                "usage": {"generated_images": 1},
            },
        )

    output = await generate_image(
        settings, "product-specific prompt", [data], transport=httpx.MockTransport(respond)
    )
    assert output.data == data and output.usage["generated_images"] == 1
    with pytest.raises(ProviderError) as error:
        await generate_image(
            settings,
            "prompt",
            [],
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, text="private-test-key full remote body")
            ),
        )
    assert "401" in str(error.value) and "private-test-key" not in str(error.value)

    def timeout(request):
        raise httpx.ReadTimeout("private-test-key", request=request)

    with pytest.raises(ImageCallUncertain):
        await generate_image(settings, "prompt", [], transport=httpx.MockTransport(timeout))


class Planner:
    calls = 0

    async def generate_json(self, schema, messages, **kwargs):
        self.calls += 1
        assert any(p["type"] == "image_url" for p in messages[-1]["content"])
        return schema(
            composition="Keep the exact subject shape and open compartments.",
            background_and_light="White neutral background with soft directional light.",
            product_preservation="Preserve all original colors, count, logos and fixed dividers.",
        ), SimpleNamespace(
            usage={"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
            model="test-prompt-model",
            elapsed_seconds=0.1,
            request_id="test-plan-call",
        )


@pytest.mark.integration
async def test_upload_queues_automatic_image_and_persists_real_result_without_approval(
    asset_context,
):
    settings, app, client, new_order = asset_context
    settings.seedream_enabled = settings.seedream_auto_on_upload = True
    settings.seedream_api_key = SecretStr("test-image-key")
    oid = await new_order()
    seller, csrf = await client(oid)
    owner, owner_csrf = await client(owner=True)
    data = image_bytes(size=(1024, 1024))
    response = await seller.post(
        f"/api/v1/orders/{oid}/assets",
        headers={"x-csrf-token": csrf},
        files={"file": ("reference.png", data, "image/png")},
    )
    assert response.status_code == 201, response.text
    root = f"/api/v1/internal/orders/{oid}"
    tasks = (await owner.get(root + "/image-tasks")).json()
    assert tasks["automatic_generation"] and tasks["items"][0]["status"] == "queued"
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        order.pending_command_id = None  # Isolate image worker from unrelated intake fixture job.
        await session.commit()
    model = Planner()
    calls = []

    async def generator(configuration, prompt, references):
        calls.append(prompt)
        assert references == [data]
        assert "商品专属美术方案" in prompt
        if len(calls) == 1:
            raise ImageCallUncertain("测试：远端调用超时，需人工确认后重试。")
        return SeedreamResult(
            data, configuration.seedream_model, "test-image-request", {"generated_images": 1}
        )

    assert await process_one_image_task(
        app.state.database, settings, order_id=oid, planner=model, generator=generator
    )
    uncertain = (await owner.get(root + "/image-tasks")).json()["items"][0]
    assert uncertain["status"] == "unknown" and "asset_id" not in uncertain
    assert not await process_one_image_task(
        app.state.database, settings, order_id=oid, planner=model, generator=generator
    )
    assert (
        await owner.post(
            root + f"/image-tasks/{uncertain['id']}/generate", headers={"x-csrf-token": owner_csrf}
        )
    ).status_code == 200
    assert await process_one_image_task(
        app.state.database, settings, order_id=oid, planner=model, generator=generator
    )
    final = (await owner.get(root + "/image-tasks")).json()["items"][0]
    assert final["status"] == "returned" and model.calls == 1 and len(calls) == 2
    assert not await process_one_image_task(
        app.state.database, settings, order_id=oid, planner=model, generator=generator
    )
    assert (await seller.get(final["asset_url"])).status_code == 404
    async with app.state.database.sessions() as session:
        asset = await session.get(Asset, UUID(final["asset_id"]))
        assert asset.method == "ark_seedream" and asset.is_demo
        assert not asset.image_metadata["owner_verified"] and asset.version_id is None
        assert asset.image_metadata["request_id"] == "test-image-request"
        assert (await budget_state(session, settings, oid))["used_tokens"] == 50
        runs = list(await session.scalars(select(AgentRun).where(AgentRun.order_id == oid)))
        assert runs[0].role == "image_prompt" and runs[0].status == "succeeded"
    # Already returned images cannot trigger a second billed call on the same task.
    response = await owner.post(
        root + f"/image-tasks/{final['id']}/generate", headers={"x-csrf-token": owner_csrf}
    )
    assert response.status_code == 409


@pytest.mark.integration
async def test_stale_inputs_and_abandoned_calls_never_auto_bill_again(asset_context):
    settings, app, client, new_order = asset_context
    settings.seedream_enabled = settings.seedream_auto_on_upload = True
    settings.seedream_api_key = SecretStr("test-image-key")
    oid = await new_order()
    seller, csrf = await client(oid)
    owner, _ = await client(owner=True)
    for color_size in [(1024, 1024), (1200, 1200)]:
        assert (
            await seller.post(
                f"/api/v1/orders/{oid}/assets",
                headers={"x-csrf-token": csrf},
                files={"file": ("reference.png", image_bytes(size=color_size), "image/png")},
            )
        ).status_code == 201
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        order.pending_command_id = None
        await session.commit()

    async def forbidden(*args, **kwargs):
        raise AssertionError("A stale or uncertain task must not call the provider")

    assert await process_one_image_task(
        app.state.database, settings, order_id=oid, generator=forbidden
    )
    async with app.state.database.sessions() as session:
        task = await session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == oid,
                OrderEvent.kind == "image_task_created",
                OrderEvent.payload["status"].as_string() == "queued",
            )
        )
        task.payload = {
            **task.payload,
            "status": "generating",
            "lease_until": (now() - timedelta(minutes=1)).isoformat(),
        }
        await session.commit()
    assert not await process_one_image_task(
        app.state.database, settings, order_id=oid, generator=forbidden
    )
    tasks = (await owner.get(f"/api/v1/internal/orders/{oid}/image-tasks")).json()["items"]
    assert {t["status"] for t in tasks} == {"unknown", "failed"}
