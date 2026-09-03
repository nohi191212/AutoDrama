from __future__ import annotations

from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef, ImageGenerationResult


def _dashscope_api_key(settings: ProviderSettings) -> str | None:
    return settings.secret("api_key_env")


class WanxiangImageProvider:
    """Tongyi Wanxiang image provider via DashScope multimodal generation API."""

    name = "wanxiang"
    supports_reference_images = True

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model = settings.models.get("image", "wan2.7-image-pro")
        self.api_key = _dashscope_api_key(settings)

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        if base_url.endswith("/services/aigc/multimodal-generation/generation"):
            return base_url
        if base_url.endswith("/api/v1"):
            return f"{base_url}/services/aigc/multimodal-generation/generation"
        return f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Wanxiang/DashScope API key environment variable")

        metadata = metadata or {}
        content: list[dict[str, str]] = []
        for ref in refs or []:
            if ref.url:
                content.append({"image": ref.url})
            elif ref.path:
                content.append({"image": ref.path})
        content.append({"text": prompt[:5000]})

        parameters: dict[str, Any] = {
            "size": (
                metadata.get("size")
                or size
                or self.settings.options.get("image_size", self.settings.options.get("size", "2K"))
            ),
            "n": int(metadata.get("n", self.settings.options.get("n", 1))),
            "watermark": bool(self.settings.options.get("watermark", False)),
        }
        for key in ("thinking_mode", "enable_sequential", "seed", "color_palette"):
            if key in self.settings.options:
                parameters[key] = self.settings.options[key]
        parameters.update(metadata.get("parameters", {}))

        payload = {
            "model": metadata.get("model", self.model),
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ]
            },
            "parameters": parameters,
        }

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Wanxiang image generation failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Wanxiang image generation returned non-JSON response: {exc}") from exc

        image_urls = self._extract_image_urls(body)
        if not image_urls:
            raise ProviderBadResponseError(f"Wanxiang image response has no image URL: {body}")

        output = body.get("output") or {}
        return ImageGenerationResult(
            provider=self.name,
            model=str(payload["model"]),
            image_urls=image_urls,
            task_id=output.get("task_id"),
            task_status=output.get("task_status"),
            request_id=body.get("request_id") or body.get("requestId"),
            usage=body.get("usage") or {},
            raw_response=body,
        )

    @staticmethod
    def _extract_image_urls(body: dict[str, Any]) -> list[str]:
        image_urls: list[str] = []
        output = body.get("output") or {}
        for choice in output.get("choices") or []:
            message = choice.get("message") or {}
            for item in message.get("content") or []:
                image_url = item.get("image") or item.get("url")
                if image_url:
                    image_urls.append(str(image_url))
        if image_url := output.get("image_url"):
            image_urls.append(str(image_url))
        return image_urls
