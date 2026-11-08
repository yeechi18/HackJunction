"""Conservative layouts: preserve the reference subject and label verified specs only."""

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel

from company.domain.facts import canonical_key
from company.providers.storage import MAX_IMAGE_BYTES, NormalizedImage, validate_and_normalize


class MissingReferenceImage(ValueError):
    pass


class ImageLayoutError(ValueError):
    pass


@dataclass(frozen=True)
class LayoutImage:
    image: NormalizedImage
    kind: str
    text_layers: list[dict[str, Any]]
    reference_sha256: str


def _confirmed_facts(facts: list[dict], *, is_demo: bool) -> dict[str, dict]:
    collected: dict[str, dict] = {}
    for item in facts:
        fact = item.model_dump() if isinstance(item, BaseModel) else dict(item)
        if fact.get("confirmed") is False:
            continue
        # M4 ConfirmedFact supplies an id; raw form facts must explicitly be confirmed.
        if not fact.get("id") and fact.get("confirmed") is not True:
            continue
        origin = fact.get("source_kind", fact.get("source_type", fact.get("source", "")))
        if isinstance(origin, dict):
            origin = origin.get("kind", origin.get("type", "unknown"))
        allowed_sources = {"", "seller", "seller_input", "seller_confirmed", "seller_confirmation"}
        if is_demo:
            allowed_sources.add("fictional_seller")
        if not isinstance(origin, str) or origin not in allowed_sources:
            continue
        key = canonical_key(str(fact.get("key", "")))
        value = str(fact.get("value", "")).strip()
        if not key or not value:
            continue
        fact.update(key=key, value=value, id=str(fact.get("id") or f"fact-{key}"))
        if key in collected and (collected[key]["value"], collected[key].get("unit")) != (
            value,
            fact.get("unit"),
        ):
            raise ImageLayoutError("Conflicting product facts must be resolved before rendering.")
        collected[key] = fact
    return collected


def _dimension_label(fact: dict) -> str:
    value = " ".join(str(fact["value"]).split())
    unit = str(fact.get("unit") or "").strip()
    for source, replacement in {"毫米": " mm", "厘米": " cm", "英寸": " in", "×": "x"}.items():
        value, unit = value.replace(source, replacement), unit.replace(source, replacement)
    # Do not append a second unit to already qualified dimensions.
    if unit and re.search(rf"\b{re.escape(unit)}\b", value, re.I):
        unit = ""
    result = " ".join(f"{value} {unit}".split())
    if (
        not result.isascii()
        or not re.fullmatch(r"[0-9A-Za-z .xX*/,()+\-]+", result)
        or not re.search(r"\d", result)
        or not re.search(
            r"(?<![a-z])(?:mm|cm|m|in|inch|inches|ft|feet|millimeters|centimeters)(?![a-z])",
            result,
            flags=re.IGNORECASE,
        )
    ):
        raise ImageLayoutError("Dimensions need renderable numeric values and explicit units.")
    return result


