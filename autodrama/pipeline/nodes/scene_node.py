"""Pipeline node: scene planning."""

from __future__ import annotations

from autodrama.config.schema import AppConfig
from autodrama.llm.factory import LLMFactory
from autodrama.models.script import Script
from autodrama.pipeline.state import DramaState
from autodrama.scene.planner import ScenePlanner
from autodrama.utils.logger import logger


class SceneNode:
    """LangGraph node: break the script into shot-by-shot scene plans."""

    def __init__(self, config: AppConfig) -> None:
        llm_config = config.llm.providers[config.llm.default_provider]
        self._llm = LLMFactory.create(config.llm.default_provider, llm_config)
        self._planner = ScenePlanner(self._llm)

    def execute(self, state: DramaState) -> dict:
        try:
            script = Script(**state["script"])  # type: ignore[arg-type]
            plans = self._planner.plan(script)
            logger.info(f"Scene plans created: {len(plans)} scenes")
            return {
                "scene_plans": [p.model_dump() for p in plans],
                "current_stage": "plan_scenes",
                "errors": [],
            }
        except Exception as exc:
            logger.exception("Scene planning failed")
            retry_count = state.get("retry_count", 0)
            return {
                "errors": [
                    {
                        "stage": "plan_scenes",
                        "error": str(exc),
                        "retry_count": retry_count + 1,
                    }
                ],
                "current_stage": "plan_scenes",
                "retry_count": retry_count + 1,
            }
