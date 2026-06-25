from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout


class MediaStore:
    DEFAULT_DOWNLOAD_ATTEMPTS = 5
    RETRYABLE_DOWNLOAD_STATUS_CODES = {
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

    def __init__(self, layout: ProjectLayout, *, timeout_seconds: int) -> None:
        self.layout = layout
        self.timeout_seconds = timeout_seconds

    def project_relative(self, project_dir: Path, path: Path) -> str:
        return self.layout.project_relative(project_dir, path)

    @staticmethod
    def _base64_payload(data: str) -> str:
        if data.startswith("data:") and ";base64," in data:
            return data.split(";base64,", 1)[1]
        return data

    async def _download(self, url: str, *, label: str) -> bytes:
        last_error: Exception | None = None
        delay_seconds = 1.0
        attempts = self.DEFAULT_DOWNLOAD_ATTEMPTS
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.get(url)
                    if response.status_code < 400:
                        return response.content
                    message = (
                        f"Failed to download generated {label} HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )
                    if response.status_code not in self.RETRYABLE_DOWNLOAD_STATUS_CODES:
                        raise RuntimeError(message)
                    last_error = RuntimeError(message)
                except httpx.HTTPError as exc:
                    last_error = exc

                if attempt >= attempts:
                    break
                get_logger().warning(
                    "media download attempt %d/%d failed for %s: %s; retrying in %.1fs",
                    attempt,
                    attempts,
                    label,
                    last_error.__class__.__name__ if last_error else "unknown",
                    delay_seconds,
                )
                await asyncio.sleep(delay_seconds)
                delay_seconds = min(10.0, delay_seconds * 2)

        if last_error is not None:
            raise RuntimeError(
                f"Failed to download generated {label} after {attempts} attempt(s): {last_error}"
            ) from last_error
        raise RuntimeError(f"Failed to download generated {label}: no response received")

    async def write_first_generated_image(self, project_dir: Path, output_path: Path, result: Any) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.image_data:
            output_path.write_bytes(base64.b64decode(self._base64_payload(result.image_data[0])))
            return self.project_relative(project_dir, output_path)

        if not result.image_urls:
            raise ValueError("Image generation result has no image data or URL")

        output_path.write_bytes(await self._download(result.image_urls[0], label="image"))
        return self.project_relative(project_dir, output_path)

    async def write_generated_music(self, project_dir: Path, output_path: Path, result: Any) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.audio_data:
            output_path.write_bytes(base64.b64decode(self._base64_payload(result.audio_data)))
            return self.project_relative(project_dir, output_path)

        if not result.audio_url:
            raise ValueError("Music generation result has no audio data or URL")

        output_path.write_bytes(await self._download(result.audio_url, label="music"))
        return self.project_relative(project_dir, output_path)

    async def write_generated_audio(
        self,
        project_dir: Path,
        output_path: Path,
        *,
        audio_data: str | None = None,
        audio_url: str | None = None,
    ) -> str | None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if audio_data:
            output_path.write_bytes(base64.b64decode(self._base64_payload(audio_data)))
            return self.project_relative(project_dir, output_path)

        if not audio_url:
            return None

        output_path.write_bytes(await self._download(audio_url, label="audio"))
        return self.project_relative(project_dir, output_path)

    async def write_generated_video(self, project_dir: Path, output_path: Path, result: Any) -> str | None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(result, "video_data", None):
            output_path.write_bytes(base64.b64decode(self._base64_payload(result.video_data)))
            return self.project_relative(project_dir, output_path)

        if not result.video_url:
            return None

        output_path.write_bytes(await self._download(result.video_url, label="video"))
        return self.project_relative(project_dir, output_path)