def _wrap(text: str, draw: ImageDraw.ImageDraw, font, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        for character in word:
            if current and draw.textbbox((0, 0), current + character, font=font)[2] > width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return lines


def _draw_text(draw, text: str, box: tuple[int, int, int, int], *, size: int, fill: str):
    left, top, width, height = box
    for selected in range(size, 19, -2):
        font = ImageFont.load_default(size=selected)
        lines = _wrap(text, draw, font, width)
        line_height = selected + 12
        if len(lines) * line_height <= height:
            for index, line in enumerate(lines):
                draw.text((left, top + index * line_height), line, font=font, fill=fill)
            return {"box": list(box), "font_size": selected, "lines": lines}
    raise ImageLayoutError("Confirmed specifications are too long for the selected image layout.")


def _fit_reference(canvas: Image.Image, reference: Image.Image, box: tuple[int, int, int, int]):
    left, top, width, height = box
    thumbnail = ImageOps.contain(reference, (width, height), method=Image.Resampling.LANCZOS)
    position = (left + (width - thumbnail.width) // 2, top + (height - thumbnail.height) // 2)
    canvas.paste(thumbnail, position, thumbnail if thumbnail.mode == "RGBA" else None)


def _export(canvas: Image.Image) -> NormalizedImage:
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return validate_and_normalize(output.getvalue(), "image/png")


def render_layouts(
    reference_path: Path | None, facts: list[dict], *, is_demo: bool
) -> list[LayoutImage]:
    if reference_path is None or not reference_path.is_file():
        raise MissingReferenceImage(
            "A verified product reference image is required; request an upload."
        )
    if reference_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ImageLayoutError("Reference image exceeds the application size limit.")
    reference_bytes = reference_path.read_bytes()
    normalized = validate_and_normalize(reference_bytes)
    reference_hash = hashlib.sha256(reference_bytes).hexdigest()
    with Image.open(io.BytesIO(normalized.data)) as opened:
        reference = opened.convert("RGBA")
    verified = _confirmed_facts(facts, is_demo=is_demo)
    quantity = verified.get("pack_quantity", {}).get("value", "")
    if quantity and not re.fullmatch(r"[1-9][0-9]*", quantity):
        quantity = ""
    dimensions = ""
    dimension_ids = []
    if "dimensions" in verified:
        try:
            dimensions = _dimension_label(verified["dimensions"])
            dimension_ids = [verified["dimensions"]["id"]]
        except ImageLayoutError:
            pass
    dimension_heading = "Dimensions"
    volume = verified.get("net_volume")
    if not dimensions and volume:
        unit = volume.get("unit", "")
        value = volume.get("value", "")
        if (
            re.fullmatch(r"\d+(?:\.\d+)?", value)
            and float(value) > 0
            and unit in {"mL", "ml", "L", "l"}
        ):
            dimensions = f"{value} {unit}"
            dimension_ids = [volume["id"]]
            dimension_heading = "Net volume (per bottle)"
    scope = verified.get("dimension_scope")
    if scope and dimensions:
        value = scope["value"].casefold()
        if re.search(r"\beach\b|\bindividual\b|单件|每个|单个", value):
            dimension_heading = "Dimensions (per item)"
            dimension_ids.append(scope["id"])
        elif re.search(r"\bpackage\b|\bpackaging\b|整包|包装", value):
            dimension_heading = "Package dimensions"
            dimension_ids.append(scope["id"])

    main = Image.new("RGB", (1600, 1600), "white")
    _fit_reference(main, reference, (64, 64, 1472, 1472))
    main_layers = []
    detail = Image.new("RGB", (1600, 1600), "#f6f3ec")
    drawing = ImageDraw.Draw(detail)
    _draw_text(drawing, "PRODUCT DETAILS", (80, 72, 1440, 110), size=76, fill="#1c2429")
    _fit_reference(detail, reference, (80, 240, 1440, 740))
    detail_layers = []
    labels = []
    if quantity:
        labels.append(
            (
                f"Pack includes: {quantity} {'bottles' if volume else 'pieces'}",
                [verified["pack_quantity"]["id"]],
                (80, 1060, 1440, 160),
            )
        )
    if dimensions:
        labels.append((f"{dimension_heading}: {dimensions}", dimension_ids, (80, 1260, 1440, 230)))
    for text, fact_ids, box in labels:
        layout = _draw_text(drawing, text, box, size=54, fill="#1c2429")
        detail_layers.append({"text": text, "fact_ids": fact_ids, **layout})
    if is_demo:
        label = "FICTIONAL DEMO - NOT FOR PUBLICATION"
        for canvas, layers in [(main, main_layers), (detail, detail_layers)]:
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 1536, 1600, 1600), fill="#eecb92")
            layout = _draw_text(draw, label, (50, 1548, 1500, 48), size=30, fill="#442b13")
            layers.append({"text": label, "fact_ids": [], "kind": "demo_watermark", **layout})
    return [
        LayoutImage(_export(main), "main_candidate", main_layers, reference_hash),
        LayoutImage(_export(detail), "infographic", detail_layers, reference_hash),
    ]


def finish_external_image(data: bytes, content_type: str | None, task: dict):
    """Only typeset confirmed labels/watermarks; never describe this as image generation."""
    normalized = validate_and_normalize(data, content_type)
    with Image.open(io.BytesIO(normalized.data)) as source:
        canvas = source.convert("RGB")
    width, height = canvas.size
    # Work at a consistent canvas size for readable programmatic type.
    if width != height or width < 1024:
        from company.providers.storage import ImageValidationError

        raise ImageValidationError("回传图片需为至少 1024×1024 的正方形，请重新导出。")
    layers = []
    draw = ImageDraw.Draw(canvas)
    if task["kind"] == "infographic" and task.get("text_layers"):
        left = int(width * 0.70)
        draw.rectangle((left, 0, width, height), fill="#f6f3ec")
        for i, label in enumerate(task["text_layers"]):
            box = (
                left + 24,
                int(height * (0.22 + i * 0.28)),
                width - left - 48,
                int(height * 0.23),
            )
            layout = _draw_text(draw, label["text"], box, size=max(28, width // 40), fill="#1c2429")
            layers.append({**label, **layout})
    if task["concept_only"]:
        band = max(80, height // 12)
        draw.rectangle((0, height - band, width, height), fill="#f1d7ab")
        text = "CONCEPT ONLY - NOT A PRODUCT PHOTO"
        layout = _draw_text(
            draw,
            text,
            (32, height - band + 16, width - 64, band - 20),
            size=max(26, width // 42),
            fill="#442b13",
        )
        layers.append({"text": text, "kind": "concept_watermark", "fact_ids": [], **layout})
    return _export(canvas), layers
