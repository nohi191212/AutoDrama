"""Visual effects utilities (ken-burns, color grading stubs)."""

from __future__ import annotations

from moviepy import vfx


class VideoEffects:
    """Collection of common visual effects for still-image clips."""

    @staticmethod
    def ken_burns(clip, zoom_ratio: float = 1.05):
        """Apply a subtle Ken Burns pan-and-zoom effect.

        Args:
            clip: A MoviePy clip.
            zoom_ratio: Final scale relative to original (e.g. 1.05 = 5% zoom).

        Returns:
            Clip with zoom-in effect applied.
        """
        return clip.resized(lambda t: 1 + (zoom_ratio - 1) * (t / clip.duration))

    @staticmethod
    def fade_in(clip, duration: float = 0.5):
        return clip.with_effects([vfx.FadeIn(duration)])

    @staticmethod
    def fade_out(clip, duration: float = 0.5):
        return clip.with_effects([vfx.FadeOut(duration)])

    @staticmethod
    def black_and_white(clip):
        return clip.with_effects([vfx.BlackAndWhite()])
