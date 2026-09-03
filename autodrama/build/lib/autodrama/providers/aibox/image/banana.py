from __future__ import annotations

from typing import Any

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.providers.base import AssetRef
from autodrama.providers.aibox.image.gpt_image import AiboxImageProvider


class AiboxBananaImageProvider(AiboxImageProvider):
    """AIBOX Gemini Banana image provider via the media task API."""

    name = "aibox"
    model_name = "gemini-3-pro-image-preview"
    MAX_REFERENCE_IMAGES = 14

    def __init__(
        self,
        settings: ProviderSettings,
        runtime: RuntimeSettings,
        *,
        reference_uploader_settings: ProviderSettings | None = None,
    ) -> None:
        super().__init__(
            settings,
            runtime,
            reference_uploader_settings=reference_uploader_settings,
        )
        self.model = str(settings.models.get("banana") or self.model_name)
        self.aspect_ratio = str(settings.options.get("aspectRatio") or settings.options.get("aspect_ratio") or "16:9")
        self.image_size = str(settings.options.get("imageSize") or settings.options.get("image_size") or "4K")

    def build_payload(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        del size
        aspect_ratio = metadata.get("aspectRatio") or metadata.get("aspect_ratio") or self.aspect_ratio
        image_size = metadata.get("imageSize") or metadata.get("image_size") or self.image_size
        images = (
            reference_images
            if reference_images is not None
            else self._reference_images(refs or [], metadata=metadata)
        )
        params: dict[str, Any] = {
            "aspectRatio": str(aspect_ratio),
            "imageSize": str(image_size),
        }
        if images:
            params["images"] = images
        return {
            "model": str(metadata.get("model") or self.model or self.model_name),
            "params": params,
            "prompt": prompt,
        }


__all__ = ["AiboxBananaImageProvider"]
