from __future__ import annotations

import inspect
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
    MAX_GENERATION_STEPS_PER_CHAPTER = 10
    _STORY_END_MARKERS = frozenset({"（未完待续）", "(未完待续)", "未完待续"})
    _ANCHOR_BOUNDARY_CHARS = frozenset("。！？；，：、“”‘’\"'（）()[]【】")
    _REFERENCE_USAGE_PROMPT = (
        "参考素材使用要求：参考图只用于锁定人物外观、服装、场景、道具造型和静态质感，"
        "不能当作本片段首帧或尾帧；参考视频只用于锁定人物动态气质、动作节奏、镜头运动和动态特效规律，"
        "不能逐帧复刻；参考音频或对白音频只用于锁定角色音色、语气、口型节奏和对白情绪，"
        "声音必须发生在本片段内部，不能提前入场，也不能拖尾到下一片段。"
    )

    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts
        self.last_text_call_count = 0

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _split_dialogue_line(raw_line: str) -> tuple[str | None, str]:
        line = str(raw_line or "").strip()
        if not line:
            return None, ""
        for separator in ("::", "：", ":"):
            if separator in line:
                speaker, text = line.split(separator, 1)
                speaker = speaker.strip()
                text = text.strip()
                if speaker and text:
                    return speaker, text
        return None, line

    @classmethod
    def _missing_dialogue_in_video_prompt(
        cls,
        dialogue: list[str],
        video_prompt: str,
    ) -> list[tuple[str | None, str, str]]:
        prompt = str(video_prompt or "")
        missing: list[tuple[str | None, str, str]] = []
        for raw_line in dialogue:
            raw_text = str(raw_line or "").strip()
            if not raw_text:
                continue
            speaker, text = cls._split_dialogue_line(raw_text)
            if not text:
                continue
            if raw_text in prompt or text in prompt:
                continue
            missing.append((speaker, text, raw_text))
        return missing

    @classmethod
    def _ensure_dialogue_in_video_prompt(
        cls,
        *,
        dialogue: list[str],
        video_prompt: str,
    ) -> str:
        prompt = str(video_prompt or "").strip()
        missing = cls._missing_dialogue_in_video_prompt(dialogue, prompt)
        if not missing:
            return prompt

        dialogue_items: list[str] = []
        for speaker, text, raw_text in missing:
            if speaker:
                dialogue_items.append(
                    f"{speaker}的完整对白必须逐字表演为“{raw_text}”，台词由{speaker}说出"
                )
            else:
                dialogue_items.append(f"完整对白必须逐字表演为“{text}”")
        supplement = (
            "对白完整表演补充："
            + "；".join(dialogue_items)
            + "。每句都要按对应说话时刻给清晰口型、语气强弱、声线状态、神情、呼吸停顿、"
            "身体动作、手部动作、视线方向和听者反应；对白音频只贴合本片段内部口型和情绪，"
            "不提前入场，不拖尾。"
        )
        if not prompt:
            return supplement
        return f"{prompt.rstrip()} {supplement}"

    @classmethod
    def _ensure_reference_usage_in_video_prompt(cls, video_prompt: str) -> str:
        prompt = str(video_prompt or "").strip()
        if all(keyword in prompt for keyword in ("参考图", "参考视频", "参考音频")):
            return prompt
        if not prompt:
            return cls._REFERENCE_USAGE_PROMPT
        return f"{prompt.rstrip()} {cls._REFERENCE_USAGE_PROMPT}"

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
        data["video_prompt"] = StoryboardService._ensure_reference_usage_in_video_prompt(
            StoryboardService._ensure_dialogue_in_video_prompt(
                dialogue=list(data.get("dialogue") or []),
                video_prompt=str(data.get("video_prompt") or ""),
            )
        )
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
    def _compact_text_with_offsets(text: str, *, search_start: int) -> tuple[str, list[int]]:
        compact_chars: list[str] = []
        compact_offsets: list[int] = []
        for index in range(max(0, search_start), len(text)):
            char = text[index]
            if char.isspace():
                continue
            compact_chars.append(char)
            compact_offsets.append(index)
        return "".join(compact_chars), compact_offsets

    @staticmethod
    def _compact_anchor_span(
        compact_text: str,
        compact_offsets: list[int],
        compact_anchor: str,
    ) -> tuple[int, int] | None:
        compact_offset = compact_text.find(compact_anchor)
        if compact_offset < 0:
            return None
        start_offset = compact_offsets[compact_offset]
        end_offset = compact_offsets[compact_offset + len(compact_anchor) - 1] + 1
        return start_offset, end_offset

    @classmethod
    def _previous_anchor_boundary(cls, text: str, offset: int, *, search_start: int) -> int:
        cursor = max(search_start, offset)
        while cursor > search_start:
            previous = text[cursor - 1]
            if previous.isspace() or previous in cls._ANCHOR_BOUNDARY_CHARS:
                break
            cursor -= 1
        return cursor

    @classmethod
    def _leading_variant_anchor_span(
        cls,
        text: str,
        compact_text: str,
        compact_offsets: list[int],
        compact_anchor: str,
        *,
        search_start: int,
    ) -> tuple[int, int] | None:
        if len(compact_anchor) < 24:
            return None

        max_trimmed_prefix = min(8, max(1, len(compact_anchor) // 3))
        for trimmed_prefix in range(1, max_trimmed_prefix + 1):
            suffix_anchor = compact_anchor[trimmed_prefix:]
            if len(suffix_anchor) < 20:
                break

            compact_offset = compact_text.find(suffix_anchor)
            if compact_offset < 0:
                continue
            if compact_text.find(suffix_anchor, compact_offset + 1) >= 0:
                continue

            suffix_start_offset = compact_offsets[compact_offset]
            start_offset = cls._previous_anchor_boundary(text, suffix_start_offset, search_start=search_start)
            if suffix_start_offset - start_offset > trimmed_prefix + 4:
                continue

            end_offset = compact_offsets[compact_offset + len(suffix_anchor) - 1] + 1
            return start_offset, end_offset
        return None

    @classmethod
    def _find_anchor_span(cls, text: str, anchor: str | None, *, search_start: int, label: str) -> tuple[int, int]:
        copied_text = str(anchor or "").strip()
        if not copied_text:
            raise ValueError(f"Storyboard {label} anchor must not be empty")

        search_start = max(0, search_start)
        offset = text.find(copied_text, search_start)
        if offset >= 0:
            return offset, offset + len(copied_text)

        compact_anchor = "".join(char for char in copied_text if not char.isspace())
        if not compact_anchor:
            raise ValueError(f"Storyboard {label} anchor must contain non-whitespace text")

        compact_text, compact_offsets = cls._compact_text_with_offsets(text, search_start=search_start)
        span = cls._compact_anchor_span(compact_text, compact_offsets, compact_anchor)
        if span is not None:
            return span

        span = cls._leading_variant_anchor_span(
            text,
            compact_text,
            compact_offsets,
            compact_anchor,
            search_start=search_start,
        )
        if span is not None:
            return span

        compact_preview = copied_text[:120]
        if compact_anchor != copied_text:
            compact_preview = f"{copied_text[:80]} (whitespace-normalized: {compact_anchor[:80]})"
        raise ValueError(f"Storyboard {label} anchor was not copied from current_novel_full: {compact_preview}")

    @staticmethod
    def _find_anchor_offset(text: str, anchor: str | None, *, search_start: int, label: str) -> int:
        offset, _ = StoryboardService._find_anchor_span(
            text,
            anchor,
            search_start=search_start,
            label=label,
        )
        return offset

    @classmethod
    def _try_find_anchor_span(
        cls,
        text: str,
        anchor: str | None,
        *,
        search_start: int,
        label: str,
    ) -> tuple[int, int] | None:
        try:
            return cls._find_anchor_span(
                text,
                anchor,
                search_start=search_start,
                label=label,
            )
        except ValueError:
            return None

    @classmethod
    def _max_generation_steps(cls, max_shots: int | None) -> int:
        if max_shots is None:
            return cls.MAX_GENERATION_STEPS_PER_CHAPTER
        try:
            steps = int(max_shots)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"storyboard max_shots must be a positive integer; got {max_shots!r}") from exc
        if steps < 1:
            raise ValueError(f"storyboard max_shots must be >= 1; got {steps}")
        return steps

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

        start_offset, _ = self._find_anchor_span(
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

        next_start_text = str(coverage.next_start_text or "").strip()

        if is_chapter_complete:
            _, end_anchor_end_offset = self._find_anchor_span(
                current_novel_full,
                coverage.end_text,
                search_start=start_offset,
                label="source_coverage.end_text",
            )
            if next_start_text:
                raise ValueError("Completed storyboard chapter must return source_coverage.next_start_text=null")
            if current_novel_full[end_anchor_end_offset:source_end_offset].strip():
                raise ValueError("Completed storyboard chapter source_coverage.end_text must reach current_novel_full end")
            return None

        if not next_start_text:
            raise ValueError("Incomplete storyboard chapter must return source_coverage.next_start_text")

        end_span = self._try_find_anchor_span(
            current_novel_full,
            coverage.end_text,
            search_start=start_offset,
            label="source_coverage.end_text",
        )
        next_search_offset = end_span[1] if end_span is not None else start_offset + 1
        next_start_offset, _ = self._find_anchor_span(
            current_novel_full,
            next_start_text,
            search_start=next_search_offset,
            label="source_coverage.next_start_text",
        )
        if next_start_offset <= current_start_offset:
            raise ValueError("Storyboard source_coverage.next_start_text must move forward in current_novel_full")
        if next_start_offset >= source_end_offset:
            raise ValueError("Storyboard source_coverage.next_start_text must point at remaining story text")
        if end_span is not None and end_span[0] >= next_start_offset:
            raise ValueError("Storyboard source_coverage.end_text must not start after source_coverage.next_start_text")
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
        on_shot_generated: Callable[[StoryboardEpisodeOutput, StoryboardShot], Any] | None = None,
        max_shots: int | None = None,
    ) -> StoryboardEpisodeOutput:
        del previous_storyboard_history
        shots: list[StoryboardShot] = []
        self.last_text_call_count = 0
        max_generation_steps = self._max_generation_steps(max_shots)
        current_novel_full = str(current_novel_full or episode_story or "").strip()
        if not current_novel_full:
            raise ValueError(f"storyboard current_novel_full is empty for {episode_key}")
        if novel_extract_all is None:
            novel_extract_all = {episode_key: str(episode_story or current_novel_full).strip()}
        source_end_offset = self._story_text_end_offset(current_novel_full)
        current_shot_start_text, current_start_offset = self._source_anchor(current_novel_full, 0)
        if current_start_offset >= source_end_offset:
            raise ValueError(f"storyboard current_novel_full has no story text for {episode_key}")
        for generation_step in range(1, max_generation_steps + 1):
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
                callback_result = on_shot_generated(StoryboardEpisodeOutput(episode_key=episode_key, shots=list(shots)), shot)
                if inspect.isawaitable(callback_result):
                    await callback_result
            if output.is_chapter_complete:
                break
            current_shot_start_text = str(output.shot.source_coverage.next_start_text or "").strip()
            if next_start_offset is None:
                raise ValueError("Incomplete storyboard chapter did not resolve the next source cursor")
            current_start_offset = next_start_offset

        return StoryboardEpisodeOutput(episode_key=episode_key, shots=shots)
