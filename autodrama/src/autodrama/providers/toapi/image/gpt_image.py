from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef, ImageGenerationResult
from autodrama.providers.http import request_id_from_response
from autodrama.providers.media_refs import (
    is_remote_url_expired,
    local_ref_path,
    signed_url_expiration,
)


class ToAPIImageProvider:
    """ToAPI async image provider for GPT Image 2."""

    name = "toapi"
    supports_reference_images = True
    DEFAULT_MAX_REFERENCE_UPLOAD_BYTES = 10_000_000
    DEFAULT_MIN_REFERENCE_UPLOAD_BYTES = 5_000_000
    DEFAULT_REFERENCE_URL_CACHE_TTL_SECONDS = 86_400
    DEFAULT_MAX_ATTEMPTS = 10
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

    PIXEL_SIZE_TO_RATIO = {
        "1024x1024": "1:1",
        "2048x2048": "1:1",
        "1536x1024": "3:2",
        "2048x1360": "3:2",
        "1024x1536": "2:3",
        "1360x2048": "2:3",
        "2048x1536": "4:3",
        "1536x2048": "3:4",
        "2560x2048": "5:4",
        "2048x2560": "4:5",
        "2048x1152": "16:9",
        "3840x2160": "16:9",
        "1152x2048": "9:16",
        "2160x3840": "9:16",
        "2688x1344": "2:1",
        "3840x1920": "2:1",
        "1344x2688": "1:2",
        "1920x3840": "1:2",
        "2688x1152": "21:9",
        "3840x1648": "21:9",
        "1152x2688": "9:21",
        "1648x3840": "9:21",
    }

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://toapis.com").rstrip("/")
        self.model = settings.models.get("image", "gpt-image-2")
        self.api_key = settings.secret("api_key_env")
        self.resolution = str(
            settings.options.get("resolution")
            or settings.options.get("image_resolution")
            or "4K"
        )
        self.size = str(settings.options.get("size") or settings.options.get("image_size") or "16:9")
        self.response_format = str(settings.options.get("response_format") or "url")
        self.n = int(settings.options.get("n") or 1)
        self.poll_interval_seconds = float(settings.options.get("poll_interval_seconds") or 3)
        self.max_wait_seconds = float(settings.options.get("max_wait_seconds") or 900)
        self.max_reference_images = int(settings.options.get("max_reference_images") or 16)
        self.max_reference_upload_bytes = int(
            settings.options.get("max_reference_upload_bytes")
            or settings.options.get("reference_upload_max_bytes")
            or self.DEFAULT_MAX_REFERENCE_UPLOAD_BYTES
        )
        self.min_reference_upload_bytes = int(
            settings.options.get("min_reference_upload_bytes")
            or settings.options.get("reference_upload_min_bytes")
            or self.DEFAULT_MIN_REFERENCE_UPLOAD_BYTES
        )
        if self.min_reference_upload_bytes >= self.max_reference_upload_bytes:
            self.min_reference_upload_bytes = self.max_reference_upload_bytes // 2
        self.reference_url_cache_ttl_seconds = max(
            1,
            int(
                settings.options.get("toapi_reference_url_cache_ttl_seconds")
                or settings.options.get("reference_url_cache_ttl_seconds")
                or self.DEFAULT_REFERENCE_URL_CACHE_TTL_SECONDS
            ),
        )
        self.max_attempts = self._int_option(
            "toapi_max_attempts",
            "max_attempts",
            default=self.DEFAULT_MAX_ATTEMPTS,
        )
        self._reference_reupload_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._reference_reupload_tasks: dict[
            tuple[str, int, int],
            asyncio.Task[dict[str, Any]],
        ] = {}
        self._reference_cache_lock = asyncio.Lock()
        self._expired_reference_warnings: set[tuple[str, str]] = set()

    @property
    def generation_endpoint(self) -> str:
        base_url = self._api_root(self.base_url)
        return f"{base_url}/v1/images/generations"

    @property
    def upload_endpoint(self) -> str:
        base_url = self._api_root(self.base_url)
        return f"{base_url}/v1/uploads/images"

    @staticmethod
    def _api_root(base_url: str) -> str:
        value = base_url.rstrip("/")
        for suffix in ("/v1/images/generations", "/images/generations", "/v1/uploads/images", "/uploads/images", "/v1"):
            if value.endswith(suffix):
                value = value[: -len(suffix)].rstrip("/")
                break
        return value

    def _headers(self, *, json_content: bool = False) -> dict[str, str]:
        if not self.api_key:
            raise ProviderAuthError("Missing ToAPI API key environment variable")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

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
        images = reference_images if reference_images is not None else self._reference_image_urls(refs or [])
        payload: dict[str, Any] = {
            "model": str(metadata.get("model") or self._purpose_model(metadata) or self.model),
            "prompt": prompt,
            "n": int(metadata.get("n", self.n)),
            "size": self._normalize_size(
                metadata.get("size")
                or size
                or self._purpose_size(metadata)
                or self.size
            ),
            "resolution": str(
                metadata.get("resolution")
                or self._purpose_resolution(metadata)
                or self.resolution
            ),
            "response_format": str(metadata.get("response_format") or self.response_format),
        }
        if images:
            payload["reference_images"] = images[: self.max_reference_images]

        extra_parameters = metadata.get("parameters")
        if isinstance(extra_parameters, dict):
            payload.update(extra_parameters)
        return payload

    def _purpose_model(self, metadata: dict[str, Any]) -> str | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "clip_storyboard_image_generation":
            return self.settings.models.get("storyboard")
        if node_name == "roleboard_image_generation":
            return self.settings.models.get("roleboard")
        if node_name in {"prop_generation", "prop_image_generation"}:
            return self.settings.models.get("prop")
        if node_name == "layout_image_generation":
            return self.settings.models.get("layout")
        if node_name == "key_vision_image_generation":
            return self.settings.models.get("key_vision")
        return None

    def _purpose_size(self, metadata: dict[str, Any]) -> object | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "clip_storyboard_image_generation":
            return self.settings.options.get("storyboard_size") or "16:9"
        if node_name == "roleboard_image_generation":
            return (
                self.settings.options.get("roleboard_size")
                or "16:9"
            )
        if node_name in {"prop_generation", "prop_image_generation"}:
            return self.settings.options.get("prop_size") or "1:1"
        if node_name == "layout_image_generation":
            return self.settings.options.get("layout_size") or "16:9"
        if node_name == "key_vision_image_generation":
            return self.settings.options.get("key_vision_size") or "9:16"
        return None

    def _purpose_resolution(self, metadata: dict[str, Any]) -> object | None:
        node_name = self._purpose_node_name(metadata)
        if node_name == "clip_storyboard_image_generation":
            return self.settings.options.get("storyboard_resolution")
        if node_name == "roleboard_image_generation":
            return self.settings.options.get("roleboard_resolution")
        if node_name in {"prop_generation", "prop_image_generation"}:
            return self.settings.options.get("prop_resolution")
        if node_name == "layout_image_generation":
            return self.settings.options.get("layout_resolution")
        if node_name == "key_vision_image_generation":
            return self.settings.options.get("key_vision_resolution")
        return None

    @staticmethod
    def _purpose_node_name(metadata: dict[str, Any]) -> str:
        return str(metadata.get("provider_binding_node") or metadata.get("node_name") or "")

    @classmethod
    def _normalize_size(cls, value: object) -> str:
        text = str(value or "").strip()
        for separator in ("×", "X", "＊", "*", "ｘ", "Ｘ"):
            text = text.replace(separator, "x")
        text = "".join(text.split())
        return cls.PIXEL_SIZE_TO_RATIO.get(text.lower(), text)

    def _reference_image_urls(self, refs: list[AssetRef]) -> list[str]:
        images: list[str] = []
        for ref in refs:
            if ref.type != "image":
                continue
            value = self._ref_url(ref)
            if not value:
                continue
            images.append(value)
            if len(images) >= self.max_reference_images:
                break
        return images

    @staticmethod
    def _ref_url(ref: AssetRef) -> str | None:
        for value in (ref.url, ref.path):
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    async def _reference_images(self, client: httpx.AsyncClient, refs: list[AssetRef]) -> tuple[list[str], list[dict[str, Any]]]:
        uploaded = await self.ensure_reference_image_urls(refs)
        images: list[str] = []
        for ref in refs:
            if ref.type != "image":
                continue

            if url := self._ref_url(ref):
                images.append(url)
            elif ref.path:
                uploaded_item = await self._upload_reference_image(client, Path(ref.path))
                images.append(str(uploaded_item["url"]))
                uploaded.append(uploaded_item)
            if len(images) >= self.max_reference_images:
                break
        return images, uploaded

    async def ensure_reference_image_urls(
        self,
        refs: list[AssetRef] | None,
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for ref in refs or []:
            if ref.type != "image":
                continue
            path = local_ref_path(ref)
            url = str(ref.url or "").strip()
            expired = bool(url and is_remote_url_expired(url))
            if url and not expired and not force:
                continue
            if path is None:
                continue

            old_url = url or None
            uploaded_item = await self._coalesced_reference_reupload(path)
            if expired and not uploaded_item.get("persistent_cache"):
                self._warn_expired_reference(ref, path, url)
            new_url = str(uploaded_item["url"])
            ref.url = new_url
            if old_url:
                ref.metadata["expired_asset_url"] = old_url
            ref.metadata["expired_url_reuploaded"] = bool(old_url)
            ref.metadata.pop("expired_url_cleared_for_local_refresh", None)
            ref.metadata["uploaded_reference_image_url"] = new_url
            ref.metadata["uploaded_reference_image_provider"] = self.name
            ref.metadata["uploaded_reference_image_persistent_cache"] = bool(
                uploaded_item.get("persistent_cache")
            )
            resolved.append(dict(uploaded_item))
        return resolved

    async def reupload_expired_reference_images(
        self,
        refs: list[AssetRef] | None,
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        targets: list[AssetRef] = []
        for ref in refs or []:
            if ref.type != "image" or not ref.url:
                continue
            path = local_ref_path(ref)
            if path is None or (not force and not is_remote_url_expired(ref.url)):
                continue
            targets.append(ref)
        if not targets:
            return []

        uploaded = await self.ensure_reference_image_urls(targets, force=force)
        for ref in targets:
            get_logger().info(
                "ToAPI refreshed expired reference image ref=%s path=%s",
                ref.id or "-",
                local_ref_path(ref),
            )
        return uploaded

    def _warn_expired_reference(self, ref: AssetRef, path: Path, url: str) -> None:
        key = (str(path.resolve()), url)
        if key in self._expired_reference_warnings:
            return
        self._expired_reference_warnings.add(key)
        expires_at = signed_url_expiration(url)
        get_logger().warning(
            "Reference image URL expired; refreshing through ToAPI ref=%s path=%s expired_at=%s",
            ref.id or "-",
            path,
            expires_at.isoformat() if expires_at is not None else "unknown",
            extra={"console_color": "light_red"},
        )

    @staticmethod
    def _reference_reupload_key(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)

    async def _coalesced_reference_reupload(self, path: Path) -> dict[str, Any]:
        key = self._reference_reupload_key(path)
        if cached := self._reference_reupload_cache.get(key):
            return dict(cached)

        if cached := await self._load_persistent_reference_upload(path):
            self._reference_reupload_cache[key] = dict(cached)
            return dict(cached)

        task = self._reference_reupload_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._upload_reference_image_with_client(path))
            self._reference_reupload_tasks[key] = task

        try:
            uploaded_item = await asyncio.shield(task)
        except BaseException:
            if task.done() and self._reference_reupload_tasks.get(key) is task:
                self._reference_reupload_tasks.pop(key, None)
            raise

        self._reference_reupload_cache[key] = dict(uploaded_item)
        await self._save_persistent_reference_upload(path, uploaded_item)
        if self._reference_reupload_tasks.get(key) is task:
            self._reference_reupload_tasks.pop(key, None)
        return dict(uploaded_item)

    @staticmethod
    def _persistent_reference_cache_location(path: Path) -> tuple[Path, str] | None:
        resolved = path.resolve()
        parts = resolved.parts
        for index in range(len(parts) - 1):
            if parts[index].casefold() != "assets" or parts[index + 1].casefold() != "images":
                continue
            project_dir = Path(*parts[:index])
            if not project_dir:
                return None
            cache_path = project_dir / "assets" / "json" / "cache" / "reference_image_urls.json"
            cache_key = resolved.relative_to(project_dir).as_posix()
            return cache_path, cache_key
        return None

    async def _load_persistent_reference_upload(self, path: Path) -> dict[str, Any] | None:
        location = self._persistent_reference_cache_location(path)
        if location is None:
            return None
        cache_path, cache_key = location
        stat = path.stat()
        async with self._reference_cache_lock:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return None
            entries = payload.get("references") if isinstance(payload, dict) else None
            entry = entries.get(cache_key) if isinstance(entries, dict) else None
            if not isinstance(entry, dict):
                return None
            url = str(entry.get("url") or "").strip()
            expires_at_text = str(entry.get("expires_at") or "").strip()
            try:
                expires_at = datetime.fromisoformat(expires_at_text.replace("Z", "+00:00"))
                expires_at = expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc).astimezone(timezone.utc)
            except ValueError:
                expires_at = None
            if (
                not url
                or is_remote_url_expired(url)
                or expires_at is None
                or datetime.now(timezone.utc) >= expires_at
                or int(entry.get("size") or -1) != stat.st_size
                or int(entry.get("mtime_ns") or -1) != stat.st_mtime_ns
            ):
                return None
            return {
                "path": str(path),
                "url": url,
                "id": entry.get("id"),
                "mime_type": entry.get("mime_type"),
                "size": entry.get("upload_size"),
                "original_size": stat.st_size,
                "upload_size": entry.get("upload_size"),
                "persistent_cache": True,
            }

    async def _save_persistent_reference_upload(self, path: Path, uploaded_item: dict[str, Any]) -> None:
        location = self._persistent_reference_cache_location(path)
        url = str(uploaded_item.get("url") or "").strip()
        if location is None or not url:
            return
        cache_path, cache_key = location
        stat = path.stat()
        updated_at = datetime.now(timezone.utc)
        signed_expires_at = signed_url_expiration(url)
        effective_expires_at = signed_expires_at or (
            updated_at + timedelta(seconds=self.reference_url_cache_ttl_seconds)
        )
        async with self._reference_cache_lock:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            entries = payload.get("references")
            if not isinstance(entries, dict):
                entries = {}
            entries[cache_key] = {
                "url": url,
                "provider": self.name,
                "id": uploaded_item.get("id"),
                "mime_type": uploaded_item.get("mime_type"),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "upload_size": uploaded_item.get("upload_size") or uploaded_item.get("size"),
                "expires_at": effective_expires_at.isoformat(),
                "updated_at": updated_at.isoformat(),
            }
            payload["version"] = 1
            payload["references"] = entries
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(cache_path)

    async def _upload_reference_image_with_client(self, path: Path) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._http_timeout()) as client:
            return await self._upload_reference_image(client, path)

    @staticmethod
    def _local_ref_path(ref: AssetRef) -> Path | None:
        if not ref.path:
            return None
        value = str(ref.path)
        if value.startswith(("http://", "https://", "data:image/")):
            return None
        path = Path(value).expanduser()
        if path.exists() and path.is_file():
            return path
        return None

    async def _upload_reference_image(self, client: httpx.AsyncClient, path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            raise ProviderBadResponseError(f"ToAPI reference image does not exist: {path}")

        upload = await asyncio.to_thread(self._prepare_reference_upload, path)
        if upload.get("data") is not None:
            response = await client.post(
                self.upload_endpoint,
                headers=self._headers(),
                files={"file": (str(upload["filename"]), upload["data"], str(upload["mime_type"]))},
                data={"purpose": "generation"},
            )
        else:
            with path.open("rb") as file:
                response = await client.post(
                    self.upload_endpoint,
                    headers=self._headers(),
                    files={"file": (str(upload["filename"]), file, str(upload["mime_type"]))},
                    data={"purpose": "generation"},
                )
        body = self._json_response(response, label=f"ToAPI image upload {path.name}")
        self._raise_for_error(response, body, label=f"ToAPI image upload {path.name}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("url"):
            raise ProviderBadResponseError(f"ToAPI image upload response missing data.url: {body}")
        return {
            "path": str(path),
            "url": str(data["url"]),
            "id": data.get("id"),
            "mime_type": data.get("mime_type"),
            "size": data.get("size"),
            "upload_filename": upload.get("filename"),
            "original_size": upload.get("original_size"),
            "upload_size": upload.get("upload_size"),
            "resized_for_upload": upload.get("resized_for_upload", False),
            "resize_scale": upload.get("resize_scale"),
            "original_dimensions": upload.get("original_dimensions"),
            "upload_dimensions": upload.get("upload_dimensions"),
        }

    def _prepare_reference_upload(self, path: Path) -> dict[str, Any]:
        original_size = path.stat().st_size
        if original_size <= self.max_reference_upload_bytes:
            return {
                "filename": path.name,
                "mime_type": self._mime_type(path),
                "original_size": original_size,
                "upload_size": original_size,
                "resized_for_upload": False,
                "data": None,
            }

        upload = self._resize_reference_image_for_upload(path, original_size)
        get_logger().info(
            "ToAPI reference image resized for upload path=%s original_size=%.2fMB upload_size=%.2fMB "
            "scale=%.4f dimensions=%s->%s",
            path,
            original_size / (1024 * 1024),
            int(upload["upload_size"]) / (1024 * 1024),
            float(upload["resize_scale"]),
            upload["original_dimensions"],
            upload["upload_dimensions"],
        )
        return upload

    def _resize_reference_image_for_upload(self, path: Path, original_size: int) -> dict[str, Any]:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise ProviderBadResponseError(
                f"ToAPI reference image is larger than {self.max_reference_upload_bytes} bytes and Pillow is not installed: {path}"
            ) from exc

        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()

        output_format = self._reference_upload_format(path, image.format)
        candidates: list[dict[str, Any]] = []
        for save_options in self._reference_upload_save_options(output_format):
            candidate = self._largest_reference_upload_candidate(image, output_format, save_options)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            raise ProviderBadResponseError(
                f"Could not resize ToAPI reference image below {self.max_reference_upload_bytes} bytes: {path}"
            )

        in_range = [
            candidate
            for candidate in candidates
            if self.min_reference_upload_bytes <= len(candidate["data"]) <= self.max_reference_upload_bytes
        ]
        if in_range:
            selected = max(in_range, key=lambda item: (float(item["scale"]), len(item["data"])))
        else:
            selected = max(candidates, key=lambda item: (float(item["scale"]), len(item["data"])))
            if len(selected["data"]) < self.min_reference_upload_bytes:
                get_logger().warning(
                    "ToAPI reference image resized below target minimum path=%s upload_size=%.2fMB minimum=%.2fMB",
                    path,
                    len(selected["data"]) / (1024 * 1024),
                    self.min_reference_upload_bytes / (1024 * 1024),
                )

        suffix = self._reference_upload_suffix(output_format)
        return {
            "filename": f"{path.stem}_toapi_ref{suffix}",
            "mime_type": self._mime_type(Path(f"image{suffix}")),
            "original_size": original_size,
            "upload_size": len(selected["data"]),
            "resized_for_upload": True,
            "resize_scale": selected["scale"],
            "original_dimensions": [image.width, image.height],
            "upload_dimensions": [selected["width"], selected["height"]],
            "data": selected["data"],
        }

    def _largest_reference_upload_candidate(
        self,
        image,
        output_format: str,
        save_options: dict[str, Any],
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        low = 0.01
        high = 1.0
        for _ in range(10):
            scale = (low + high) / 2
            candidate = self._encode_reference_upload_image(image, output_format, scale, save_options)
            if len(candidate["data"]) <= self.max_reference_upload_bytes:
                best = candidate
                low = scale
            else:
                high = scale
        return best

    @staticmethod
    def _reference_upload_format(path: Path, image_format: str | None) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"} or str(image_format or "").upper() == "JPEG":
            return "JPEG"
        if suffix == ".webp" or str(image_format or "").upper() == "WEBP":
            return "WEBP"
        return "PNG"

    @staticmethod
    def _reference_upload_suffix(output_format: str) -> str:
        if output_format == "JPEG":
            return ".jpg"
        if output_format == "WEBP":
            return ".webp"
        return ".png"

    @staticmethod
    def _reference_upload_save_options(output_format: str) -> list[dict[str, Any]]:
        if output_format == "PNG":
            return [
                {"optimize": False, "compress_level": 6},
            ]
        if output_format == "JPEG":
            return [
                {"quality": 95, "optimize": True, "subsampling": 0},
                {"quality": 98, "optimize": True, "subsampling": 0},
                {"quality": 100, "optimize": True, "subsampling": 0},
            ]
        if output_format == "WEBP":
            return [
                {"quality": 95, "method": 6},
                {"quality": 98, "method": 6},
                {"quality": 100, "method": 6},
            ]
        return [{}]

    def _encode_reference_upload_image(
        self,
        image,
        output_format: str,
        scale: float,
        save_options: dict[str, Any],
    ) -> dict[str, Any]:
        from PIL import Image

        width = max(1, round(image.width * scale))
        height = max(1, round(image.height * scale))
        resized = image
        if width != image.width or height != image.height:
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
        output_image = self._image_for_reference_upload_format(resized, output_format)
        buffer = BytesIO()
        output_image.save(buffer, format=output_format, **save_options)
        return {
            "data": buffer.getvalue(),
            "scale": scale,
            "width": width,
            "height": height,
        }

    @staticmethod
    def _image_for_reference_upload_format(image, output_format: str):
        if output_format != "JPEG":
            return image
        if image.mode == "RGB":
            return image

        from PIL import Image

        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            return background.convert("RGB")
        return image.convert("RGB")

    @staticmethod
    def _mime_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/png"

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
            raise ProviderAuthError("Missing ToAPI API key environment variable")

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
                    "ToAPI image generation failed after "
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
                        "ToAPI image generation failed after "
                        f"{attempt} attempt(s) for asset={metadata.get('asset_id') or '-'}: {exc}"
                    ) from exc
                raise

        raise ProviderBadResponseError(
            f"ToAPI image generation failed after {max_attempts} attempt(s): no response received"
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
            reference_images, uploaded_refs = await self._reference_images(client, refs or [])
            payload = self.build_payload(
                prompt,
                size=size,
                metadata=metadata,
                reference_images=reference_images,
            )
            get_logger().info(
                "ToAPI image request node=%s asset=%s model=%s size=%s resolution=%s refs=%d uploaded_refs=%d prompt_chars=%d",
                metadata.get("node_name") or "-",
                metadata.get("asset_id") or "-",
                payload.get("model") or "-",
                payload.get("size") or "-",
                payload.get("resolution") or "-",
                len(reference_images),
                len(uploaded_refs),
                len(prompt),
            )
            create_response = await client.post(
                self.generation_endpoint,
                headers=self._headers(json_content=True),
                json=payload,
            )
            create_body = self._json_response(create_response, label="ToAPI image generation")
            self._raise_for_error(create_response, create_body, label="ToAPI image generation")

            task_id = self._task_id(create_body)
            final_body = create_body
            image_urls, image_data = self._extract_images(final_body)
            status = str(final_body.get("status") or "").lower() if isinstance(final_body, dict) else None

            if task_id and (not image_urls and not image_data) and status != "completed":
                final_body = await self._wait_for_task(client, task_id)
                image_urls, image_data = self._extract_images(final_body)
                status = str(final_body.get("status") or "").lower()

        if not image_urls and not image_data:
            raise ProviderBadResponseError(f"ToAPI image response has no image URL or base64 data: {final_body}")

        raw_response = {
            "create_response": create_body,
            "final_response": final_body,
        }
        if uploaded_refs:
            raw_response["uploaded_reference_images"] = uploaded_refs

        return ImageGenerationResult(
            provider=self.name,
            model=str(payload["model"]),
            image_urls=image_urls,
            image_data=image_data,
            task_id=task_id,
            task_status=status,
            request_id=self._request_id(final_body, create_response),
            usage=self._usage(final_body, create_body),
            raw_response=raw_response,
        )

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
            "ToAPI image generation attempt %d/%d failed for asset=%s node=%s: %s; retrying in %.1fs",
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
            "toapi_retry_initial_delay_seconds",
            "retry_initial_delay_seconds",
            default=2.0,
        )
        max_delay = self._float_option(
            "toapi_retry_max_delay_seconds",
            "retry_max_delay_seconds",
            default=30.0,
        )
        delay = max(0.0, initial_delay) * (2 ** max(0, attempt - 1))
        return min(max(0.0, max_delay), delay)

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
    def is_safety_provider_error(cls, exc: ProviderBadResponseError) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "image_unsafe",
                "appear to be unsafe",
                "generated images appear to be unsafe",
            )
        )

    @classmethod
    def _is_retryable_provider_error(cls, exc: ProviderBadResponseError) -> bool:
        if cls.is_safety_provider_error(exc):
            return False
        text = str(exc).lower()
        non_retryable_markers = (
            "missing toapi api key",
            "reference image does not exist",
            "pillow is not installed",
            "could not resize toapi reference image",
            "image too large",
        )
        if any(marker in text for marker in non_retryable_markers):
            return False
        for status_code in cls.RETRYABLE_HTTP_STATUS_CODES:
            if f"http {status_code}" in text:
                return True
        return any(
            marker in text
            for marker in (
                "generation_failed",
                "call upstream api failed",
                "upstream",
                "decode response failed",
                "unexpected end of json input",
                "non-json response",
                "json response is not an object",
                "response missing data.url",
                "has no image url or base64 data",
                "did not complete after",
                "timeout",
                "temporarily",
                "too many requests",
                "rate limit",
                "server error",
                "bad gateway",
                "service unavailable",
                "gateway timeout",
            )
        )

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        text = str(exc).strip()
        return text or exc.__class__.__name__

    def _http_timeout(self) -> httpx.Timeout:
        timeout = float(self.runtime.request_timeout_seconds)
        connect_timeout = min(30.0, max(5.0, timeout))
        return httpx.Timeout(
            timeout=timeout,
            connect=connect_timeout,
            read=timeout,
            write=connect_timeout,
            pool=connect_timeout,
        )

    async def _wait_for_task(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        started_at = asyncio.get_running_loop().time()
        while True:
            response = await client.get(
                f"{self.generation_endpoint}/{task_id}",
                headers=self._headers(),
            )
            body = self._json_response(response, label=f"ToAPI image task {task_id}")
            self._raise_for_error(response, body, label=f"ToAPI image task {task_id}")
            if not isinstance(body, dict):
                raise ProviderBadResponseError(f"ToAPI image task response is not an object: {body}")

            status = str(body.get("status") or "").lower()
            if status == "completed":
                return body
            if status == "failed":
                raise ProviderBadResponseError(f"ToAPI image task {task_id} failed: {body.get('error') or body}")

            elapsed = asyncio.get_running_loop().time() - started_at
            if elapsed >= self.max_wait_seconds:
                raise ProviderBadResponseError(
                    f"ToAPI image task {task_id} did not complete after {self.max_wait_seconds:g}s; "
                    f"last status={status or '-'}"
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
        value = body.get("id") or body.get("task_id")
        if value:
            return str(value)
        output = body.get("output")
        if isinstance(output, dict) and output.get("task_id"):
            return str(output["task_id"])
        return None

    @classmethod
    def _extract_images(cls, body: dict[str, Any]) -> tuple[list[str], list[str]]:
        image_urls: list[str] = []
        image_data: list[str] = []
        cls._extract_image_item(body, image_urls, image_data)
        return cls._dedupe(image_urls), cls._dedupe(image_data)

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

        for key in ("url", "image_url", "output_url", "b64_json", "image", "image_base64"):
            if key in item:
                cls._extract_image_item(item[key], image_urls, image_data)
        for key in ("data", "images", "choices", "output", "result", "results"):
            if key in item:
                cls._extract_image_item(item[key], image_urls, image_data)

    @staticmethod
    def _append_image_value(value: object, image_urls: list[str], image_data: list[str]) -> None:
        if not isinstance(value, str) or not value:
            return
        if value.startswith(("http://", "https://")):
            if ToAPIImageProvider._looks_like_image_url(value):
                image_urls.append(value)
            return
        if value.startswith("data:image/") or len(value) > 200:
            image_data.append(value)

    @staticmethod
    def _looks_like_image_url(value: str) -> bool:
        path = urlparse(value).path.lower()
        if Path(path).suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return True
        return "files.toapis.com" in value or "blob.core.windows.net" in value

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            result.append(value)
            seen.add(value)
        return result

    @staticmethod
    def _request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        return request_id_from_response(body, response, "x-request-id", "x-requestid", "cf-ray")

    @staticmethod
    def _usage(final_body: dict[str, Any], create_body: dict[str, Any]) -> dict[str, Any]:
        usage = final_body.get("usage") or create_body.get("usage") or {}
        return usage if isinstance(usage, dict) else {}
