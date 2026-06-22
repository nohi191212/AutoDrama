from __future__ import annotations

import json

from autodrama.core.schemas import (
    ClipSegmentOutput,
    ScriptDetailExpandOutput,
    ProjectState,
    ScriptNovelExtractBatchOutput,
    ScriptNovelEpisodeOutput,
    ScriptOutlineOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class ScriptService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def episode_count(state: ProjectState) -> int:
        return int(state.metadata.get("episode_count", 1))

    @staticmethod
    def episode_duration_seconds(state: ProjectState) -> int:
        return int(state.metadata.get("episode_duration_seconds", 30))

    @classmethod
    def episode_target_char_count(cls, state: ProjectState) -> int:
        return cls.episode_duration_seconds(state) * 60

    @staticmethod
    def episode_keys(episode_count: int) -> list[str]:
        return [f"episode_{index:03d}" for index in range(1, episode_count + 1)]

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    async def script_outline(self, state: ProjectState, provider: TextLLM) -> ScriptOutlineOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_outline",
            title=state.title,
            raw_script=state.raw_script,
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
            episode_keys=", ".join(self.episode_keys(episode_count)),
        )
        return await provider.generate_json(
            prompt,
            ScriptOutlineOutput,
            temperature=0.7,
            metadata={
                "node_name": "script_outline",
                "project_id": state.project_id,
                "required_mapping_field": "episode_outlines",
                "expected_keys": self.episode_keys(episode_count),
            },
        )

    async def script_detail_expand(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        raw_script: str,
        max_expand_ratio: float = 1.35,
    ) -> ScriptDetailExpandOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_detail_expand",
            title=state.title,
            episode_key=episode_key,
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
            max_expand_ratio=max_expand_ratio,
            raw_script=raw_script,
        )
        return await provider.generate_json(
            prompt,
            ScriptDetailExpandOutput,
            temperature=0.35,
            metadata={
                "node_name": "script_detail_expand",
                "project_id": state.project_id,
                "episode_key": episode_key,
                "max_expand_ratio": max_expand_ratio,
            },
        )

    async def script_novel_extract_batch(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        batch_episode_keys: list[str],
        novel_full: dict[str, str],
        previous_extract: dict[str, str],
        extract_hints: dict[str, str],
        director_prep: str | None = None,
    ) -> ScriptNovelExtractBatchOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_novel_extract",
            title=state.title,
            novel_full=self.format_json(novel_full),
            previous_extract=self.format_json(previous_extract) if previous_extract else "（暂无，当前是第一批。）",
            extract_hints=self.format_json(extract_hints),
            director_prep=director_prep or "（暂无导演前期。）",
            batch_episode_keys=", ".join(batch_episode_keys),
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
        )
        return await provider.generate_json(
            prompt,
            ScriptNovelExtractBatchOutput,
            temperature=0.5,
            metadata={
                "node_name": "script_novel_extract",
                "project_id": state.project_id,
                "required_mapping_field": "novel_extract",
                "expected_keys": batch_episode_keys,
                "batch_episode_keys": batch_episode_keys,
            },
        )

    async def clip_segment(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        novel_full: str,
        novel_extract: str,
        director_prep: str | None = None,
    ) -> ClipSegmentOutput:
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "clip_segment",
            title=state.title,
            raw_script=state.raw_script,
            episode_key=episode_key,
            novel_full=novel_full,
            novel_extract=novel_extract,
            director_prep=director_prep or "（暂无导演前期。）",
            episode_duration_seconds=episode_duration_seconds,
        )
        print(
            "\n".join(
                [
                    "",
                    "=" * 100,
                    "CLIP_SEGMENT INPUT PROMPT",
                    "-" * 100,
                    prompt,
                    "=" * 100,
                ]
            ),
            flush=True,
        )
        return await provider.generate_json(
            prompt,
            ClipSegmentOutput,
            temperature=0.35,
            metadata={
                "node_name": "clip_segment",
                "project_id": state.project_id,
                "episode_key": episode_key,
                "episode_duration_seconds": episode_duration_seconds,
                "segment_seconds": 15,
            },
        )

    async def script_novel_episode(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        episode_outlines: dict[str, str],
        current_episode_outline: str,
        previous_chapters: str,
    ) -> ScriptNovelEpisodeOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        target_char_count = self.episode_target_char_count(state)
        prompt = self.prompts.render(
            "script_novel_episode",
            title=state.title,
            outline=state.script.outline or "",
            episode_outlines=self.format_json(episode_outlines),
            current_episode_key=episode_key,
            current_episode_outline=current_episode_outline,
            previous_chapters=previous_chapters,
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
            target_char_count=target_char_count,
            episode_keys=", ".join(self.episode_keys(episode_count)),
        )
        return await provider.generate_json(
            prompt,
            ScriptNovelEpisodeOutput,
            temperature=0.75,
            metadata={
                "node_name": "script_novel_episode",
                "project_id": state.project_id,
                "episode_key": episode_key,
                "target_char_count": target_char_count,
            },
        )
