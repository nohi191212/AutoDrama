"""Subtitle rendering over video clips."""

from __future__ import annotations

from moviepy import TextClip, CompositeVideoClip

from autodrama.config.schema import AppConfig


class SubtitleRenderer:
    """Renders subtitle text onto MoviePy clips."""

    def __init__(self, config: AppConfig) -> None:
        pipe = config.pipeline
        self._font = pipe.subtitle_font
        self._font_size = pipe.subtitle_font_size
        self._color = pipe.subtitle_color
        self._position = pipe.subtitle_position

    def render(self, clip, lines: list[str]) -> CompositeVideoClip:
        """Overlay subtitle text(s) on *clip*.

        Args:
            clip: A MoviePy VideoClip / ImageClip.
            lines: One or more subtitle strings (shown together at the bottom).

        Returns:
            CompositeVideoClip with subtitles baked in.
        """
        if not lines:
            return clip

        text = "\n".join(lines)

        # Determine position
        y_pos: str | tuple[int, int]
        if self._position == "bottom":
            y_pos = ("center", int(clip.size[1] * 0.85))
        elif self._position == "top":
            y_pos = ("center", int(clip.size[1] * 0.10))
        else:
            y_pos = ("center", "center")

        txt_clip = TextClip(
            text=text,
            font=self._font if self._font else "",
            font_size=self._font_size,
            color=self._color,
            stroke_color="black",
            stroke_width=2,
            duration=clip.duration,
        ).with_position(y_pos)

        return CompositeVideoClip([clip, txt_clip])
