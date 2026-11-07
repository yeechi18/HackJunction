"""Actual image bytes, bounded uploads, scoped visibility, and explicit ZIP exports."""

import hashlib
import io
import json
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import delete

from company.api.assets import router as asset_router
from company.cli.init_db import initialize_database
from company.config import Settings
from company.main import create_app
from company.persistence.assets import Asset
from company.persistence.models import AuthSession, Order, now
from company.providers.images import ImageProvider
from company.providers.storage import (
    MAX_IMAGE_BYTES,
    ImageLimitError,
    ImageValidationError,
    LocalAssetStorage,
    UnsupportedImageError,
    validate_and_normalize,
)
from company.schemas.orders import CreateOrder
from company.services.access import create_session, csrf_token, token_hash
from company.services.orders import create_order
from company.tools.build_package import build_package
from company.tools.image_layout import ImageLayoutError, MissingReferenceImage


def image_bytes(*, image_format: str = "PNG", size=(120, 80), exif=None):
    output = io.BytesIO()
    image = Image.new("RGB", size, "#294859")
    options = {"exif": exif} if exif is not None else {}
    image.save(output, format=image_format, **options)
    return output.getvalue()


def confirmed_facts():
    return [
        {"id": "fact-count", "key": "pack_quantity", "value": "6"},
        {"id": "fact-size", "key": "dimensions", "value": "25 x 25 x 15", "unit": "mm"},
    ]


def test_decode_normalization_checks_real_format_and_removes_exif():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private camera metadata"
    data = image_bytes(image_format="JPEG", exif=exif)
    normalized = validate_and_normalize(data, "image/jpeg")
    assert (normalized.width, normalized.height) == (80, 120)
    with Image.open(io.BytesIO(normalized.data)) as result:
        assert result.format == "PNG"
        assert not result.getexif()
        assert not result.info
    with pytest.raises(UnsupportedImageError):
        validate_and_normalize(data, "image/png")
    with pytest.raises(ImageValidationError):
        validate_and_normalize(b"not an image", "image/png")


def test_rejects_byte_and_pixel_limits_before_rendering():
    with pytest.raises(ImageLimitError):
        validate_and_normalize(b"x" * (MAX_IMAGE_BYTES + 1), "image/png")
    with pytest.raises(ImageLimitError):
        validate_and_normalize(image_bytes(size=(10_001, 1)), "image/png")


def test_storage_ignores_filenames_and_prevents_path_escape(tmp_path: Path):
    storage = LocalAssetStorage(tmp_path / "assets")
    stored = storage.put_image(image_bytes(), "image/png")
    assert stored.path.parent == storage.root
    assert len(stored.path.stem) == 32
    assert storage.read(stored.storage_key) == stored.path.read_bytes()
    assert stored.sha256 == hashlib.sha256(stored.path.read_bytes()).hexdigest()
    for key in ("../.env", "/etc/passwd", "..\\secret.png", "filename.png"):
        with pytest.raises(ValueError):
            storage.resolve(key)


@pytest.mark.parametrize("is_demo", [False, True])
async def test_rendered_images_are_readable_and_use_confirmed_text_only(
    tmp_path: Path, is_demo: bool
):
    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes())
    settings = Settings(_env_file=None, app_env="test", asset_dir=tmp_path / "assets")
    rendered = await ImageProvider(settings).render_bundle(
        reference,
        confirmed_facts(),
        image_plan=[{"text_overlay": "UNSUPPORTED WATERPROOF CLAIM"}],
        is_demo=is_demo,
    )
    assert [image.kind for image in rendered] == ["main_candidate", "infographic"]
    for item in rendered:
        with Image.open(item.path) as picture:
            picture.load()
            assert picture.format == "PNG" and picture.size == (1600, 1600)
        assert item.sha256 == hashlib.sha256(item.path.read_bytes()).hexdigest()
        assert item.reference_sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()
        assert "WATERPROOF" not in json.dumps(item.text_layers)
        for layer in item.text_layers:
            left, top, width, height = layer["box"]
            assert left >= 0 and top >= 0 and left + width <= 1600 and top + height <= 1600
    main_layers = rendered[0].text_layers
    assert bool(main_layers) is is_demo
    assert all(layer["kind"] == "demo_watermark" for layer in main_layers)
    details = rendered[1].text_layers
    assert any(
        layer["fact_ids"] == ["fact-count"] and "6 pieces" in layer["text"] for layer in details
    )
    assert any(
        layer["fact_ids"] == ["fact-size"] and "25 x 25 x 15 mm" in layer["text"]
        for layer in details
    )


