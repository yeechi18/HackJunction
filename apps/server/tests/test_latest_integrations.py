from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_assets import asset_context as _asset_context
from test_assets import image_bytes
from test_delivery import delivery_context as _delivery_context

from company.api import copy_preview
from company.domain.brands import known_brand, missing_delivery_fields
from company.persistence.models import Order
from company.persistence.production import DeliverableVersion
from company.services.delivery import DeliveryError, release_version

asset_context = _asset_context
delivery_context = _delivery_context


def test_brand_is_limited_and_volume_is_sku_specific():
    profile = known_brand("百岁山瓶装水")
    assert profile and not known_brand("百岁山瓶子贴纸")
    assert {f["key"] for f in profile["facts"]} == {"brand", "product_type", "use_cases"}
    facts = profile["facts"] + [{"key": "pack_quantity", "value": "1", "confirmed": True}]
    assert missing_delivery_fields("百岁山水", facts) == ["net_volume"]
    facts += [{"key": "net_volume", "value": "570", "confirmed": True}]
    assert missing_delivery_fields("百岁山水", facts) == ["net_volume"]
    facts[-1]["unit"] = "mL"
    assert not missing_delivery_fields("百岁山水", facts)
    assert not missing_delivery_fields("desk organizer", [])


def test_imported_text_without_currency_does_not_crash_price_analysis():
    from company.agents.research import observed_price_range

    assert observed_price_range([{"id": "seller-source", "currency": None, "price": None}]) is None


def test_research_target_paths_are_normalized_but_unknown_fields_stay_invalid():
    from company.schemas.research import SellingPointPlan

    fields = SellingPointPlan.model_fields["target_fields"].annotation
    from pydantic import TypeAdapter

    assert SellingPointPlan.normalize_target_paths(
        ["bullets[0]", "bullets.1", "faqs[0].answer"]
    ) == ["bullets", "faqs"]
    with pytest.raises(ValidationError):
        TypeAdapter(fields).validate_python(["publish_now"])


def test_preview_language_and_claim_path_checks():
    data = dict(
        title="Ganten Water",
        bullets=["Natural mineral water"] * 5,
        description="Natural mineral water.",
        claims=[
            dict(
                field_path="bullets.0", text="Natural mineral water", fact_ids=["fact:product_type"]
            )
        ],
    )
    assert copy_preview.CopyDraft(**data).claims[0].field_path == "bullets[0]"
    with pytest.raises(ValidationError):
        copy_preview.CopyDraft(**{**data, "title": "中文标题"})


@pytest.mark.asyncio
async def test_complete_review_rejects_a_plan_with_a_missing_actual_image(tmp_path):
    from company.agents.review import review_listing

    path = tmp_path / "test.png"
    path.write_bytes(image_bytes())

    class Reviewer:
        async def generate_json(self, schema, messages, **kwargs):
            return schema(passed=True, issues=[], buyer_question_coverage=[]), SimpleNamespace(
                usage={}, model="test", elapsed_seconds=0.1
            )

    draft = dict(
        title="Product",
        bullets=["Product information"] * 5,
        description="Product information.",
        faqs=[],
        claims=[],
        image_plan=[dict(purpose="main", layout="Product", text_overlay="", fact_ids=[])] * 3,
    )
    result = await review_listing(
        {"draft": draft, "confirmed_facts": [], "image_paths": [str(path), str(path)]}, Reviewer()
    )
    assert not result.passed and any(i.rule_id == "incomplete_images" for i in result.issues)


@pytest.mark.asyncio
async def test_live_research_node_passes_selected_ark_provider_not_default_mimo(monkeypatch):
    from pydantic import SecretStr

    from company.config import Settings
    from company.providers import ark
    from company.services import workflow

    class ResearchProvider:
        async def close(self):
            pass

    monkeypatch.setattr(ark, "ArkProvider", lambda settings: ResearchProvider())
    settings = Settings(
        _env_file=None,
        app_env="development",
        research_provider="ark",
        ark_api_key=SecretStr("test-key"),
    )
    lease = workflow.JobLease(uuid4(), uuid4(), uuid4(), 1, 1)
    context = workflow.WorkflowContext(settings, SimpleNamespace(), lease)

    async def ready(state):
        return None

    async def step(order_id, command_id, provider, **kwargs):
        assert isinstance(provider.inner, ResearchProvider)
        return SimpleNamespace()

    async def apply(state, result, **kwargs):
        return kwargs

    monkeypatch.setattr(context, "_production_ready", ready)
    monkeypatch.setattr(context, "_apply_production_step", apply)
    monkeypatch.setattr(workflow, "research_step", step)
    result = await context.research({"command_id": str(lease.command_id)})
    assert result["next_phase"] == "GENERATION"
    await context.close()


class PreviewProvider:
    calls = 0

    async def generate_json(self, schema, messages, **kwargs):
        type(self).calls += 1
        return schema(
            title="Ganten Natural Mineral Water",
            bullets=["Natural mineral water"] * 5,
            description="Ganten natural mineral water for drinking.",
            faqs=[],
            claims=[],
        ), SimpleNamespace(
            usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            model="test-preview",
            elapsed_seconds=0.1,
        )

    async def close(self):
        pass


