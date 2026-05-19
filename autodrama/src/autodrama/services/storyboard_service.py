from __future__ import annotations

import json
from typing import Any, Callable

from autodrama.core.schemas import (
    ProjectState,
    StoryboardEpisodeOutput,
    StoryboardShot,
    StoryboardShotGenerationOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class StoryboardService:
    MIN_SHOTS_PER_EPISODE = 3
    MAX_SHOTS_PER_EPISODE = 9

    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts
        self.last_text_call_count = 0

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _episode_duration_seconds(state: ProjectState) -> int:
        value = state.metadata.get("episode_duration_seconds")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 30

    @staticmethod
    def _generated_shots_context(shots: list[StoryboardShot]) -> dict[str, Any]:
        return {
            "shot_count": len(shots),
            "duration_seconds": round(sum(float(shot.duration_seconds or 0) for shot in shots), 2),
            "shots": [
                shot.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude={
                        "dialogue_audio_assets",
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
                for shot in shots
            ],
        }

    @staticmethod
    def _normalize_generated_shot(shot: StoryboardShot, *, episode_key: str, shot_index: int) -> StoryboardShot:
        data = shot.model_dump(mode="json", exclude_none=True)
        data["shot_id"] = f"{episode_key}_shot_{shot_index:03d}"
        data["index"] = shot_index
        try:
            duration = float(data.get("duration_seconds", 6))
        except (TypeError, ValueError):
            duration = 6.0
        data["duration_seconds"] = min(15.0, max(4.0, duration))
        if shot_index == 1:
            data["start_frame_source"] = "new_reference_frame"
            data.setdefault("start_frame_inheritance_reason", "第一片段重新建立本集起始画面，不继承上一片段尾帧。")
        return StoryboardShot.model_validate(data)

    async def storyboard_episode(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
        previous_storyboard_history: dict[str, Any] | None = None,
        on_shot_generated: Callable[[StoryboardEpisodeOutput, StoryboardShot], None] | None = None,
    ) -> StoryboardEpisodeOutput:
        shots: list[StoryboardShot] = []
        self.last_text_call_count = 0
        target_duration_seconds = self._episode_duration_seconds(state)
        visual_style_prompt = state.metadata.get(
            "visual_style_prompt",
            "真人电影质感：真实摄影、自然光或电影布光、真实材质、真实皮肤纹理和电影镜头语言。",
        )

        for shot_index in range(1, self.MAX_SHOTS_PER_EPISODE + 1):
            prompt = self.prompts.render(
                "storyboard_generate",
                title=state.title,
                episode_key=episode_key,
                episode_script=state.script.final_script.get(episode_key, ""),
                previous_storyboard_history=self.format_json(previous_storyboard_history or {"episodes": []}),
                generated_shots=self.format_json(self._generated_shots_context(shots)),
                shot_index=shot_index,
                min_shots=self.MIN_SHOTS_PER_EPISODE,
                max_shots=self.MAX_SHOTS_PER_EPISODE,
                current_duration_seconds=round(sum(float(shot.duration_seconds or 0) for shot in shots), 2),
                target_duration_seconds=target_duration_seconds,
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
                StoryboardShotGenerationOutput,
                temperature=0.6,
                metadata={
                    "node_name": "storyboard_generation",
                    "project_id": state.project_id,
                    "episode_key": episode_key,
                    "shot_index": shot_index,
                    "generated_shot_count": len(shots),
                    "min_shots": self.MIN_SHOTS_PER_EPISODE,
                    "max_shots": self.MAX_SHOTS_PER_EPISODE,
                    "target_duration_seconds": target_duration_seconds,
                },
            )
            self.last_text_call_count += 1
            if output.episode_key != episode_key:
                raise ValueError(f"Storyboard episode_key must be {episode_key}; got {output.episode_key}")

            shot = self._normalize_generated_shot(output.shot, episode_key=episode_key, shot_index=shot_index)
            shots.append(shot)
            if on_shot_generated is not None:
                on_shot_generated(StoryboardEpisodeOutput(episode_key=episode_key, shots=list(shots)), shot)
            if output.is_episode_complete and len(shots) >= self.MIN_SHOTS_PER_EPISODE:
                break

        return StoryboardEpisodeOutput(episode_key=episode_key, shots=shots)