@pytest.mark.parametrize(
    ("scope", "label"),
    [("each individual cable clip", "Dimensions (per item)"), ("整包", "Package dimensions")],
)
async def test_dimension_image_preserves_seller_confirmed_scope(
    tmp_path: Path, scope: str, label: str
):
    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes())
    provider = ImageProvider(
        Settings(_env_file=None, app_env="test", asset_dir=tmp_path / "assets")
    )
    rendered = await provider.render_bundle(
        reference,
        [*confirmed_facts(), {"id": "fact-scope", "key": "dimension_scope", "value": scope}],
    )
    layer = next(layer for layer in rendered[1].text_layers if "fact-size" in layer["fact_ids"])
    assert layer["text"] == f"{label}: 25 x 25 x 15 mm"
    assert layer["fact_ids"] == ["fact-size", "fact-scope"]


async def test_no_reference_or_unconfirmed_specs_cannot_create_product_images(tmp_path: Path):
    provider = ImageProvider(
        Settings(_env_file=None, app_env="test", asset_dir=tmp_path / "assets")
    )
    with pytest.raises(MissingReferenceImage):
        await provider.render_bundle(None, confirmed_facts(), is_demo=False)
    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes())
    facts = confirmed_facts()
    facts[0]["source_kind"] = "competitor"
    with pytest.raises(ImageLayoutError):
        await provider.render_bundle(reference, facts)
    assert not (tmp_path / "assets").exists()


def test_zip_contains_only_explicit_public_fields_and_images(tmp_path: Path):
    storage = LocalAssetStorage(tmp_path / "assets")
    reference = storage.put_image(image_bytes())
    (storage.root / "diagnostics.txt").write_text("private diagnostics must never be included")
    output = build_package(
        tmp_path / "version-1.zip",
        listing={
            "title": "Cable Clips",
            "bullets": ["Six clips"],
            "description": "Details",
            "diagnostics": "private listing trace",
            "claims": [{"fact_ids": ["internal"]}],
        },
        faqs=[{"question": "How many?", "answer": "Six.", "internal_note": "private"}],
        pricing_notes="Fictional demo pricing only.",
        manifest={"version_id": "demo-v1", "is_demo": True, "diagnostics": "private manifest"},
        assets=[reference.path],
        asset_root=storage.root,
    )
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "listing.json",
            "listing.txt",
            "faq.md",
            "pricing-notes.md",
            "manifest.json",
            "images/image-01.png",
        }
        all_text = " ".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if not name.startswith("images/")
        )
        assert "private" not in all_text and "diagnostics" not in all_text
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["asset_hashes"]["images/image-01.png"] == reference.sha256
    with pytest.raises(ValueError):
        build_package(output, listing={}, faqs=[], pricing_notes="", manifest={}, assets=[])


