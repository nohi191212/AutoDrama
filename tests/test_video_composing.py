"""Tests for video composition module."""

from autodrama.config.loader import ConfigLoader
from autodrama.models.video import VideoSegment
from autodrama.video.composer import VideoComposer


def test_compose_creates_video_file(test_config_path: str, tiny_image_path: str, silent_audio_path: str, tmp_path) -> None:
    """End-to-end compose test with real (tiny) media files.

    IMPORTANT: This test requires ffmpeg to be installed (conda/moviepy dependency).
    """
    import os
    cfg = ConfigLoader.load(test_config_path)
    cfg.project.output_dir = str(tmp_path / "output")

    composer = VideoComposer(cfg)

    segments = [
        VideoSegment(
            scene_number=1,
            shot_index=0,
            image_path=tiny_image_path,
            audio_path=silent_audio_path,
            duration=1.0,
            transition_in="fade",
            transition_out="fade",
            subtitle_lines=["Alice: Run!"],
        ),
    ]

    output_path = composer.compose(segments)
    assert output_path.endswith(".mp4")
    assert os.path.exists(output_path)
