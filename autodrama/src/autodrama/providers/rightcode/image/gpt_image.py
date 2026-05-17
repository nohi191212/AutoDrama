from __future__ import annotations

from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef, ImageGenerationResult


class RightCodeImageProvider:
    """RightCode image provider via the OpenAI-compatible images API."""

    name = "rightcode"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://www.right.codes/draw").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model = settings.models.get("image", "gpt-image-2")
        self.api_key = settings.secret("api_key_env")

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        if base_url.endswith("/v1/images/generations"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/images/generations"
        return f"{base_url}/v1/images/generations"

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing RightCode API key environment variable")

        if refs:
            raise ProviderBadResponseError(
                "RightCode image generation uses /v1/images/generations and does not support reference images"
            )

        metadata = metadata or {}
        payload: dict[str, Any] = {
            "model": str(metadata.get("model", self.model)),
            "prompt": prompt,
        }

        resolved_size = size or metadata.get("size") or self.settings.options.get("size")
        if resolved_size is None:
            resolved_size = self.settings.options.get("image_size")
        if resolved_size is not None:
            payload["size"] = str(resolved_size)

        n = metadata.get("n", self.settings.options.get("n"))
        if n is not None:
            payload["n"] = int(n)

        for key in (
            "quality",
            "style",
            "background",
            "moderation",
            "output_format",
            "output_compression",
            "response_format",
            "user",
        ):
            if key in self.settings.options:
                payload[key] = self.settings.options[key]
            if key in metadata:
                payload[key] = metadata[key]

        extra_parameters = metadata.get("parameters")
        if isinstance(extra_parameters, dict):
            payload.update(extra_parameters)

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
                f"RightCode image generation failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"RightCode image generation returned non-JSON response: {exc}") from exc

        image_urls, image_data = self._extract_images(body)
        if not image_urls and not image_data:
            raise ProviderBadResponseError(f"RightCode image response has no image URL or base64 data: {body}")

        return ImageGenerationResult(
            provider=self.name,
            model=str(payload["model"]),
            image_urls=image_urls,
            image_data=image_data,
            request_id=self._request_id(body, response),
            usage=body.get("usage") or {},
            raw_response=body,
        )

    @staticmethod
    def _request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        request_id = body.get("request_id") or body.get("requestId") or body.get("id")
        if request_id:
            return str(request_id)
        return response.headers.get("x-request-id") or response.headers.get("x-requestid")

    @classmethod
    def _extract_images(cls, body: dict[str, Any]) -> tuple[list[str], list[str]]:
        image_urls: list[str] = []
        image_data: list[str] = []

        data = body.get("data")
        if isinstance(data, list):
            for item in data:
                cls._extract_image_item(item, image_urls, image_data)

        output = body.get("output")
        if isinstance(output, dict):
            for item in output.get("data") or []:
                cls._extract_image_item(item, image_urls, image_data)

        return image_urls, image_data

    @classmethod
    def _extract_image_item(cls, item: object, image_urls: list[str], image_data: list[str]) -> None:
        if not isinstance(item, dict):
            return

        cls._append_image_value(item.get("url"), image_urls, image_data)
        cls._append_image_value(item.get("b64_json"), image_urls, image_data)
        cls._append_image_value(item.get("image"), image_urls, image_data)
        cls._append_image_value(item.get("image_base64"), image_urls, image_data)

        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            cls._append_image_value(image_url.get("url"), image_urls, image_data)
            cls._append_image_value(image_url.get("b64_json"), image_urls, image_data)
        else:
            cls._append_image_value(image_url, image_urls, image_data)

    @staticmethod
    def _append_image_value(value: object, image_urls: list[str], image_data: list[str]) -> None:
        if not isinstance(value, str) or not value:
            return
        if value.startswith(("http://", "https://")):
            image_urls.append(value)
            return
        image_data.append(value)
