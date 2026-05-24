from __future__ import annotations

import json
from typing import Any, Callable

from autodrama.core.schemas import (
    ProjectState,
    StoryboardEpisodeOutput,
    StoryboardNextShotOutput,
    StoryboardShot,
    StoryboardShotDraft,
    StoryboardSourceCoverage,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class StoryboardService:
    MAX_GENERATION_STEPS_PER_CHAPTER = 24
    _STORY_END_MARKERS = frozenset({"（未完待续）", "(未完待续)", "未完待续"})

    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts
        self.last_text_call_count = 0

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _generated_shots_context(shots: list[StoryboardShot]) -> dict[str, Any]:
        rendered_shots: list[dict[str, Any]] = []
        for shot in shots:
            data = shot.model_dump(
                mode="json",
                exclude_none=True,
                exclude={
                    "start_frame_source",
                    "start_frame_inheritance_reason",
                    "dialogue_audio_assets",
                    "shot_bgm_assets",
                    "ref_frame_asset_id",
                    "ref_frame_asset_path",
                    "ref_frame_provider",
                    "ref_frame_model",
                    "ref_frame_request_id",
                    "ref_frame_usage",
                    "ref_frame_raw_response",
                    "video_asset_id",
                    "video_asset_path",
                    "video_provider",
                    "video_model",
                    "video_task_id",
                    "video_task_status",
                    "video_request_id",
                    "video_last_frame_asset_path",
                    "video_usage",
                    "video_raw_response",
                    "solidified_asset_ids",
                },
            )
            if ref_frame_prompt := data.pop("ref_frame_prompt", None):
                data["anchor_frame_prompt"] = ref_frame_prompt
            rendered_shots.append(data)
        return {
            "shots": rendered_shots,
        }

    @staticmethod
    def _normalize_generated_shot(
        shot: StoryboardShotDraft,
        *,
        episode_key: str,
        shot_index: int,
    ) -> StoryboardShot:
        data = shot.model_dump(mode="json", exclude_none=True)
        data["shot_id"] = f"{episode_key}_shot_{shot_index:03d}"
        data["index"] = shot_index
        data["ref_frame_prompt"] = data.pop("anchor_frame_prompt")
        try:
            duration = float(data.get("duration_seconds", 6))
        except (TypeError, ValueError):
            duration = 6.0
        data["duration_seconds"] = min(15.0, max(4.0, duration))
        return StoryboardShot.model_validate(data)

    @staticmethod
    def _story_text_start_offset(text: str, start_offset: int = 0) -> int:
        cursor = max(0, start_offset)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1

        line_end = text.find("\n", cursor)
        first_line_end = len(text) if line_end < 0 else line_end
        first_line = text[cursor:first_line_end].strip()
        if first_line.startswith(("源章节：", "源章节:")):
            cursor = first_line_end
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
        return cursor

    @classmethod
    def _story_text_end_offset(cls, text: str) -> int:
        end = len(text.rstrip())
        while end > 0:
            line_start = text.rfind("\n", 0, end) + 1
            last_line = text[line_start:end].strip()
            if last_line not in cls._STORY_END_MARKERS:
                break
            end = len(text[:line_start].rstrip())
        return end

    @staticmethod
    def _source_anchor(text: str, start_offset: int, *, limit: int = 96) -> tuple[str, int]:
        cursor = StoryboardService._story_text_start_offset(text, start_offset)
        if cursor >= len(text):
            raise ValueError("Cannot build storyboard source anchor after the end of current_novel_full")

        end_offset = min(len(text), cursor + limit)
        for index in range(min(len(text), cursor + 24), end_offset):
            if text[index] in "。！？；\n":
                end_offset = index + 1
                break
        anchor = text[cursor:end_offset].strip()
        if not anchor:
            raise ValueError("Cannot build empty storyboard source anchor from current_novel_full")
        return anchor, cursor

    @staticmethod
    def _find_anchor_offset(text: str, anchor: str | None, *, search_start: int, label: str) -> int:
        copied_text = str(anchor or "").strip()
        if not copied_text:
            raise ValueError(f"Storyboard {label} anchor must not be empty")
        offset = text.find(copied_text, max(0, search_start))
        if offset < 0:
            raise ValueError(f"Storyboard {label} anchor was not copied from current_novel_full: {copied_text[:120]}")
        return offset

    def _validate_source_coverage(
        self,
        coverage: StoryboardSourceCoverage,
        *,
        current_novel_full: str,
        current_shot_start_text: str,
        current_start_offset: int,
        is_chapter_complete: bool,
        source_end_offset: int | None = None,
    ) -> int | None:
        if source_end_offset is None:
            source_end_offset = len(current_novel_full.rstrip())

        start_offset = self._find_anchor_offset(
            current_novel_full,
            coverage.start_text,
            search_start=current_start_offset,
            label="source_coverage.start_text",
        )
        if start_offset != current_start_offset:
            raise ValueError(
                "Storyboard source_coverage.start_text must point at the supplied current_shot_start_text boundary "
                f"(expected {current_shot_start_text[:80]!r}, got {coverage.start_text[:80]!r})"
            )

        end_offset = self._find_anchor_offset(
            current_novel_full,
            coverage.end_text,
            search_start=start_offset,
            label="source_coverage.end_text",
        )
        next_search_offset = end_offset + len(coverage.end_text.strip())
        next_start_text = str(coverage.next_start_text or "").strip()

        if is_chapter_complete:
            if next_start_text:
                raise ValueError("Completed storyboard chapter must return source_coverage.next_start_text=null")
            if current_novel_full[next_search_offset:source_end_offset].strip():
                raise ValueError("Completed storyboard chapter source_coverage.end_text must reach current_novel_full end")
            return None

        if not next_start_text:
            raise ValueError("Incomplete storyboard chapter must return source_coverage.next_start_text")
        next_start_offset = self._find_anchor_offset(
            current_novel_full,
            next_start_text,
            search_start=next_search_offset,
            label="source_coverage.next_start_text",
        )
        if next_start_offset <= current_start_offset:
            raise ValueError("Storyboard source_coverage.next_start_text must move forward in current_novel_full")
        if next_start_offset >= source_end_offset:
            raise ValueError("Storyboard source_coverage.next_start_text must point at remaining story text")
        return next_start_offset

    async def storyboard_episode(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        novel_extract_all: dict[str, str] | None = None,
        current_novel_full: str | None = None,
        episode_story: str | None = None,
        previous_storyboard_history: dict[str, Any] | None = None,
        on_shot_generated: Callable[[StoryboardEpisodeOutput, StoryboardShot], None] | None = None,
    ) -> StoryboardEpisodeOutput:
        del previous_storyboard_history
        shots: list[StoryboardShot] = []
        self.last_text_call_count = 0
        current_novel_full = str(current_novel_full or episode_story or "").strip()
        if not current_novel_full:
            raise ValueError(f"storyboard current_novel_full is empty for {episode_key}")
        if novel_extract_all is None:
            novel_extract_all = {episode_key: str(episode_story or current_novel_full).strip()}
        source_end_offset = self._story_text_end_offset(current_novel_full)
        current_shot_start_text, current_start_offset = self._source_anchor(current_novel_full, 0)
        if current_start_offset >= source_end_offset:
            raise ValueError(f"storyboard current_novel_full has no story text for {episode_key}")
        visual_style_prompt = state.metadata.get(
            "visual_style_prompt",
            "真人电影质感：真实摄影、自然光或电影布光、真实材质、真实皮肤纹理和电影镜头语言。",
        )

        for generation_step in range(1, self.MAX_GENERATION_STEPS_PER_CHAPTER + 1):
            prompt = self.prompts.render(
                "storyboard_generate",
                title=state.title,
                episode_key=episode_key,
                novel_extract_all=self.format_json(novel_extract_all),
                current_novel_full=current_novel_full,
                generated_storyboard=self.format_json(self._generated_shots_context(shots)),
                current_shot_start_text=current_shot_start_text,
                roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
                props=self.format_json({prop_id: prop.model_dump(mode="json") for prop_id, prop in state.props.items()}),
                layouts=self.format_json(
                    {layout_id: layout.model_dump(mode="json") for layout_id, layout in state.layouts.items()}
                ),
                visual_style_label=state.metadata.get("visual_style_label", "真人电影质感"),
                visual_style_prompt=visual_style_prompt,
            )
            output = await provider.generate_json(
                prompt,
                StoryboardNextShotOutput,
                temperature=0.6,
                metadata={
                    "node_name": "storyboard_generation",
                    "project_id": state.project_id,
                    "episode_key": episode_key,
                    "generation_step": generation_step,
                    "generated_shot_count": len(shots),
                    "current_shot_start_text": current_shot_start_text,
                    "current_novel_full": current_novel_full,
                },
            )
            self.last_text_call_count += 1
            if output.episode_key != episode_key:
                raise ValueError(f"Storyboard episode_key must be {episode_key}; got {output.episode_key}")

            next_start_offset = self._validate_source_coverage(
                output.shot.source_coverage,
                current_novel_full=current_novel_full,
                current_shot_start_text=current_shot_start_text,
                current_start_offset=current_start_offset,
                is_chapter_complete=output.is_chapter_complete,
                source_end_offset=source_end_offset,
            )
            shot_index = len(shots) + 1
            shot = self._normalize_generated_shot(output.shot, episode_key=episode_key, shot_index=shot_index)
            shots.append(shot)
            if on_shot_generated is not None:
                on_shot_generated(StoryboardEpisodeOutput(episode_key=episode_key, shots=list(shots)), shot)
            if output.is_chapter_complete:
                break
            current_shot_start_text = str(output.shot.source_coverage.next_start_text or "").strip()
            if next_start_offset is None:
                raise ValueError("Incomplete storyboard chapter did not resolve the next source cursor")
            current_start_offset = next_start_offset
        else:
            raise RuntimeError(
                f"Storyboard generation exceeded {self.MAX_GENERATION_STEPS_PER_CHAPTER} steps before "
                f"{episode_key} reached the end of current_novel_full"
            )

        return StoryboardEpisodeOutput(episode_key=episode_key, shots=shots)
