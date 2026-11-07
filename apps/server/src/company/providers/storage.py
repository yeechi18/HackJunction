"""Local immutable image storage. User filenames never become filesystem paths."""

import hashlib
import io
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_SIDE = 10_000
FORMAT_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
OBJECT_KEY = re.compile(r"^[0-9a-f]{32}\.png$")


class ImageValidationError(ValueError):
    pass


class ImageLimitError(ImageValidationError):
    pass


class UnsupportedImageError(ImageValidationError):
    pass


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    width: int
    height: int
    media_type: str = "image/png"


@dataclass(frozen=True)
class StoredImage:
    storage_key: str
    path: Path
    sha256: str
    byte_size: int
    width: int
    height: int
    media_type: str = "image/png"


def validate_and_normalize(data: bytes, content_type: str | None = None) -> NormalizedImage:
    if not data:
        raise ImageValidationError("The uploaded image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageLimitError("Image exceeds the 10 MiB application limit.")
    declared = content_type.split(";", 1)[0].strip().lower() if content_type else None
    if declared and declared not in FORMAT_MEDIA_TYPES.values():
        raise UnsupportedImageError("Only PNG, JPEG and WebP uploads are supported.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_type = FORMAT_MEDIA_TYPES.get(image.format or "")
                if actual_type is None or (declared is not None and actual_type != declared):
                    raise UnsupportedImageError("File bytes do not match the allowed image type.")
                if (
                    image.width * image.height > MAX_IMAGE_PIXELS
                    or max(image.size) > MAX_IMAGE_SIDE
                ):
                    raise ImageLimitError("Image dimensions exceed the safe decoding limit.")
                if getattr(image, "n_frames", 1) != 1:
                    raise UnsupportedImageError("Animated image uploads are not supported.")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                clean = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
                clean.info.clear()
                output = io.BytesIO()
                clean.save(output, format="PNG", exif=b"")
                normalized = output.getvalue()
                if len(normalized) > MAX_IMAGE_BYTES:
                    raise ImageLimitError(
                        "Normalized image exceeds 10 MiB; upload a smaller image."
                    )
                return NormalizedImage(normalized, clean.width, clean.height)
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageLimitError("Image dimensions exceed the safe decoding limit.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ImageValidationError("The image cannot be decoded safely.") from None


class LocalAssetStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, storage_key: str) -> Path:
        if not OBJECT_KEY.fullmatch(storage_key):
            raise ValueError("Invalid asset storage key.")
        destination = (self.root / storage_key).resolve()
        if destination.parent != self.root:
            raise ValueError("Asset path escapes its configured storage root.")
        return destination

    def store(self, image: NormalizedImage) -> StoredImage:
        self.root.mkdir(parents=True, exist_ok=True)
        storage_key = f"{uuid4().hex}.png"
        destination = self.resolve(storage_key)
        with destination.open("xb") as output:
            output.write(image.data)
        return StoredImage(
            storage_key=storage_key,
            path=destination,
            sha256=hashlib.sha256(image.data).hexdigest(),
            byte_size=len(image.data),
            width=image.width,
            height=image.height,
        )

    def put_image(self, data: bytes, content_type: str | None = None) -> StoredImage:
        return self.store(validate_and_normalize(data, content_type))

    def read(self, storage_key: str) -> bytes:
        return self.resolve(storage_key).read_bytes()
