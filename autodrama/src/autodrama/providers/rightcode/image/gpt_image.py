from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef, ImageGenerationResult


class RightCodeImageProvider:
    """RightCode image provider via the OpenAI-compatible Images API."""

    name = "rightcode"
    supports_reference_images = True

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://www.right.codes/draw").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model = settings.models.get("image", "gpt-image-2")
        self.api_key = settings.secret("api_key_env")
        self.max_reference_images = int(
            settings.options.get("rightcode_max_reference_images")
            or settings.options.get("max_reference_images")
            or 16
        )

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        for suffix in (
            "/v1/images/generations",
            "/images/generations",
            "/v1/chat/completions",
            "/chat/completions",
        ):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)].rstrip("/")
                break
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

        metadata = metadata or {}
        payload = self.build_payload(prompt, refs=refs, size=size, metadata=metadata)

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
            "model": str(metadata.get("model") or self._purpose_model(metadata) or self.model),
            "prompt": prompt,
        }

        resolved_size = size or metadata.get("size") or self._purpose_size(metadata) or self.settings.options.get("size")
        if resolved_size is None:
            resolved_size = self.settings.options.get("image_size")
        if resolved_size is not None:
            payload["size"] = str(resolved_size)

        images = self._reference_images(refs or [], metadata=metadata)
        if images:
            payload["image"] = images

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
            purpose_value = self._purpose_parameter(key, metadata)
            if purpose_value is not None:
                payload[key] = purpose_value
            if key in metadata:
                payload[key] = metadata[key]

        extra_parameters = metadata.get("parameters")
        if isinstance(extra_parameters, dict):
            payload.update(extra_parameters)
        return payload

    def _purpose_model(self, metadata: dict[str, Any]) -> str | None:
        if self._is_role_design_image(metadata):
            return self.settings.models.get("rightcode_role_design") or self.settings.models.get("role_design")
        if str(metadata.get("node_name") or "") == "ref_frame_generation":
            return self.settings.models.get("rightcode_ref_frame") or self.settings.models.get("ref_frame")
        return None

    def _purpose_size(self, metadata: dict[str, Any]) -> object | None:
        if self._is_role_design_image(metadata):
            return self.settings.options.get("rightcode_role_design_size") or self.settings.options.get("role_design_size")
        if str(metadata.get("node_name") or "") == "ref_frame_generation":
            return self.settings.options.get("rightcode_ref_frame_size") or self.settings.options.get("ref_frame_size")
        return None

    def _purpose_parameter(self, key: str, metadata: dict[str, Any]) -> object | None:
        if key == "quality" and self._is_role_design_image(metadata):
            return self.settings.options.get("rightcode_role_design_quality") or self.settings.options.get(
                "role_design_quality"
            )
        return None

    @staticmethod
    def _is_role_design_image(metadata: dict[str, Any]) -> bool:
        return str(metadata.get("node_name") or "") == "role_appearance_generation"

    def _build_chat_payload(
        self,
        prompt: str,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.build_payload(prompt, size=size, metadata=metadata)

    def _reference_images(self, refs: list[AssetRef], *, metadata: dict[str, Any]) -> list[str]:
        images: list[str] = []
        max_reference_images = int(metadata.get("max_reference_images") or self.max_reference_images)
        for ref in refs:
            if ref.type != "image":
                continue
            value = self._ref_url_or_data(ref)
            if not value:
                continue
            images.append(value)
            if len(images) >= max_reference_images:
                break
        return images

    def _ref_url_or_data(self, ref: AssetRef) -> str | None:
        for value in (ref.url, ref.path):
            if isinstance(value, str) and value.startswith("data:image/"):
                return value
        if ref.url:
            return ref.url
        if not ref.path:
            return None
        path_value = str(ref.path)
        if path_value.startswith(("http://", "https://")):
            return path_value
        return self._data_url(Path(path_value))

    @staticmethod
    def _data_url(path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        if not mime_type.startswith("image/"):
            return None
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

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

        choices = body.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                cls._extract_image_item(choice, image_urls, image_data)

        output = body.get("output")
        if isinstance(output, dict):
            for item in output.get("data") or []:
                cls._extract_image_item(item, image_urls, image_data)
            cls._extract_image_item(output, image_urls, image_data)

        return image_urls, image_data

    @classmethod
    def _extract_image_item(cls, item: object, image_urls: list[str], image_data: list[str]) -> None:
        if isinstance(item, list):
            for nested in item:
                cls._extract_image_item(nested, image_urls, image_data)
            return
        if isinstance(item, str):
            cls._extract_image_text(item, image_urls, image_data)
            return
        if not isinstance(item, dict):
            return

        cls._append_image_value(item.get("url"), image_urls, image_data)
        cls._append_image_value(item.get("b64_json"), image_urls, image_data)
        cls._append_image_value(item.get("image"), image_urls, image_data)
        cls._append_image_value(item.get("image_base64"), image_urls, image_data)
        cls._append_image_value(item.get("text"), image_urls, image_data)

        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            cls._append_image_value(image_url.get("url"), image_urls, image_data)
            cls._append_image_value(image_url.get("b64_json"), image_urls, image_data)
        else:
            cls._append_image_value(image_url, image_urls, image_data)

        for key in ("message", "content", "images", "data", "choices", "output"):
            if key in item:
                cls._extract_image_item(item[key], image_urls, image_data)

    @classmethod
    def _extract_image_text(cls, value: str, image_urls: list[str], image_data: list[str]) -> None:
        text = value.strip()
        if not text:
            return
        if text.startswith(("data:image/", "http://", "https://")):
            cls._append_image_value(text, image_urls, image_data)
            return

        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            cls._extract_image_item(parsed, image_urls, image_data)
            return

        for image_value in cls._iter_image_like_strings(text):
            cls._append_image_value(image_value, image_urls, image_data)

    @staticmethod
    def _iter_image_like_strings(text: str) -> list[str]:
        patterns = [
            r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+",
            r"https?://[^\s)\"']+",
        ]
        matches: list[str] = []
        for pattern in patterns:
            matches.extend(match.group(0).strip() for match in re.finditer(pattern, text))
        return matches

    @staticmethod
    def _append_image_value(value: object, image_urls: list[str], image_data: list[str]) -> None:
        if not isinstance(value, str) or not value:
            return
        if value.startswith(("http://", "https://")):
            image_urls.append(value)
            return
        image_data.append(value)
