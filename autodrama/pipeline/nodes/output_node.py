"""Pipeline node: final output — write metadata and clean up."""

from __future__ import annotations

import json
from pathlib import Path

from autodrama.config.schema import AppConfig
from autodrama.pipeline.state import DramaState
from autodrama.utils.file_utils import clean_temp
from autodrama.utils.logger import logger


class OutputNode:
    """LangGraph node: finalise output, write metadata, optional cleanup."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config

    def execute(self, state: DramaState) -> dict:
        output_path = state.get("final_video_path")
        if not output_path:
            logger.warning("No video path in state; nothing to output")
            return {}

        # Write metadata JSON alongside the video
        meta_path = Path(output_path).with_suffix(".meta.json")
        meta = {
            "concept": state.get("concept"),
            "script": state.get("script"),
            "final_video_path": output_path,
            "warnings": state.get("warnings", []),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Metadata saved to {meta_path}")

        # Clean temporary files (only when config is available)
        if self._config and self._config.pipeline.cleanup_temp:
            temp_dir = self._config.project.temp_dir
            clean_temp(temp_dir)
            logger.info("Temp files cleaned up")

        logger.info(f"Pipeline complete! Video: {output_path}")
        return {"current_stage": "final_output"}
