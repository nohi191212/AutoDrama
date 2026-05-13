"""Pipeline node: script generation."""

from __future__ import annotations

from autodrama.config.schema import AppConfig
from autodrama.llm.factory import LLMFactory
from autodrama.pipeline.state import DramaState
from autodrama.script.generator import ScriptGenerator
from autodrama.utils.logger import logger


class ScriptNode:
    """LangGraph node: validate input + generate script."""

    def __init__(self, config: AppConfig) -> None:
        llm_config = config.llm.providers[config.llm.default_provider]
        self._llm = LLMFactory.create(config.llm.default_provider, llm_config)
        self._generator = ScriptGenerator(self._llm)
        self._max_retries = config.pipeline.max_retries

    def validate_input(self, state: DramaState) -> dict:
        concept = state.get("concept", "").strip()
        if not concept:
            max_retries = state.get("max_retries", self._max_retries)
            return {
                "errors": [
                    {
                        "stage": "validate_input",
                        "error": "Empty concept",
                        "retry_count": max_retries,  # non-recoverable — abort immediately
                    }
                ],
                "current_stage": "validate_input",
                "retry_count": max_retries,
                "max_retries": max_retries,
            }
        return {
            "current_stage": "validate_input",
            "max_retries": state.get("max_retries", self._max_retries),
        }

    def execute(self, state: DramaState) -> dict:
        try:
            script = self._generator.generate(
                concept=state["concept"],
                params=state.get("input_params", {}),
            )
            logger.info(f"Script generated: '{script.title}' ({len(script.scenes)} scenes)")
            return {
                "script": script.model_dump(),
                "current_stage": "generate_script",
                "errors": [],
            }
        except Exception as exc:
            logger.exception("Script generation failed")
            retry_count = state.get("retry_count", 0)
            return {
                "errors": [
                    {
                        "stage": "generate_script",
                        "error": str(exc),
                        "retry_count": retry_count + 1,
                    }
                ],
                "current_stage": "generate_script",
                "retry_count": retry_count + 1,
            }