@pytest.fixture
async def asset_context(postgres_test_settings: Settings, tmp_path: Path):
    settings = postgres_test_settings.model_copy(
        update={
            "asset_dir": tmp_path / "assets",
            "session_secret": SecretStr("asset-tests-have-a-private-session-secret-32-bytes"),
        }
    )
    await initialize_database(settings)
    app = create_app(settings)
    if "/api/v1/orders/{order_id}/assets" not in app.openapi()["paths"]:
        app.include_router(asset_router, prefix="/api/v1")
        app.openapi_schema = None
    order_ids, session_tokens = [], []
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))

        async def scoped_client(order_id=None, *, owner=False):
            client = await stack.enter_async_context(
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
            client.cookies.set("owner_session" if owner else "seller_session", token)
            return client, csrf_token(settings, token)

        async def new_order():
            async with app.state.database.sessions() as session:
                result = await create_order(
                    session,
                    settings,
                    CreateOrder(
                        seller_name="Asset test seller",
                        product_name="Fictional cable clip",
                        is_demo=True,
                    ),
                )
            order_ids.append(result.id)
            return result.id

        yield settings, app, scoped_client, new_order
        async with app.state.database.sessions() as session:
            if order_ids:
                await session.execute(delete(Order).where(Order.id.in_(order_ids)))
            if session_tokens:
                await session.execute(
                    delete(AuthSession).where(
                        AuthSession.token_hash.in_([token_hash(token) for token in session_tokens])
                    )
                )
            await session.commit()


@pytest.mark.integration
async def test_asset_upload_scope_csrf_and_generated_visibility(asset_context):
    settings, app, scoped_client, new_order = asset_context
    order_id, other_id = await new_order(), await new_order()
    seller, csrf = await scoped_client(order_id)
    other, _ = await scoped_client(other_id)
    owner, owner_csrf = await scoped_client(owner=True)
    url = f"/api/v1/orders/{order_id}/assets"
    upload = {"file": ("../../.env", image_bytes(), "image/png")}
    assert (await seller.post(url, files=upload)).status_code == 403
    response = await seller.post(url, files=upload, headers={"x-csrf-token": csrf})
    assert response.status_code == 201, response.text
    original = response.json()
    assert original["kind"] == "reference" and original["is_demo"] is True
    assert "storage_key" not in original and "path" not in original
    assert (await seller.get(original["url"])).status_code == 200
    assert (await other.get(original["url"])).status_code == 404
    stored = LocalAssetStorage(settings.asset_dir).put_image(image_bytes())
    generated_id = uuid4()
    async with app.state.database.sessions() as session:
        session.add(
            Asset(
                id=generated_id,
                order_id=order_id,
                version_id=uuid4(),
                kind="infographic",
                method="reference_template_v1",
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                media_type=stored.media_type,
                byte_size=stored.byte_size,
                width=stored.width,
                height=stored.height,
                is_demo=True,
                delivered_at=None,
                image_metadata={},
            )
        )
        await session.commit()
    generated_url = f"{url}/{generated_id}"
    assert (await seller.get(generated_url)).status_code == 404
    assert (await owner.get(generated_url)).status_code == 200
    assert len((await seller.get(url)).json()["items"]) == 1
    assert len((await owner.get(url)).json()["items"]) == 2
    async with app.state.database.sessions() as session:
        asset = await session.get(Asset, generated_id)
        asset.delivered_at = now()
        await session.commit()
    assert (await seller.get(generated_url)).status_code == 200
    assert (await other.get(generated_url)).status_code == 404
    # A valid scoped seller may still upload when a separate owner cookie is stale.
    seller.cookies.set("owner_session", "expired-owner-token")
    assert (await seller.post(url, files=upload, headers={"x-csrf-token": csrf})).status_code == 201
    assert (
        await owner.post(url, files=upload, headers={"x-csrf-token": owner_csrf})
    ).status_code == 201


@pytest.mark.integration
async def test_asset_upload_rejects_corrupt_and_oversized_input(asset_context):
    _, _, scoped_client, new_order = asset_context
    order_id = await new_order()
    seller, csrf = await scoped_client(order_id)
    url = f"/api/v1/orders/{order_id}/assets"
    for data, expected in [(b"bad-png", 422), (b"x" * (MAX_IMAGE_BYTES + 1), 413)]:
        response = await seller.post(
            url, files={"file": ("file.png", data, "image/png")}, headers={"x-csrf-token": csrf}
        )
        assert response.status_code == expected, response.text
    assert (await seller.get(url)).json()["items"] == []
