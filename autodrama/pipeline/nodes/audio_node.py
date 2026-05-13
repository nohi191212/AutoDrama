"""Pipeline node: audio / TTS generation."""

from __future__ import annotations

from autodrama.config.schema import AppConfig
from autodrama.media.audio.factory import TTSFactory
from autodrama.models.media import AudioAsset
from autodrama.models.script import Script
from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class AudioNode:
    """LangGraph node: TTS for all dialogue lines."""

    def __init__(self, config: AppConfig) -> None:
        audio_cfg = config.media.audio
        provider_cfg = audio_cfg.providers[audio_cfg.default_provider]
        self._generator = TTSFactory.create(
            audio_cfg.default_provider, provider_cfg,
            save_dir=config.project.output_dir + "/audio",
        )

    def execute(self, state: DramaState) -> dict:
        try:
            script = Script(**state["script"])  # type: ignore[arg-type]
            assets: list[AudioAsset] = []

            lines_to_synthesise: list[dict] = []
            scene_indices: list[int] = []
            shot_indices: list[int] = []

            shot_idx = 0
            for scene in script.scenes:
                # Narration
                if scene.narration:
                    lines_to_synthesise.append({"text": scene.narration, "character": "narrator"})
                    scene_indices.append(scene.scene_number)
                    shot_indices.append(0)

                # Dialogue
                for dline in scene.dialogue:
                    lines_to_synthesise.append(
                        {"text": dline.text, "character": dline.character, "emotion": dline.emotion}
                    )
                    scene_indices.append(scene.scene_number)
                    shot_indices.append(shot_idx)
                    shot_idx += 1

            if lines_to_synthesise:
                logger.info(f"Synthesising {len(lines_to_synthesise)} audio lines …")
                raw_assets = self._generator.synthesize_batch(lines_to_synthesise)

                for asset, sc, sh in zip(raw_assets, scene_indices, shot_indices):
                    asset.scene_number = sc
                    asset.shot_index = sh
                    assets.append(asset)

            return {
                "audio_assets": [a.model_dump() for a in assets],
                "current_stage": "generate_audio",
                "errors": [],
            }
        except Exception as exc:
            logger.exception("Audio generation failed")
            retry_count = state.get("retry_count", 0)
            return {
                "errors": [
                    {
                        "stage": "generate_audio",
                        "error": str(exc),
                        "retry_count": retry_count + 1,
                    }
                ],
                "current_stage": "generate_audio",
                "retry_count": retry_count + 1,
            }
