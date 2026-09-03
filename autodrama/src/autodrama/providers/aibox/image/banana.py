from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.providers.base import AssetRef
from autodrama.providers.aibox.image.gpt_image import AiboxImageProvider
from autodrama.providers.aibox.image.submission_budget import (
    ImageSubmissionLedger,
    ImageSubmissionTicket,
)


class AiboxBananaImageProvider(AiboxImageProvider):
    """AIBOX Gemini Banana image provider via the media task API."""

    name = "aibox"
    model_name = "gemini-3-pro-image-preview"
    MAX_REFERENCE_IMAGES = 14
    DEFAULT_SUBMISSION_LIMIT = 100
    SUBMISSION_LIMIT_ENV = "AUTODRAMA_BANANA_SUBMISSION_LIMIT"
    SUBMISSION_LEDGER_ENV = "AUTODRAMA_BANANA_SUBMISSION_LEDGER_PATH"

    def __init__(
        self,
        settings: ProviderSettings,
        runtime: RuntimeSettings,
        *,
        reference_uploader_settings: ProviderSettings | None = None,
        submission_ledger_path: Path | None = None,
        submission_limit: int | None = None,
    ) -> None:
        super().__init__(
            settings,
            runtime,
            reference_uploader_settings=reference_uploader_settings,
        )
        self.model = str(settings.models.get("banana") or self.model_name)
        self.aspect_ratio = str(settings.options.get("aspectRatio") or settings.options.get("aspect_ratio") or "16:9")
        self.image_size = str(settings.options.get("imageSize") or settings.options.get("image_size") or "4K")
        configured_limit = (
            submission_limit
            if submission_limit is not None
            else os.environ.get(self.SUBMISSION_LIMIT_ENV)
            or settings.options.get("banana_submission_limit")
            or self.DEFAULT_SUBMISSION_LIMIT
        )
        configured_ledger_path = (
            os.environ.get(self.SUBMISSION_LEDGER_ENV)
            or submission_ledger_path
            or settings.options.get("banana_submission_ledger_path")
            or Path.cwd() / ".tmp" / "aibox_banana_submission_budget.jsonl"
        )
        self.submission_ledger = ImageSubmissionLedger(
            Path(configured_ledger_path),
            limit=int(configured_limit),
        )

    # camelCase aliases so node-level params (aspectRatio / imageSize) bound via
    # BoundProviderProxy._apply_provider_model actually reach the snake_case
    # attributes that build_payload reads. Without these, a node such as
    # prop_image_generation specifying aspectRatio: "1:1" would silently fall
    # back to the provider default (16:9).
    @property
    def aspectRatio(self) -> str:
        return self.aspect_ratio

    @aspectRatio.setter
    def aspectRatio(self, value: Any) -> None:
        self.aspect_ratio = str(value)

    @property
    def imageSize(self) -> str:
        return self.image_size

    @imageSize.setter
    def imageSize(self, value: Any) -> None:
        self.image_size = str(value)

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

    def _before_generation_post(
        self,
        *,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> ImageSubmissionTicket:
        return self.submission_ledger.reserve(
            metadata={
                **metadata,
                "provider": self.name,
                "model": payload.get("model") or self.model,
            }
        )

    def _record_generation_submission_status(
        self,
        ticket: ImageSubmissionTicket | None,
        *,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.submission_ledger.record_status(
            ticket,
            status=status,
            details=details,
        )


__all__ = ["AiboxBananaImageProvider"]
