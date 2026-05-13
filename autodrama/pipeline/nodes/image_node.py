"""Pipeline node: image generation."""

from __future__ import annotations

from autodrama.config.schema import AppConfig
from autodrama.media.image.factory import ImageFactory
from autodrama.models.media import ImageAsset
from autodrama.models.scene_plan import ScenePlan
from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class ImageNode:
    """LangGraph node: generate images for all shots across all scenes."""

    def __init__(self, config: AppConfig) -> None:
        image_cfg = config.media.image
        provider_cfg = image_cfg.providers[image_cfg.default_provider]
        self._generator = ImageFactory.create(
            image_cfg.default_provider, provider_cfg,
            save_dir=config.project.output_dir + "/images",
        )
        self._resolution = config.pipeline.resolution

    def execute(self, state: DramaState) -> dict:
        try:
            plans = [ScenePlan(**p) for p in (state["scene_plans"] or [])]
            assets: list[ImageAsset] = []

            # Collect all prompts
            prompts: list[str] = []
            for plan in plans:
                for shot in plan.shots:
                    prompts.append(shot.image_prompt or shot.subject_focus)

            if prompts:
                logger.info(f"Generating {len(prompts)} images …")
                # Tag scene/shot info on each generated asset
                idx = 0
                for plan in plans:
                    for shot in plan.shots:
                        prompt = shot.image_prompt or shot.subject_focus
                        try:
                            asset = self._generator.generate(
                                prompt,
                                size=self._resolution,
                            )
                            asset.scene_number = plan.scene_number
                            asset.shot_index = shot.shot_index
                            assets.append(asset)
                        except Exception as exc:
                            logger.error(
                                f"Image generation failed for scene {plan.scene_number} "
                                f"shot {shot.shot_index}: {exc}"
                            )
                            raise
                        idx += 1

            return {
                "image_assets": [a.model_dump() for a in assets],
                "current_stage": "generate_images",
                "errors": [],
            }
        except Exception as exc:
            logger.exception("Image generation failed")
            retry_count = state.get("retry_count", 0)
            return {
                "errors": [
                    {
                        "stage": "generate_images",
                        "error": str(exc),
                        "retry_count": retry_count + 1,
                    }
                ],
                "current_stage": "generate_images",
                "retry_count": retry_count + 1,
            }
