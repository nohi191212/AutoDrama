"""Pipeline node: video composition."""

from __future__ import annotations

from autodrama.config.schema import AppConfig
from autodrama.models.media import AudioAsset, ImageAsset
from autodrama.models.script import DialogueLine, Script
from autodrama.models.video import VideoSegment
from autodrama.video.composer import VideoComposer
from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class ComposeNode:
    """LangGraph node: assemble the final video from images and audio."""

    def __init__(self, config: AppConfig) -> None:
        self._composer = VideoComposer(config)
        self._config = config

    def execute(self, state: DramaState) -> dict:
        try:
            image_assets = [
                ImageAsset(**a) for a in (state.get("image_assets") or [])
            ]
            audio_assets = [
                AudioAsset(**a) for a in (state.get("audio_assets") or [])
            ]
            script = Script(**state["script"])  # type: ignore[arg-type]

            segments = self._build_segments(script, image_assets, audio_assets)

            output_path = self._composer.compose(segments)

            return {
                "video_segments": [s.model_dump() for s in segments],
                "final_video_path": output_path,
                "current_stage": "compose_video",
                "errors": [],
            }
        except Exception as exc:
            logger.exception("Video composition failed")
            retry_count = state.get("retry_count", 0)
            return {
                "errors": [
                    {
                        "stage": "compose_video",
                        "error": str(exc),
                        "retry_count": retry_count + 1,
                    }
                ],
                "current_stage": "compose_video",
                "retry_count": retry_count + 1,
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_segments(
        script: Script,
        images: list[ImageAsset],
        audio: list[AudioAsset],
    ) -> list[VideoSegment]:
        """Pair images with the matching audio and dialogue text."""
        # Index assets by (scene, shot)
        img_map: dict[tuple[int, int], ImageAsset] = {
            (a.scene_number, a.shot_index): a for a in images
        }
        aud_map: dict[tuple[int, int], AudioAsset] = {
            (a.scene_number, a.shot_index): a for a in audio
        }

        # Walk script in order
        segments: list[VideoSegment] = []
        for scene in script.scenes:
            # Narration shot
            if scene.narration:
                key = (scene.scene_number, 0)
                img = img_map.get(key)
                aud = aud_map.get(key)
                segments.append(
                    VideoSegment(
                        scene_number=scene.scene_number,
                        shot_index=0,
                        image_path=img.image_path if img else "",
                        audio_path=aud.audio_path if aud else "",
                        duration=aud.duration_seconds if aud else 3.0,
                        subtitle_lines=[scene.narration],
                    )
                )

            # Dialogue shots
            shot_idx = 0
            for dline in scene.dialogue:
                key = (scene.scene_number, shot_idx)
                img = img_map.get(key)
                aud = aud_map.get(key)
                segments.append(
                    VideoSegment(
                        scene_number=scene.scene_number,
                        shot_index=shot_idx,
                        image_path=img.image_path if img else "",
                        audio_path=aud.audio_path if aud else "",
                        duration=aud.duration_seconds if aud else dline.timing_hint,
                        subtitle_lines=[f"{dline.character}: {dline.text}"],
                    )
                )
                shot_idx += 1

        return segments