@pytest.mark.integration
async def test_preview_does_not_release_and_brand_patch_creates_new_revision(
    asset_context, monkeypatch
):
    settings, app, client, new_order = asset_context
    oid = await new_order()
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        order.product_name = "百岁山瓶装水"
        await session.commit()
    owner, csrf = await client(owner=True)
    seller, seller_csrf = await client(oid)
    other, _ = await client(await new_order())
    root = f"/api/v1/orders/{oid}"
    monkeypatch.setattr(copy_preview, "build_provider", lambda settings: PreviewProvider())
    PreviewProvider.calls = 0
    assert (await other.get(root + "/copy-preview")).status_code == 404
    assert (
        await owner.post(root + "/copy-preview", json={"expected_revision": 1})
    ).status_code == 403
    response = await owner.post(
        root + "/copy-preview", json={"expected_revision": 1}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    assert "net_volume" in response.json()["missing_fields"]
    assert (await seller.get(root + "/copy-preview")).json()["preview"]["id"] == response.json()[
        "id"
    ]
    await owner.post(
        root + "/copy-preview", json={"expected_revision": 1}, headers={"x-csrf-token": csrf}
    )
    assert PreviewProvider.calls == 1
    assert not (await seller.get(root + "/deliverables")).json()["items"]
    changed = await owner.post(
        root + "/brand-basics", json={"expected_revision": 1}, headers={"x-csrf-token": csrf}
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert (await seller.get(root + "/copy-preview")).json()["preview"] is None
    # Owning seller can confirm pack quantity while leaving volume explicitly unknown.
    confirmed = await seller.post(
        root + "/required-facts",
        json={
            "expected_revision": 2,
            "facts": [{"key": "pack_quantity", "value": "1", "unit": "bottles", "confirmed": True}],
        },
        headers={"x-csrf-token": seller_csrf},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["missing_delivery_fields"] == ["net_volume"]


@pytest.mark.integration
@pytest.mark.parametrize("problem", ["missing_volume", "missing_image"])
async def test_delivery_service_cannot_bypass_fact_or_image_gate(delivery_context, problem):
    settings, app, candidate, client = delivery_context
    item = await candidate()
    async with app.state.database.sessions() as session:
        order = await session.get(Order, item.order_id)
        version = await session.get(DeliverableVersion, item.version_id)
        if problem == "missing_volume":
            order.product_name = "百岁山瓶装水"
        else:
            version.draft = {
                **version.draft,
                "image_plan": version.draft["image_plan"] + [version.draft["image_plan"][0]],
            }
        await session.commit()
    with pytest.raises(DeliveryError):
        await release_version(
            app.state.database, settings, item.order_id, item.version_id, item.command_id
        )


@pytest.mark.integration
async def test_image_handoff_is_bound_to_facts_and_preserves_seller_reference_permissions(
    asset_context,
):
    settings, app, client, new_order = asset_context
    oid = await new_order()
    async with app.state.database.sessions() as session:
        order = await session.get(Order, oid)
        order.product_name = "Cable clip with real reference"
        await session.commit()
    seller, seller_csrf = await client(oid)
    owner, owner_csrf = await client(owner=True)
    public = f"/api/v1/orders/{oid}"
    internal = f"/api/v1/internal/orders/{oid}"
    file = {"file": ("product.png", image_bytes(), "image/png")}
    assert (
        await owner.post(public + "/assets", files=file, headers={"x-csrf-token": owner_csrf})
    ).status_code == 403
    uploaded = await seller.post(
        public + "/assets", files=file, headers={"x-csrf-token": seller_csrf}
    )
    assert uploaded.status_code == 201
    brief = (await owner.get(internal + "/image-brief")).json()
    params = dict(
        expected_revision=brief["revision"],
        input_digest=brief["input_digest"],
        purpose="reference_edit",
        approved="false",
    )
    assert (
        await owner.post(
            internal + "/generated-images",
            params=params,
            files=file,
            headers={"x-csrf-token": owner_csrf},
        )
    ).status_code == 422
    params["approved"] = "true"
    result = await owner.post(
        internal + "/generated-images",
        params=params,
        files=file,
        headers={"x-csrf-token": owner_csrf},
    )
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "reference_edit"
    references = (await owner.get(internal + "/image-brief")).json()["references"]
    assert len(references) == 1 and references[0]["id"] == uploaded.json()["id"]
    params["input_digest"] = "0" * 64
    assert (
        await owner.post(
            internal + "/generated-images",
            params=params,
            files=file,
            headers={"x-csrf-token": owner_csrf},
        )
    ).status_code == 409
    params.update(input_digest=brief["input_digest"], purpose="concept", approved="false")
    concept = await owner.post(
        internal + "/generated-images",
        params=params,
        files=file,
        headers={"x-csrf-token": owner_csrf},
    )
    assert concept.status_code == 200
    assert (await seller.get(public + "/assets/" + concept.json()["id"])).status_code == 404


@pytest.mark.integration
async def test_research_import_requires_owner_revision_and_does_not_claim_verified_facts(
    asset_context,
):
    settings, app, client, new_order = asset_context
    oid = await new_order()
    seller, seller_csrf = await client(oid)
    owner, csrf = await client(owner=True)
    path = f"/api/v1/internal/orders/{oid}/research-sources"
    body = dict(
        expected_revision=1,
        source="Seller customer question",
        content="How many bottles are included?",
    )
    assert (
        await seller.post(path, json=body, headers={"x-csrf-token": seller_csrf})
    ).status_code == 401
    result = await owner.post(path, json=body, headers={"x-csrf-token": csrf})
    assert result.status_code == 200 and result.json()["status"] == "partial"
    assert (
        await owner.post(
            path, json={**body, "expected_revision": 99}, headers={"x-csrf-token": csrf}
        )
    ).status_code == 409
