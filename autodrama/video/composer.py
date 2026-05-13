"""VideoComposer — assemble images + audio + subtitles into a final video."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from moviepy import ImageClip, concatenate_videoclips
from moviepy.audio.io.AudioFileClip import AudioFileClip

from autodrama.config.schema import AppConfig
from autodrama.models.video import VideoSegment
from autodrama.video.subtitles import SubtitleRenderer
from autodrama.video.transitions import TransitionFactory


class VideoComposer:
    """Compose the final short-drama video from image and audio assets."""

    def __init__(self, config: AppConfig) -> None:
        self._cfg = config
        self._subtitle = SubtitleRenderer(config)
        self._transition = TransitionFactory()

    def compose(self, segments: list[VideoSegment]) -> str:
        """Compose all segments into one MP4 file.

        Returns:
            Absolute path to the rendered video file.
        """
        if not segments:
            raise ValueError("No segments to compose")

        clips = []
        for seg in segments:
            clip = self._build_segment(seg)
            clips.append(clip)

        # Apply transitions between adjacent clips
        transition_dur = self._cfg.pipeline.transition_duration
        if len(clips) > 1 and transition_dur > 0:
            processed = self._transition.apply_sequence(clips, transition_dur)
        else:
            processed = clips

        # Concatenate
        final_clip = concatenate_videoclips(processed, method="compose")

        # Write output
        output_dir = Path(self._cfg.project.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "autodrama_output.mp4"

        logger.info(f"Rendering video to {output_path} …")
        final_clip.write_videofile(
            str(output_path),
            fps=self._cfg.pipeline.fps,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            logger=None,
        )
        logger.info(f"Video saved: {output_path}")

        # Cleanup
        for clip in processed:
            clip.close()
        final_clip.close()

        return str(output_path.resolve())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_segment(self, seg: VideoSegment):
        """Create a MoviePy clip for a single segment."""
        # Image
        img_clip = ImageClip(seg.image_path, duration=seg.duration)
        img_clip = img_clip.resized(new_size=self._cfg.pipeline.resolution)

        # Audio
        if seg.audio_path and Path(seg.audio_path).exists():
            try:
                audio = AudioFileClip(seg.audio_path)
                img_clip = img_clip.with_audio(audio)
            except Exception:
                logger.warning(f"Failed to load audio: {seg.audio_path}")

        # Subtitles
        if self._cfg.pipeline.subtitle_enabled and seg.subtitle_lines:
            img_clip = self._subtitle.render(img_clip, seg.subtitle_lines)

        return img_clip
