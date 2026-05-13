"""Transition effects between video clips (crossfade, fade-in/out)."""

from __future__ import annotations

from moviepy import CompositeVideoClip, vfx


class TransitionFactory:
    """Apply transitions to a sequence of clips."""

    def apply_sequence(
        self,
        clips: list,
        duration: float = 0.5,
        default_type: str = "crossfade",
    ) -> list:
        """Apply transitions between consecutive clips.

        Args:
            clips: List of MoviePy VideoClip / ImageClip objects.
            duration: Transition duration in seconds.
            default_type: Default transition type (crossfade, fade).

        Returns:
            List of clips with transitions baked in (crossfade produces
            overlapping composite clips).
        """
        if len(clips) <= 1:
            return clips

        result = []
        for i, clip in enumerate(clips):
            # Fade-in on first clip
            if i == 0:
                clip = clip.with_effects([vfx.FadeIn(duration)])
                result.append(clip)
                continue

            prev = clips[i - 1]

            if default_type == "crossfade":
                # Crossfade: overlap prev and current by *duration*
                prev_end = prev.duration
                overlap_start = max(0, prev_end - duration)

                # Trim previous clip to overlap region
                prev_overlap = prev.subclipped(overlap_start, prev_end)
                prev_overlap = prev_overlap.with_effects([vfx.FadeOut(duration)])

                # Current clip starts at overlap with fade-in
                curr_fade = clip.with_effects([vfx.FadeIn(duration)])

                # Composite the overlap
                composite = CompositeVideoClip(
                    [prev_overlap, curr_fade.with_start(overlap_start)],
                    size=clip.size,
                )

                # Replace the last result entry with the composite
                result[-1] = composite
                result.append(curr_fade.with_start(prev_end))
            else:
                # Simple fade
                clip = clip.with_effects([vfx.FadeIn(duration)])
                result.append(clip)

        return result
