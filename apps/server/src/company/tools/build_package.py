"""Build an explicit public bundle; never archive a directory or internal diagnostics."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from company.providers.storage import MAX_IMAGE_BYTES, validate_and_normalize

PUBLIC_LISTING_FIELDS = {"title", "bullets", "description"}
PUBLIC_MANIFEST_FIELDS = {
    "order_id",
    "version_id",
    "created_at",
    "marketplace",
    "language",
    "is_demo",
    "content_hash",
}
MAX_PACKAGE_ASSETS = 16
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


def build_package(
    output_path: Path,
    *,
    listing: dict,
    faqs: list[dict],
    pricing_notes: str,
    manifest: dict,
    assets: list[Path],
    asset_root: Path | None = None,
) -> Path:
    """Export only public fields and exactly the explicitly supplied image files.

    Callers select the approved immutable version and its asset IDs before invoking this
    function. Optional ``asset_root`` enforces that image files belong to configured storage.
    """
    if output_path.suffix.lower() != ".zip" or output_path.exists():
        raise ValueError("Choose a new ZIP path for the immutable delivery package.")
    if len(assets) > MAX_PACKAGE_ASSETS:
        raise ValueError("Too many images for one delivery package.")
    public_listing = {key: value for key, value in listing.items() if key in PUBLIC_LISTING_FIELDS}
    public_faqs = [{"question": item["question"], "answer": item["answer"]} for item in faqs]
    public_manifest = {
        key: value for key, value in manifest.items() if key in PUBLIC_MANIFEST_FIELDS
    }
    text = str(public_listing.get("title", "")) + "\n\n"
    text += "\n".join(f"- {bullet}" for bullet in public_listing.get("bullets", []))
    text += "\n\n" + str(public_listing.get("description", "")) + "\n"
    faq_text = "\n\n".join(f"## {item['question']}\n\n{item['answer']}" for item in public_faqs)
    entries: dict[str, bytes] = {
        "listing.json": json.dumps(public_listing, ensure_ascii=False, indent=2).encode("utf-8"),
        "listing.txt": text.encode("utf-8"),
        "faq.md": faq_text.encode("utf-8"),
        "pricing-notes.md": pricing_notes.encode("utf-8"),
    }
    hashes = {}
    total = sum(len(value) for value in entries.values())
    allowed_root = asset_root.resolve() if asset_root is not None else None
    for index, path in enumerate(assets, 1):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Each package image must be a regular immutable asset file.")
        if allowed_root is not None and not path.resolve().is_relative_to(allowed_root):
            raise ValueError("Package image is outside configured asset storage.")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Package image exceeds the application size limit.")
        data = path.read_bytes()
        validate_and_normalize(data)  # Verify decode/pixels, without changing the confirmed bytes.
        with Image.open(path) as image:
            extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]
        name = f"images/image-{index:02d}.{extension}"
        entries[name] = data
        hashes[name] = hashlib.sha256(data).hexdigest()
        total += len(data)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("Delivery package exceeds the application size limit.")
    public_manifest.update(files=[*entries, "manifest.json"], asset_hashes=hashes)
    entries["manifest.json"] = json.dumps(
        public_manifest, ensure_ascii=False, indent=2, default=str
    ).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        with (
            temporary.open("xb") as target,
            ZipFile(target, "w", compression=ZIP_DEFLATED) as archive,
        ):
            for name, value in entries.items():
                archive.writestr(name, value)
        # Same-directory hard linking publishes atomically without overwriting an existing ZIP.
        os.link(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path
