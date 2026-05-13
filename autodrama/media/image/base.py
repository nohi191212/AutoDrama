"""Abstract base for image generators."""

from __future__ import annotations

from abc import ABC, abstractmethod

from autodrama.models.media import ImageAsset


class BaseImageGenerator(ABC):
    """Abstract interface for image-generation providers (DALL-E, SD, etc.)."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        size: tuple[int, int] = (1792, 1024),
        **kwargs,
    ) -> ImageAsset:
        """Generate a single image from a text prompt."""

    @abstractmethod
    def generate_batch(
        self,
        prompts: list[str],
        size: tuple[int, int] = (1792, 1024),
        **kwargs,
    ) -> list[ImageAsset]:
        """Generate multiple images (may parallelise for efficiency)."""
