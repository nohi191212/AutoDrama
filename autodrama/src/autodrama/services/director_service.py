from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from autodrama.core.schemas import DirectorPrepOutput, ProjectState
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class DirectorService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _episode_count(state: ProjectState) -> int:
        return int(state.metadata.get("episode_count", 1))

    @staticmethod
    def _episode_duration_seconds(state: ProjectState) -> int:
        return int(state.metadata.get("episode_duration_seconds", 30))

    @staticmethod
    def _fallback_context() -> str:
        return "（暂无导演前期。请以当前剧本文本、已有资产和节点要求为准。）"

    @classmethod
    def director_prep_context(
        cls,
        state: ProjectState,
        *,
        episode_keys: list[str] | None = None,
    ) -> str:
        payload = state.metadata.get("director_prep")
        if not isinstance(payload, dict) or not payload:
            return cls._fallback_context()
        data = deepcopy(payload)
        if episode_keys:
            selected = {str(key) for key in episode_keys}
            episodes = data.get("episodes")
            if isinstance(episodes, list):
                data["episodes"] = [
                    item
                    for item in episodes
                    if isinstance(item, dict) and str(item.get("episode_key")) in selected
                ]
        return cls.format_json(data)

    async def director_prep(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
    ) -> DirectorPrepOutput:
        episode_keys = list(novel_full)
        prompt = self.prompts.render(
            "director_prep",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            episode_keys=", ".join(episode_keys),
            episode_count=self._episode_count(state),
            episode_duration_seconds=self._episode_duration_seconds(state),
            target_shot_beats=12,
        )
        return await provider.generate_json(
            prompt,
            DirectorPrepOutput,
            temperature=0.45,
            metadata={
                "node_name": "director_prep",
                "project_id": state.project_id,
                "expected_keys": episode_keys,
                "target_shot_beats": 12,
            },
        )


__all__ = ["DirectorService"]
