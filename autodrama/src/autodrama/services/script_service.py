from __future__ import annotations

import json
import re

from autodrama.core.schemas import (
    ClipSegmentOutput,
    ProjectState,
    ScriptCinematicAdaptOutput,
    ScriptImportOutput,
    ScriptNovelExtractModelOutput,
    ScriptNovelEpisodeOutput,
    ScriptOutlineOutput,
    ScriptWorldviewExtractOutput,
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
    def sort_episode_keys(keys: list[str]) -> list[str]:
        def sort_key(value: str) -> tuple[int, int, str]:
            match = re.fullmatch(r"episode_(\d+)", value)
            if match:
                return (0, int(match.group(1)), value)
            return (1, 0, value)

        return sorted(dict.fromkeys(keys), key=sort_key)

    @classmethod
    def state_episode_keys(cls, state: ProjectState) -> list[str]:
        for refs in (
            state.script.episode_outlines,
            state.script.novel_full,
            state.script.novel_extract,
        ):
            keys = [
                str(key)
                for key, value in dict(refs or {}).items()
                if str(key).strip() and value
            ]
            if keys:
                return cls.sort_episode_keys(keys)
        return cls.episode_keys(cls.episode_count(state))

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    async def script_import(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        raw_script: str,
    ) -> ScriptImportOutput:
        prompt = self.prompts.render(
            "script_import",
            raw_script=raw_script,
        )
        return await provider.generate_json(
            prompt,
            ScriptImportOutput,
            temperature=0.25,
            metadata={
                "node_name": "script_import",
                "project_id": state.project_id,
                "expected_keys": self.state_episode_keys(state),
            },
        )

    async def script_outline(self, state: ProjectState, provider: TextLLM) -> ScriptOutlineOutput:
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_outline",
            raw_script=state.raw_script,
            episode_duration_seconds=episode_duration_seconds,
        )
        return await provider.generate_json(
            prompt,
            ScriptOutlineOutput,
            temperature=0.7,
            metadata={
                "node_name": "script_outline",
                "project_id": state.project_id,
                "required_mapping_field": "episode_outlines",
            },
        )

    async def script_cinematic_adapt(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        raw_script: str,
    ) -> ScriptCinematicAdaptOutput:
        prompt = self.prompts.render(
            "script_cinematic_adapt",
            raw_script=raw_script,
        )
        return await provider.generate_json(
            prompt,
            ScriptCinematicAdaptOutput,
            temperature=0.35,
            metadata={
                "node_name": "script_cinematic_adapt",
                "project_id": state.project_id,
                "episode_key": episode_key,
            },
        )

    async def script_novel_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        script_novel_full: str,
    ) -> ScriptNovelExtractModelOutput:
        prompt = self.prompts.render(
            "script_novel_extract",
            script_novel_full=script_novel_full,
        )
        return await provider.generate_json(
            prompt,
            ScriptNovelExtractModelOutput,
            temperature=0.5,
            metadata={
                "node_name": "script_novel_extract",
                "project_id": state.project_id,
                "episode_key": episode_key,
            },
        )

    async def script_worldview_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        raw_script: str,
    ) -> ScriptWorldviewExtractOutput:
        prompt = self.prompts.render(
            "script_worldview_extract",
            raw_script=raw_script,
        )
        return await provider.generate_json(
            prompt,
            ScriptWorldviewExtractOutput,
            temperature=0.2,
            metadata={
                "node_name": "script_worldview_extract",
                "project_id": state.project_id,
            },
        )

    async def clip_segment(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        novel_full_this_episode: str,
        novel_extract_all_episodes: str,
        role_index: str,
        prop_index: str,
        scene_index: str,
    ) -> ClipSegmentOutput:
        episode_duration_seconds = self.episode_duration_seconds(state)
        min_clip_seconds = 60
        max_clip_seconds = 120
        duration_reference_note = (
            "Use episode_duration_seconds only to judge text pacing and information density. "
            "Do not mechanically calculate or force a clip count from it."
        )
        prompt = self.prompts.render(
            "clip_segment",
            episode_duration_seconds=episode_duration_seconds,
            min_clip_seconds=min_clip_seconds,
            max_clip_seconds=max_clip_seconds,
            duration_reference_note=duration_reference_note,
            novel_full_this_episode=novel_full_this_episode,
            novel_extract_all_episodes=novel_extract_all_episodes,
            role_index=role_index,
            prop_index=prop_index,
            scene_index=scene_index,
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
                "min_clip_seconds": min_clip_seconds,
                "max_clip_seconds": max_clip_seconds,
                "segment_seconds": max_clip_seconds,
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
        prompt = self.prompts.render(
            "script_novel_episode",
            title=state.title,
            outline=state.script.outline or "",
            episode_outlines=self.format_json(episode_outlines),
            current_episode_outline=current_episode_outline,
            previous_chapters=previous_chapters,
        )
        return await provider.generate_json(
            prompt,
            ScriptNovelEpisodeOutput,
            temperature=0.75,
            metadata={
                "node_name": "script_novel_episode",
                "project_id": state.project_id,
                "episode_key": episode_key,
            },
        )
