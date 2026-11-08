"""Deterministic image provider; no generative service or invented product appearance."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from company.config import Settings, get_settings
from company.providers.storage import LocalAssetStorage
from company.tools.image_layout import render_layouts


@dataclass(frozen=True)
class RenderedImage:
    path: Path
    storage_key: str
    kind: str
    method: str
    sha256: str
    width: int
    height: int
    text_layers: list[dict[str, Any]]
    reference_sha256: str
    metadata: dict = field(default_factory=dict)


class ImageProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.storage = LocalAssetStorage(self.settings.asset_dir)

    async def render_bundle(
        self,
        reference_path: Path | None,
        facts: list[dict],
        image_plan=None,
        *,
        is_demo: bool = False,
    ) -> list[RenderedImage]:
        """Use fixed templates and confirmed values. Unverified plan text is not rendered.

        The main image preserves the original reference on a white canvas; this is not
        background removal or a guarantee of Amazon image-policy compliance. The caller
        must review the actual subject and all generated images before delivery.
        """
        return await asyncio.to_thread(self._render, reference_path, facts, is_demo=is_demo)

    def _render(self, reference_path: Path | None, facts: list[dict], *, is_demo: bool):
        images = render_layouts(reference_path, facts, is_demo=is_demo)
        result = []
        for image in images:
            stored = self.storage.store(image.image)
            result.append(
                RenderedImage(
                    path=stored.path,
                    storage_key=stored.storage_key,
                    kind=image.kind,
                    method="reference_template_v2",
                    sha256=stored.sha256,
                    width=stored.width,
                    height=stored.height,
                    text_layers=image.text_layers,
                    reference_sha256=image.reference_sha256,
                )
            )
        return result
