"""Abstract base for TTS / audio generators."""

from __future__ import annotations

from abc import ABC, abstractmethod

from autodrama.models.media import AudioAsset


class BaseTTSGenerator(ABC):
    """Abstract interface for text-to-speech providers."""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str = "",
        **kwargs,
    ) -> AudioAsset:
        """Synthesise a single line of text into speech."""

    @abstractmethod
    def synthesize_batch(
        self,
        lines: list[dict],
        **kwargs,
    ) -> list[AudioAsset]:
        """Synthesise multiple dialogue lines.

        Each element in *lines* is a dict with keys:
        ``text`` (required), ``character``, ``emotion``.
        """
