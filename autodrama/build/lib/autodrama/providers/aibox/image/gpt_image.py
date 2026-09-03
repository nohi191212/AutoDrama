from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef, ImageGenerationResult
from autodrama.providers.http import request_id_from_response
from autodrama.providers.media_refs import is_remote_url_expired
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider


class AiboxImageProvider:
    """AI Box media provider for GPT Image 2."""

    name = "aibox"
    supports_reference_images = True
    DEFAULT_MAX_ATTEMPTS = 3
    MAX_REFERENCE_IMAGES = 10
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
    SIZE_VALUES = {
        "auto",
        "1024x1024",
        "1024x1536",
        "1536x1024",
        "960x1280",
        "1280x960",
        "1088x1920",
        "1920x1088",
        "2048x2048",
        "2048x3072",
        "3072x2048",
        "1920x2560",
        "2560x1920",
        "1440x2560",
        "2560x1440",
        "2880x2880",
        "2304x3456",
        "3456x2304",
        "2400x3200",
        "3200x2400",
        "2160x3840",
        "3840x2160",
    }
    RATIO_SIZE_MAP = {
        "1:1": {"1k": "1024x1024", "2k": "2048x2048", "4k": "2880x2880"},
        "3:2": {"1k": "1536x1024", "2k": "3072x2048", "4k": "3456x2304"},
        "2:3": {"1k": "1024x1536", "2k": "2048x3072", "4k": "2304x3456"},
        "4:3": {"1k": "1280x960", "2k": "2560x1920", "4k": "3200x2400"},
        "3:4": {"1k": "960x1280", "2k": "1920x2560", "4k": "2400x3200"},
        "16:9": {"1k": "1920x1088", "2k": "1920x1088", "4k": "3840x2160"},
        "9:16": {"1k": "1088x1920", "2k": "1088x1920", "4k": "2160x3840"},
        "1:2": {"1k": "1088x1920", "2k": "1088x1920", "4k": "2160x3840"},
        "2:1": {"1k": "1920x1088", "2k": "1920x1088", "4k": "3840x2160"},
    }

    def __init__(
        self,
        settings: ProviderSettings,
        runtime: RuntimeSettings,
        *,
        reference_uploader_settings: ProviderSettings | None = None,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://api.lk888.ai").rstrip("/")
        self.model = settings.models.get("image", "gpt-image-2-guan")
        self.api_key = settings.secret("api_key_env")
        self.size = str(settings.options.get("size") or settings.options.get("image_size") or "auto")
        self.quality = str(settings.options.get("quality") or "auto")
        self.resolution = str(settings.options.get("resolution") or settings.options.get("image_resolution") or "")
        self.response_format = str(settings.options.get("response_format") or "url")
        self.n = int(settings.options.get("n") or 1)
        self.request_timeout_seconds = float(
            settings.options.get("aibox_request_timeout_seconds")
            or settings.options.get("request_timeout_seconds")
            or self.runtime.request_timeout_seconds
        )
        self.poll_interval_seconds = float(
            settings.options.get("aibox_poll_interval_seconds")
            or settings.options.get("poll_interval_seconds")
            or 4
        )
        self.max_wait_seconds = float(
            settings.options.get("aibox_max_wait_seconds")
            or settings.options.get("max_wait_seconds")
            or 900
        )
        self.max_reference_images = min(
            self.MAX_REFERENCE_IMAGES,
            int(
                settings.options.get("aibox_max_reference_images")
                or settings.options.get("max_reference_images")
                or self.MAX_REFERENCE_IMAGES
            ),
        )
        self.max_attempts = self._int_option(
            "aibox_max_attempts",
            "max_attempts",
            default=self.DEFAULT_MAX_ATTEMPTS,
        )
        self.reference_uploader = (
            ToAPIImageProvider(reference_uploader_settings, runtime)
            if reference_uploader_settings is not None
            else None
        )

    @property
    def generation_endpoint(self) -> str:
        base_url = self._api_root(self.base_url)
        return f"{base_url}/v1/media/generate"

    @property
    def status_endpoint(self) -> str:
        base_url = self._api_root(self.base_url)
        return f"{base_url}/v1/media/status"

    @staticmethod
    def _api_root(base_url: str) -> str:
        value = base_url.rstrip("/")
        for suffix in ("/v1/media/generate", "/media/generate", "/v1/media/status", "/media/status", "/v1/media", "/v1"):
            if value.endswith(suffix):
                value = value[: -len(suffix)].rstrip("/")
                break
        return value

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderAuthError("Missing AIBOX API key environment variable")
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
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        requested_size = metadata.get("size") or size or self._purpose_size(metadata) or self.size
        requested_resolution = metadata.get("resolution") or self._purpose_resolution(metadata) or self.resolution
        params: dict[str, Any] = {
            "size": self._normalize_size(requested_size, requested_resolution),
            "quality": str(metadata.get("quality") or self._purpose_quality(metadata) or self.quality),
        }

        images = (
            reference_images
            if reference_images is not None
            else self._reference_images(refs or [], metadata=metadata)
        )
        if images:
            params["images"] = images

        notify_url = metadata.get("notify_url") or self.settings.options.get("notify_url")
        extra_parameters = metadata.get("parameters")
        if isinstance(extra_parameters, dict):
            nested_params = extra_parameters.get("params")
            if isinstance(nested_params, dict):
                for key in ("size", "quality", "images"):
                    if key in nested_params:
                        params[key] = nested_params[key]
            for key in ("size", "quality", "images"):
                if key in extra_parameters:
                    params[key] = extra_parameters[key]
            notify_url = extra_parameters.get("notify_url") or notify_url

        payload: dict[str, Any] = {
            "model": str(metadata.get("model") or self._purpose_model(metadata) or self.model),
            "prompt": prompt,
            "params": params,
        }
        if notify_url:
            payload["notify_url"] = str(notify_url)
        return payload

    def _purpose_model(self, metadata: dict[str, Any]) -> str | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "shot_keyframe_image_generation":
            return self.settings.models.get("shot_keyframe") or self.settings.models.get("shot") or self.settings.models.get("layout")
        if node_name == "roleboard_image_generation":
            return self.settings.models.get("roleboard") or self.settings.models.get("role_design")
        if node_name in {"prop_generation", "prop_image_generation"}:
            return self.settings.models.get("prop")
        if node_name == "layout_image_generation":
            return self.settings.models.get("layout")
        if node_name == "key_vision_image_generation":
            return self.settings.models.get("key_vision")
        if node_name == "role_full_body_generation":
            return self.settings.models.get("role_portrait")
        if node_name in {"role_multiview_generation", "role_design_generation"}:
            return self.settings.models.get("role_design")
        if "ref_frame" in node_name:
            return self.settings.models.get("ref_frame")
        return None

    def _purpose_size(self, metadata: dict[str, Any]) -> object | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "shot_keyframe_image_generation":
            return self.settings.options.get("shot_keyframe_size") or self.settings.options.get("shot_size")
        if node_name == "roleboard_image_generation":
            return (
                self.settings.options.get("aibox_roleboard_size")
                or self.settings.options.get("roleboard_size")
                or self.settings.options.get("role_design_size")
            )
        if node_name in {"prop_generation", "prop_image_generation"}:
            return self.settings.options.get("aibox_prop_size") or self.settings.options.get("prop_size")
        if node_name == "layout_image_generation":
            return self.settings.options.get("aibox_layout_size") or self.settings.options.get("layout_size")
        if node_name == "key_vision_image_generation":
            return self.settings.options.get("aibox_key_vision_size") or self.settings.options.get("key_vision_size")
        if node_name == "role_full_body_generation":
            return self.settings.options.get("role_portrait_size")
        if node_name in {"role_multiview_generation", "role_design_generation"}:
            return self.settings.options.get("role_design_size")
        if "ref_frame" in node_name:
            return self.settings.options.get("ref_frame_size")
        return None

    def _purpose_resolution(self, metadata: dict[str, Any]) -> object | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "shot_keyframe_image_generation":
            return self.settings.options.get("shot_keyframe_resolution") or self.settings.options.get("shot_resolution")
        if node_name == "roleboard_image_generation":
            return self.settings.options.get("roleboard_resolution") or self.settings.options.get("role_design_resolution")
        if node_name in {"prop_generation", "prop_image_generation"}:
            return self.settings.options.get("prop_resolution")
        if node_name == "layout_image_generation":
            return self.settings.options.get("layout_resolution")
        if node_name == "key_vision_image_generation":
            return self.settings.options.get("key_vision_resolution")
        if node_name == "role_full_body_generation":
            return self.settings.options.get("role_portrait_resolution")
        if node_name in {"role_multiview_generation", "role_design_generation"}:
            return self.settings.options.get("role_design_resolution")
        if "ref_frame" in node_name:
            return self.settings.options.get("ref_frame_resolution")
        return None

    def _purpose_quality(self, metadata: dict[str, Any]) -> object | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "roleboard_image_generation":
            return self.settings.options.get("roleboard_quality") or self.settings.options.get("role_design_quality")
        if node_name == "key_vision_image_generation":
            return self.settings.options.get("key_vision_quality")
        return None

    @staticmethod
    def _purpose_node_name(metadata: dict[str, Any]) -> str:
        return str(metadata.get("provider_binding_node") or metadata.get("node_name") or "")

    @classmethod
    def _normalize_size(cls, size: object, resolution: object | None) -> str:
        text = str(size or "").strip()
        for separator in ("×", "X", "＊", "*", "ｘ", "Ｘ"):
            text = text.replace(separator, "x")
        text = re.sub(r"\s+", "", text).lower()
        if not text:
            return "auto"
        if text in cls.SIZE_VALUES:
            return text
        ratio = cls._aspect_ratio(text)
        if ratio and ratio in cls.RATIO_SIZE_MAP:
            bucket = cls._resolution_bucket(resolution)
            return cls.RATIO_SIZE_MAP[ratio].get(bucket) or cls.RATIO_SIZE_MAP[ratio]["2k"]
        return text

    @staticmethod
    def _resolution_bucket(value: object | None) -> str:
        text = str(value or "").strip().lower()
        if "4" in text or text in {"high", "large"}:
            return "4k"
        if "1" in text or text in {"low", "small"}:
            return "1k"
        return "2k"

    @staticmethod
    def _aspect_ratio(value: object) -> str | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        if ":" in text:
            left, right = text.split(":", 1)
            try:
                width = int(float(left))
                height = int(float(right))
            except ValueError:
                return text
            if width > 0 and height > 0:
                divisor = _gcd(width, height)
                return f"{width // divisor}:{height // divisor}"
        if "x" in text:
            left, right = text.split("x", 1)
            try:
                width = int(float(left))
                height = int(float(right))
            except ValueError:
                return None
            if width > 0 and height > 0:
                divisor = _gcd(width, height)
                return f"{width // divisor}:{height // divisor}"
        return None

    @classmethod
    def _aspect_ratio_parameter(cls, value: object) -> str | None:
        text = str(value or "").strip().lower()
        if ":" in text:
            return cls._aspect_ratio(text)
        if "x" in text:
            left, right = text.split("x", 1)
            try:
                width = int(float(left))
                height = int(float(right))
            except ValueError:
                return None
            if width <= 64 and height <= 64:
                return cls._aspect_ratio(text)
        return None

    def _reference_images(self, refs: list[AssetRef], *, metadata: dict[str, Any]) -> list[str]:
        images: list[str] = []
        max_reference_images = min(
            self.MAX_REFERENCE_IMAGES,
            int(metadata.get("max_reference_images") or self.max_reference_images),
        )
        for ref in refs:
            if ref.type != "image":
                continue
            value = self._ref_url(ref)
            if not value:
                continue
            images.append(value)
            if len(images) >= max_reference_images:
                break
        return images

    async def _resolve_reference_images(
        self,
        client: httpx.AsyncClient,
        refs: list[AssetRef],
        *,
        metadata: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        del client
        images: list[str] = []
        uploaded: list[dict[str, Any]] = []
        max_reference_images = min(
            self.MAX_REFERENCE_IMAGES,
            int(metadata.get("max_reference_images") or self.max_reference_images),
        )
        unresolved_local_refs = [
            ref
            for ref in refs
            if ref.type == "image"
            and self._local_ref_path(ref) is not None
            and not self._ref_url(ref)
        ]
        expired_local_refs = [
            ref
            for ref in refs
            if ref.type == "image"
            and self._local_ref_path(ref) is not None
            and ref.url
            and is_remote_url_expired(ref.url)
        ]
        refs_requiring_upload = [*expired_local_refs, *unresolved_local_refs]
        if refs_requiring_upload:
            if self.reference_uploader is None:
                raise ProviderBadResponseError(
                    "AIBOX reference image requires a public URL, but no upload provider is configured"
                )
            uploaded.extend(await self.reference_uploader.ensure_reference_image_urls(refs_requiring_upload))
        for ref in refs:
            if ref.type != "image":
                continue

            if url := self._ref_url(ref):
                images.append(url)
            elif local_path := self._local_ref_path(ref):
                raise ProviderBadResponseError(
                    f"AIBOX reference image upload did not produce a public URL: {local_path}"
                )
            elif ref.path and str(ref.path).startswith("data:image/"):
                raise ProviderBadResponseError(
                    "AIBOX reference image requires a public URL; inline base64 data is not supported"
                )

            if len(images) >= max_reference_images:
                break
        return images, uploaded

    @staticmethod
    def _ref_url(ref: AssetRef) -> str | None:
        for value in (ref.url, ref.path):
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    @staticmethod
    def _local_ref_path(ref: AssetRef) -> Path | None:
        if not ref.path:
            return None
        value = str(ref.path)
        if value.startswith(("http://", "https://", "data:image/", "asset://")):
            return None
        path = Path(value).expanduser()
        if path.exists() and path.is_file():
            return path
        return None

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        metadata = metadata or {}
        if not self.api_key:
            raise ProviderAuthError("Missing AIBOX API key environment variable")

        max_attempts = max(1, self.max_attempts)
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._generate_image_once(prompt, refs=refs, size=size, metadata=metadata)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < max_attempts:
                    await self._retry_after_failure(
                        attempt=attempt,
                        max_attempts=max_attempts,
                        metadata=metadata,
                        reason=f"{exc.__class__.__name__}: {self._format_exception(exc)}",
                    )
                    continue
                raise ProviderBadResponseError(
                    "AIBOX image generation failed after "
                    f"{attempt} attempt(s) for asset={metadata.get('asset_id') or '-'}: "
                    f"{exc.__class__.__name__}: {self._format_exception(exc)}"
                ) from exc
            except ProviderBadResponseError as exc:
                if attempt < max_attempts and self._is_retryable_provider_error(exc):
                    await self._retry_after_failure(
                        attempt=attempt,
                        max_attempts=max_attempts,
                        metadata=metadata,
                        reason=self._format_exception(exc),
                    )
                    continue
                if attempt > 1 and self._is_retryable_provider_error(exc):
                    raise ProviderBadResponseError(
                        "AIBOX image generation failed after "
                        f"{attempt} attempt(s) for asset={metadata.get('asset_id') or '-'}: {exc}"
                    ) from exc
                raise

        raise ProviderBadResponseError(
            f"AIBOX image generation failed after {max_attempts} attempt(s): no response received"
        )

    async def _generate_image_once(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        metadata = metadata or {}
        async with httpx.AsyncClient(timeout=self._http_timeout()) as client:
            reference_images, uploaded_refs = await self._resolve_reference_images(
                client,
                refs or [],
                metadata=metadata,
            )
            payload = self.build_payload(
                prompt,
                refs=refs,
                size=size,
                metadata=metadata,
                reference_images=reference_images,
            )
            get_logger().info(
                "AIBOX image request node=%s asset=%s model=%s size=%s quality=%s refs=%d uploaded_refs=%d prompt_chars=%d",
                metadata.get("node_name") or "-",
                metadata.get("asset_id") or "-",
                payload.get("model") or "-",
                payload.get("params", {}).get("size") or "-",
                payload.get("params", {}).get("quality") or "-",
                len(payload.get("params", {}).get("images") or []),
                len(uploaded_refs),
                len(prompt),
            )
            create_response = await client.post(
                self.generation_endpoint,
                headers=self._headers(),
                json=payload,
            )
            create_body = self._json_response(create_response, label="AIBOX image generation")
            self._raise_for_error(create_response, create_body, label="AIBOX image generation")

            image_urls, image_data = self._extract_images(create_body)
            task_id = self._task_id(create_body)
            final_body = create_body
            status = self._status(create_body)
            if task_id and not image_urls and not image_data and not self._is_completed(create_body):
                final_body = await self._wait_for_task(client, task_id)
                image_urls, image_data = self._extract_images(final_body)
                status = self._status(final_body)

        if not image_urls:
            raise ProviderBadResponseError(f"AIBOX image response has no image URL or base64 data: {final_body}")

        raw_response = {
            "create_response": create_body,
            "final_response": final_body,
        }
        if uploaded_refs:
            raw_response["uploaded_reference_images"] = uploaded_refs

        return ImageGenerationResult(
            provider=self.name,
            model=str(payload["model"]),
            image_urls=self._dedupe(image_urls),
            image_data=[],
            task_id=task_id,
            task_status=status,
            request_id=self._request_id(final_body, create_response),
            usage=self._usage(final_body, create_body),
            raw_response=raw_response,
        )

    async def _wait_for_task(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        started_at = asyncio.get_running_loop().time()
        while True:
            response = await client.get(
                self.status_endpoint,
                headers=self._headers(),
                params={"task_id": task_id},
            )
            body = self._json_response(response, label=f"AIBOX image task {task_id}")
            self._raise_for_error(response, body, label=f"AIBOX image task {task_id}")
            if self._is_completed(body):
                if self._status(body) == "failed":
                    raise ProviderBadResponseError(f"AIBOX image task {task_id} failed: {body.get('error') or body}")
                return body

            elapsed = asyncio.get_running_loop().time() - started_at
            if elapsed >= self.max_wait_seconds:
                raise ProviderBadResponseError(
                    f"AIBOX image task {task_id} did not complete after {self.max_wait_seconds:g}s; "
                    f"last status={self._status(body) or '-'}"
                )
            await asyncio.sleep(max(0.5, self.poll_interval_seconds))

    @staticmethod
    def _json_response(response: httpx.Response, *, label: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"{label} returned non-JSON response: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderBadResponseError(f"{label} JSON response is not an object")
        return body

    @staticmethod
    def _raise_for_error(response: httpx.Response, body: dict[str, Any], *, label: str) -> None:
        if response.status_code < 400 and body.get("success") is not False:
            return
        message: object = body.get("message")
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or message or error
        elif error:
            message = error
        raise ProviderBadResponseError(f"{label} failed with HTTP {response.status_code}: {message or body}")

    @staticmethod
    def _task_id(body: dict[str, Any]) -> str | None:
        for key in ("task_id", "taskId", "id"):
            value = body.get(key)
            if value:
                return str(value)
        data = body.get("data")
        if isinstance(data, dict):
            for key in ("task_id", "taskId", "id"):
                value = data.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def _status(body: dict[str, Any]) -> str | None:
        value = body.get("state") or body.get("status")
        return str(value).lower() if value else None

    @staticmethod
    def _is_completed(body: dict[str, Any]) -> bool:
        if "is_final" in body:
            return body.get("is_final") is True
        return str(body.get("state") or "").lower() in {"success", "failed"}

    @classmethod
    def _extract_images(cls, body: dict[str, Any]) -> tuple[list[str], list[str]]:
        image_urls: list[str] = []
        image_data: list[str] = []
        cls._extract_image_item(body.get("data"), image_urls, image_data)
        cls._extract_image_item(body.get("output"), image_urls, image_data)
        cls._extract_image_item(body.get("result"), image_urls, image_data)
        cls._extract_image_item(body.get("result_url"), image_urls, image_data)
        cls._extract_image_item(body.get("url"), image_urls, image_data)
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

        for key in ("url", "image_url", "output_url", "result_url", "b64_json", "image", "image_base64"):
            if key in item:
                cls._extract_image_item(item[key], image_urls, image_data)
        for key in ("data", "result", "results", "output", "images"):
            if key in item:
                cls._extract_image_item(item[key], image_urls, image_data)

    @staticmethod
    def _append_image_value(value: object, image_urls: list[str], image_data: list[str]) -> None:
        if not isinstance(value, str) or not value:
            return
        if value.startswith(("http://", "https://")):
            image_urls.append(value)
            return
        if value.startswith("data:image/"):
            image_data.append(value)
            return
        if re.fullmatch(r"[A-Za-z0-9+/=\r\n]{200,}", value):
            image_data.append(value)

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        return request_id_from_response(body, response, "x-request-id", "x-requestid", "cf-ray")

    @staticmethod
    def _usage(final_body: dict[str, Any], create_body: dict[str, Any]) -> dict[str, Any]:
        usage = final_body.get("usage") or create_body.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        cost = final_body.get("cost") or create_body.get("cost")
        if cost is not None:
            usage = dict(usage)
            usage["cost"] = cost
        return usage

    async def _retry_after_failure(
        self,
        *,
        attempt: int,
        max_attempts: int,
        metadata: dict[str, Any],
        reason: str,
    ) -> None:
        delay_seconds = self._retry_delay_seconds(attempt)
        get_logger().warning(
            "AIBOX image generation attempt %d/%d failed for asset=%s node=%s: %s; retrying in %.1fs",
            attempt,
            max_attempts,
            metadata.get("asset_id") or "-",
            metadata.get("node_name") or "-",
            reason,
            delay_seconds,
        )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    def _retry_delay_seconds(self, attempt: int) -> float:
        initial_delay = self._float_option(
            "aibox_retry_initial_delay_seconds",
            "retry_initial_delay_seconds",
            default=2.0,
        )
        max_delay = self._float_option(
            "aibox_retry_max_delay_seconds",
            "retry_max_delay_seconds",
            default=30.0,
        )
        delay = max(0.0, initial_delay) * (2 ** max(0, attempt - 1))
        return min(max(0.0, max_delay), delay)

    def _http_timeout(self) -> httpx.Timeout:
        timeout = float(self.request_timeout_seconds)
        connect_timeout = min(30.0, max(5.0, timeout))
        return httpx.Timeout(
            timeout=timeout,
            connect=connect_timeout,
            read=timeout,
            write=connect_timeout,
            pool=connect_timeout,
        )

    def _int_option(self, primary_key: str, fallback_key: str, *, default: int) -> int:
        raw_value = self.settings.options.get(primary_key, self.settings.options.get(fallback_key, default))
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return max(1, int(default))

    def _float_option(self, primary_key: str, fallback_key: str, *, default: float) -> float:
        raw_value = self.settings.options.get(primary_key, self.settings.options.get(fallback_key, default))
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _is_retryable_provider_error(cls, exc: ProviderBadResponseError) -> bool:
        text = str(exc).lower()
        if "missing aibox api key" in text:
            return False
        if "requires a public url" in text or "inline base64 data is not supported" in text:
            return False
        for status_code in cls.RETRYABLE_HTTP_STATUS_CODES:
            if f"http {status_code}" in text:
                return True
        return any(
            marker in text
            for marker in (
                "timeout",
                "temporarily",
                "too many requests",
                "rate limit",
                "server error",
                "bad gateway",
                "service unavailable",
                "gateway timeout",
                "no available compatible account",
                "没有可用的兼容账号",
                "did not complete after",
                "non-json response",
                "json response is not an object",
                "has no image url or base64 data",
            )
        )

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        parts: list[str] = []
        current: BaseException | None = exc
        while current is not None and len(parts) < 8:
            exception_type = type(current).__name__
            detail = str(current).strip()
            if not detail and current.args:
                detail = repr(current.args)
            parts.append(f"{exception_type}: {detail}" if detail else exception_type)
            current = current.__cause__ or current.__context__
        return " <- ".join(parts)


def _gcd(left: int, right: int) -> int:
    while right:
        left, right = right, left % right
    return max(1, abs(left))
