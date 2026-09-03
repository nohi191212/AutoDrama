from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef, ImageGenerationResult
from autodrama.providers.http import request_id_from_response
from autodrama.providers.media_refs import file_to_data_url
from autodrama.providers.rightcode.url_utils import resolve_rightcode_endpoint


class RightCodeImageProvider:
    """RightCode image provider via the OpenAI-compatible Images API."""

    name = "rightcode"
    supports_reference_images = True
    RETRYABLE_HTTP_STATUS_CODES = {
        408,
        409,
        425,
        429,
        500,
        502,
        503,
        504,
        520,
        521,
        522,
        523,
        524,
    }

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.model = settings.models.get("image", "gpt-image-2")
        self.base_url = (settings.base_url or "https://www.right.codes").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.api_key = settings.secret("api_key_env")
        self.max_reference_images = int(
            settings.options.get("rightcode_max_reference_images")
            or settings.options.get("max_reference_images")
            or 16
        )

    def refresh_endpoint(self) -> None:
        self.endpoint = self._resolve_endpoint(self.base_url)

    def _resolve_endpoint(self, base_url: str) -> str:
        settings = self.settings.model_copy(deep=True)
        settings.base_url = base_url
        return resolve_rightcode_endpoint(
            settings,
            capability="image",
            model_key="image",
            model=self.model,
            default_base_path="/draw",
            api_path="/v1/images/generations",
        )

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

        response = await self._post_with_retries(payload, metadata=metadata)

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

    async def _post_with_retries(self, payload: dict[str, Any], *, metadata: dict[str, Any]) -> httpx.Response:
        max_attempts = self._max_attempts()
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
                    response = await client.post(
                        self.endpoint,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                if attempt < max_attempts:
                    await self._retry_after_failure(
                        attempt=attempt,
                        max_attempts=max_attempts,
                        reason=f"timeout: {self._format_exception(exc)}",
                        metadata=metadata,
                    )
                    continue
                raise ProviderBadResponseError(
                    "RightCode image generation timed out after "
                    f"{attempt} attempt(s) calling {self.endpoint}: {self._format_exception(exc)}"
                ) from exc
            except httpx.TransportError as exc:
                if attempt < max_attempts:
                    await self._retry_after_failure(
                        attempt=attempt,
                        max_attempts=max_attempts,
                        reason=f"transport error: {self._format_exception(exc)}",
                        metadata=metadata,
                    )
                    continue
                raise ProviderBadResponseError(
                    "RightCode image generation request failed after "
                    f"{attempt} attempt(s) calling {self.endpoint}: {self._format_exception(exc)}"
                ) from exc

            if response.status_code < 400:
                return response

            is_retryable_response = self._is_retryable_response(response)
            if is_retryable_response and attempt < max_attempts:
                await self._retry_after_failure(
                    attempt=attempt,
                    max_attempts=max_attempts,
                    reason=f"HTTP {response.status_code}: {response.text[:200]}",
                    metadata=metadata,
                )
                continue

            message = f"RightCode image generation failed with HTTP {response.status_code}: {response.text[:500]}"
            if is_retryable_response and attempt > 1:
                message = (
                    "RightCode image generation failed after "
                    f"{attempt} attempt(s) with HTTP {response.status_code}: {response.text[:500]}"
                )
            raise ProviderBadResponseError(message)

        raise ProviderBadResponseError(
            f"RightCode image generation failed after {max_attempts} attempt(s): no response received"
        )

    def _max_attempts(self) -> int:
        raw_value = (
            self.settings.options.get("rightcode_max_attempts")
            or self.settings.options.get("max_attempts")
            or self.runtime.max_media_retry
        )
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return max(1, int(self.runtime.max_media_retry))

    async def _retry_after_failure(
        self,
        *,
        attempt: int,
        max_attempts: int,
        reason: str,
        metadata: dict[str, Any],
    ) -> None:
        delay_seconds = self._retry_delay_seconds(attempt)
        logger = get_logger()
        if logger.handlers:
            logger.warning(
                "RightCode image generation attempt %d/%d failed for asset=%s: %s; retrying in %.1fs",
                attempt,
                max_attempts,
                metadata.get("asset_id") or "-",
                reason,
                delay_seconds,
            )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    def _retry_delay_seconds(self, attempt: int) -> float:
        initial_delay = self._float_option(
            "rightcode_retry_initial_delay_seconds",
            "retry_initial_delay_seconds",
            default=2.0,
        )
        max_delay = self._float_option(
            "rightcode_retry_max_delay_seconds",
            "retry_max_delay_seconds",
            default=30.0,
        )
        delay = max(0.0, initial_delay) * (2 ** max(0, attempt - 1))
        return min(max(0.0, max_delay), delay)

    def _float_option(self, primary_key: str, fallback_key: str, *, default: float) -> float:
        raw_value = self.settings.options.get(primary_key, self.settings.options.get(fallback_key, default))
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _is_retryable_http_status(cls, status_code: int) -> bool:
        return status_code in cls.RETRYABLE_HTTP_STATUS_CODES

    @classmethod
    def _is_retryable_response(cls, response: httpx.Response) -> bool:
        if cls._is_retryable_http_status(response.status_code):
            return True
        return cls._has_retryable_error_body(response)

    @staticmethod
    def _has_retryable_error_body(response: httpx.Response) -> bool:
        try:
            body = response.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False

        error = body.get("error")
        if not isinstance(error, dict):
            return False

        fields = [
            str(error.get("type") or "").lower(),
            str(error.get("code") or "").lower(),
            str(error.get("message") or "").lower(),
        ]
        return any(
            marker in field
            for field in fields
            for marker in (
                "upstream_error",
                "upstream timeout",
                "excessive system load",
                "server overloaded",
            )
        )

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        text = str(exc).strip()
        if text:
            return text
        return exc.__class__.__name__

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
        if "size" in payload and payload["size"] is not None:
            payload["size"] = self._normalize_size(payload["size"])
        return payload

    @staticmethod
    def _normalize_size(value: object) -> str:
        text = str(value).strip()
        for separator in ("×", "X", "＊", "*", "ｘ", "Ｘ"):
            text = text.replace(separator, "x")
        return re.sub(r"\s+", "", text)

    def _purpose_model(self, metadata: dict[str, Any]) -> str | None:
        if self._is_roleboard_image(metadata):
            return (
                self.settings.models.get("rightcode_roleboard")
                or self.settings.models.get("roleboard")
            )
        if str(metadata.get("node_name") or "") == "key_vision_image_generation":
            return self.settings.models.get("rightcode_key_vision") or self.settings.models.get("key_vision")
        return None

    def _purpose_size(self, metadata: dict[str, Any]) -> object | None:
        if self._is_roleboard_image(metadata):
            return (
                self.settings.options.get("rightcode_roleboard_size")
                or self.settings.options.get("roleboard_size")
            )
        if str(metadata.get("node_name") or "") == "key_vision_image_generation":
            return self.settings.options.get("rightcode_key_vision_size") or self.settings.options.get("key_vision_size")
        return None

    def _purpose_parameter(self, key: str, metadata: dict[str, Any]) -> object | None:
        if key == "quality":
            if self._is_roleboard_image(metadata):
                return (
                    self.settings.options.get("rightcode_roleboard_quality")
                    or self.settings.options.get("roleboard_quality")
                )
            if str(metadata.get("node_name") or "") == "key_vision_image_generation":
                return (
                    self.settings.options.get("rightcode_key_vision_quality")
                    or self.settings.options.get("key_vision_quality")
                )
        return None

    @staticmethod
    def _is_roleboard_image(metadata: dict[str, Any]) -> bool:
        return str(metadata.get("node_name") or "") == "roleboard_image_generation"

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
        return file_to_data_url(path, expected_type="image", default_mime="image/png")

    @staticmethod
    def _request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        return request_id_from_response(body, response, "x-request-id", "x-requestid")

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
