"""OpenAI DALL-E image generator."""

from __future__ import annotations

import time
from pathlib import Path

import requests
from loguru import logger

from autodrama.config.schema import ImageProviderConfig
from autodrama.media.image.base import BaseImageGenerator
from autodrama.models.media import ImageAsset


class DALLEGenerator(BaseImageGenerator):
    """Image generator using OpenAI's DALL-E API."""

    def __init__(self, config: ImageProviderConfig, save_dir: str | Path = "./output/images") -> None:
        import openai

        self._client = openai.OpenAI(api_key=config.api_key or None)
        self._model = config.model
        self._quality = config.quality
        self._save_dir = Path(save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        size: tuple[int, int] = (1792, 1024),
        **kwargs,
    ) -> ImageAsset:
        resp = self._client.images.generate(
            model=self._model,
            prompt=prompt,
            n=1,
            size=f"{size[0]}x{size[1]}",
            quality=self._quality,
            **kwargs,
        )

        image_url = resp.data[0].url
        if not image_url:
            raise RuntimeError("DALL-E returned no image URL")

        image_path = self._download(image_url)
        return ImageAsset(
            scene_number=0,
            shot_index=0,
            prompt=prompt,
            image_path=str(image_path),
            width=size[0],
            height=size[1],
            provider="openai",
        )

    def generate_batch(
        self,
        prompts: list[str],
        size: tuple[int, int] = (1792, 1024),
        **kwargs,
    ) -> list[ImageAsset]:
        # DALL-E doesn't have a native batch endpoint; iterate sequentially
        return [self.generate(p, size, **kwargs) for p in prompts]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _download(self, url: str) -> Path:
        filename = f"dalle_{int(time.time() * 1000)}.png"
        filepath = self._save_dir / filename
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
        logger.debug(f"Downloaded image to {filepath}")
        return filepath
