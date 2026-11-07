"""Load bounded real attachments and a public reference page outside DB transactions."""

import asyncio
import hashlib
from pathlib import Path

from company.config import Settings
from company.providers.product_page import fetch_product_page
from company.providers.storage import LocalAssetStorage
from company.schemas.intake import PageObservation


def verified_image(storage, image):
    try:
        path = storage.resolve(image["storage_key"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != image["sha256"]:
            return None
        return path
    except (OSError, ValueError):
        return None


async def load_intake_sources(settings: Settings, context: dict, references: list[dict]):
    result = dict(context)
    images: list[Path] = []
    public = []
    storage = LocalAssetStorage(settings.asset_dir)
    for image in references[:3]:
        path = await asyncio.to_thread(verified_image, storage, image)
        public.append(
            {key: image[key] for key in ["id", "width", "height", "method"]}
            | {"attached": path is not None}
        )
        if path:
            images.append(path)
    result["reference_images"] = public
    url = context.get("domestic_product_url")
    if settings.app_env == "test":
        page = PageObservation(url=url, status="offline_test") if url else None
    else:
        try:
            page = await asyncio.wait_for(asyncio.to_thread(fetch_product_page, url), timeout=20)
        except TimeoutError:
            page = PageObservation(url=url, status="unavailable") if url else None
    result["page_observation"] = page.model_dump() if page else None
    return result, images
