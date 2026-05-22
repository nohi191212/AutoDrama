from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from autodrama.repositories.project_layout import ProjectLayout


class MediaStore:
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

    @staticmethod
    def _audio_extension(audio_format: str | None) -> str:
        extension = (audio_format or "mp3").lower().lstrip(".")
        extension = {"ogg_opus": "opus"}.get(extension, extension)
        if extension not in {"wav", "mp3", "pcm", "opus", "ogg", "m4a", "aac"}:
            extension = "bin"
        return extension

    async def _download(self, url: str, *, label: str) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated {label} HTTP {response.status_code}: {response.text[:500]}")
        return response.content

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

    def write_preview_audio(
        self,
        project_dir: Path,
        *,
        audio_id: str,
        data: str | None,
        response_format: str | None,
    ) -> str | None:
        if not data:
            return None

        output_dir = project_dir / "assets" / "audios" / "role_voices"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{audio_id}.{self._audio_extension(response_format)}"
        output_path.write_bytes(base64.b64decode(self._base64_payload(data)))
        return self.project_relative(project_dir, output_path)
