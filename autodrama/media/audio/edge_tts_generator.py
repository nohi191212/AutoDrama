"""Microsoft Edge TTS provider — free, local, high-quality Chinese voices."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from autodrama.config.schema import TTSProviderConfig
from autodrama.media.audio.base import BaseTTSGenerator
from autodrama.models.media import AudioAsset


class EdgeTTSGenerator(BaseTTSGenerator):
    """TTS generator using Microsoft Edge TTS (edge-tts)."""

    def __init__(self, config: TTSProviderConfig, save_dir: str | Path = "./output/audio") -> None:
        self._voice = config.voice
        self._rate = config.rate
        self._volume = config.volume
        self._save_dir = Path(save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def synthesize(
        self,
        text: str,
        voice: str = "",
        **kwargs,
    ) -> AudioAsset:
        voice = voice or self._voice
        output_path = self._save_dir / f"tts_{int(time.time() * 1_000_000)}.mp3"

        coro = self._synthesize_async(text, voice, str(output_path))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # Running in async context — schedule on the loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                future.result()
        else:
            loop.run_until_complete(coro)

        duration = self._estimate_duration(text)
        return AudioAsset(
            scene_number=0,
            shot_index=0,
            text=text,
            audio_path=str(output_path),
            duration_seconds=duration,
            provider="edge_tts",
        )

    def synthesize_batch(
        self,
        lines: list[dict],
        **kwargs,
    ) -> list[AudioAsset]:
        assets: list[AudioAsset] = []
        for i, line in enumerate(lines):
            text = line.get("text", "")
            voice = line.get("voice", self._voice)
            asset = self.synthesize(text, voice=voice, **kwargs)
            asset.shot_index = i
            assets.append(asset)
        return assets

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _synthesize_async(self, text: str, voice: str, output_path: str) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=self._rate,
            volume=self._volume,
        )
        await communicate.save(output_path)
        logger.debug(f"TTS saved to {output_path}")

    @staticmethod
    def _estimate_duration(text: str) -> float:
        """Rough estimate: ~4 chars per second for Chinese; ~12 for English."""
        # Assume mixed content; use ~5 chars/sec as a safe estimate
        return max(1.0, len(text) / 5.0)
