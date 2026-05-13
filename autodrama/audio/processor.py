"""Audio processing — concatenation, mixing, volume adjustment with pydub."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydub import AudioSegment

from autodrama.config.schema import AppConfig
from autodrama.models.media import AudioAsset, BackgroundMusic


class AudioProcessor:
    """Concatenate, mix, and process audio segments via pydub."""

    def __init__(self, config: AppConfig) -> None:
        self._pipe = config.pipeline

    def concatenate(self, assets: list[AudioAsset]) -> AudioSegment:
        """Concatenate all dialogue audio clips in order.

        Returns:
            A single pydub AudioSegment.
        """
        combined = AudioSegment.empty()
        for asset in assets:
            if asset.audio_path and Path(asset.audio_path).exists():
                seg = AudioSegment.from_file(asset.audio_path)
                combined += seg
            else:
                combined += AudioSegment.silent(
                    duration=int(asset.duration_seconds * 1000)
                )
        return combined

    def mix_with_music(
        self,
        dialogue: AudioSegment,
        music: BackgroundMusic,
    ) -> AudioSegment:
        """Overlay background music onto dialogue audio.

        The music volume is reduced per pipeline config, and looped if shorter
        than the dialogue track.
        """
        if not Path(music.file_path).exists():
            logger.warning(f"Background music not found: {music.file_path}")
            return dialogue

        bgm = AudioSegment.from_file(music.file_path)

        # Volume adjustment
        volume_db = self._linear_to_db(music.volume)
        bgm = bgm + volume_db

        # Loop if needed
        if music.loop and len(bgm) < len(dialogue):
            repeat_count = (len(dialogue) // len(bgm)) + 1
            bgm = bgm * repeat_count

        # Trim to dialogue length
        bgm = bgm[: len(dialogue)]

        # Mix (overlay)
        return dialogue.overlay(bgm)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _linear_to_db(volume: float) -> float:
        """Convert linear volume (0.0 – 1.0) to dB gain."""
        import math

        if volume <= 0:
            return -60.0
        return 20.0 * math.log10(volume)
