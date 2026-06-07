from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef, ImageGenerationResult
from autodrama.providers.http import request_id_from_response
from autodrama.providers.media_refs import asset_uri, file_to_data_url


class VolcengineSeedreamImageProvider:
    """Volcengine Ark Seedream image provider."""

    name = "volcengine_seedream"
    supports_reference_images = True

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = str(
            settings.options.get("seedream_base_url")
            or settings.options.get("ark_base_url")
            or "https://ark.cn-beijing.volces.com"
        ).rstrip("/")
        self.model = (
            settings.models.get("seedream_5")
            or settings.models.get("seedream_5_lite")
            or settings.models.get("seedream")
            or settings.models.get("image")
            or "doubao-seedream-5-0-260128"
        )
        self.api_key = self._api_key(settings)
        self.size = str(
            settings.options.get("seedream_image_size")
            or settings.options.get("image_size")
            or settings.options.get("size")
            or "1600x2848"
        )
        self.output_format = str(
            settings.options.get("seedream_output_format") or settings.options.get("output_format") or "png"
        )
        self.response_format = str(
            settings.options.get("seedream_response_format") or settings.options.get("response_format") or "url"
        )
        self.watermark = bool(settings.options.get("seedream_watermark", settings.options.get("watermark", False)))
        self.sequential_image_generation = str(
            settings.options.get("seedream_sequential_image_generation")
            or settings.options.get("sequential_image_generation")
            or "disabled"
        )
        self.max_reference_images = int(settings.options.get("seedream_max_reference_images", 14))

    @staticmethod
    def _api_key(settings: ProviderSettings) -> str | None:
        api_key_ref = settings.options.get("seedream_api_key_env") or settings.options.get("ark_api_key_env")
        if api_key_ref:
            copied_settings = settings.model_copy(update={"api_key_env": str(api_key_ref)})
            return copied_settings.secret("api_key_env")
        return settings.secret("api_key_env")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/api/v3/images/generations"):
            return self.base_url
        if self.base_url.endswith("/api/v3"):
            return f"{self.base_url}/images/generations"
        return f"{self.base_url}/api/v3/images/generations"

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderAuthError(
                "Missing Volcengine Ark API key. Set providers.volcengine.options.seedream_api_key_env "
                "or providers.volcengine.api_key_env."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def build_payload(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        payload: dict[str, Any] = {
            "model": str(metadata.get("model") or self.model),
            "prompt": prompt[:5000],
            "size": str(metadata.get("size") or size or self._purpose_size(metadata) or self.size),
            "output_format": str(metadata.get("output_format") or self.output_format),
            "response_format": str(metadata.get("response_format") or self.response_format),
            "watermark": bool(metadata.get("watermark", self.watermark)),
            "sequential_image_generation": str(
                metadata.get("sequential_image_generation") or self.sequential_image_generation
            ),
        }

        images = self._reference_images(refs or [])
        if images:
            payload["image"] = images[0] if len(images) == 1 else images

        for key in (
            "stream",
            "seed",
            "guidance_scale",
            "optimize_prompt",
            "optimize_prompt_options",
            "sequential_image_generation_options",
            "tools",
            "safety_identifier",
        ):
            if key in self.settings.options:
                payload[key] = self.settings.options[key]
            if key in metadata:
                payload[key] = metadata[key]

        extra_parameters = metadata.get("parameters")
        if isinstance(extra_parameters, dict):
            payload.update(extra_parameters)
        return payload

    def _purpose_size(self, metadata: dict[str, Any]) -> object | None:
        node_name = str(metadata.get("node_name") or "")
        if node_name == "role_full_body_generation":
            return (
                self.settings.options.get("seedream_role_full_body_size")
                or self.settings.options.get("role_full_body_size")
            )
        if node_name == "role_multiview_generation":
            return (
                self.settings.options.get("seedream_role_multiview_size")
                or self.settings.options.get("seedream_role_design_size")
                or self.settings.options.get("role_multiview_size")
                or self.settings.options.get("role_design_size")
            )
        if node_name == "prop_generation":
            return self.settings.options.get("seedream_prop_size") or self.settings.options.get("prop_size")
        if node_name == "layout_image_generation":
            return self.settings.options.get("seedream_layout_size") or self.settings.options.get("layout_size")
        if node_name == "ref_frame_generation":
            return self.settings.options.get("seedream_ref_frame_size") or self.settings.options.get("ref_frame_size")
        return None

    def _reference_images(self, refs: list[AssetRef]) -> list[str]:
        images: list[str] = []
        for ref in refs:
            if ref.type != "image":
                continue
            value = self._ref_url_or_data(ref)
            if not value:
                continue
            images.append(value)
            if len(images) >= self.max_reference_images:
                break
        return images

    def _ref_url_or_data(self, ref: AssetRef) -> str | None:
        if ref.url:
            return ref.url
        if asset_uri := self._asset_uri(ref):
            return asset_uri
        if not ref.path:
            return None
        return self._data_url(Path(ref.path))

    @staticmethod
    def _asset_uri(ref: AssetRef) -> str | None:
        return asset_uri(ref)

    @staticmethod
    def _data_url(path: Path) -> str | None:
        return file_to_data_url(path, expected_type="image", default_mime="image/png")

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        payload = self.build_payload(prompt, refs, size=size, metadata=metadata)
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(self.endpoint, headers=self._headers(), json=payload)

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Seedream image generation failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Seedream image generation returned non-JSON response: {exc}") from exc

        image_urls, image_data = self._extract_images(body)
        if not image_urls and not image_data:
            raise ProviderBadResponseError(f"Seedream image response has no image URL or base64 data: {body}")

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
        return request_id_from_response(body, response, "x-request-id", "x-tt-logid")

    @classmethod
    def _extract_images(cls, body: dict[str, Any]) -> tuple[list[str], list[str]]:
        image_urls: list[str] = []
        image_data: list[str] = []
        cls._extract_image_item(body.get("data"), image_urls, image_data)
        cls._extract_image_item(body.get("output"), image_urls, image_data)
        cls._extract_image_item(body.get("result"), image_urls, image_data)
        return image_urls, image_data

    @classmethod
    def _extract_image_item(cls, item: object, image_urls: list[str], image_data: list[str]) -> None:
        if isinstance(item, list):
            for nested in item:
                cls._extract_image_item(nested, image_urls, image_data)
            return
        if isinstance(item, str):
            cls._append_image_value(item, image_urls, image_data)
            return
        if not isinstance(item, dict):
            return

        for key in ("url", "image_url", "b64_json", "image", "image_base64"):
            value = item.get(key)
            if isinstance(value, dict):
                cls._extract_image_item(value, image_urls, image_data)
            else:
                cls._append_image_value(value, image_urls, image_data)

        for key in ("data", "images", "choices", "output", "result"):
            if key in item:
                cls._extract_image_item(item[key], image_urls, image_data)

    @staticmethod
    def _append_image_value(value: object, image_urls: list[str], image_data: list[str]) -> None:
        if not isinstance(value, str) or not value:
            return
        if value.startswith(("http://", "https://")):
            image_urls.append(value)
            return
        image_data.append(value)
