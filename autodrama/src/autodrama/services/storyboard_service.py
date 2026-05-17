from __future__ import annotations

import json

from autodrama.core.schemas import ProjectState, StoryboardEpisodeOutput
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class StoryboardService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    async def storyboard_episode(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_key: str,
    ) -> StoryboardEpisodeOutput:
        prompt = self.prompts.render(
            "storyboard_generate",
            title=state.title,
            episode_key=episode_key,
            episode_script=state.script.final_script.get(episode_key, ""),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            props=self.format_json({prop_id: prop.model_dump(mode="json") for prop_id, prop in state.props.items()}),
            layouts=self.format_json({layout_id: layout.model_dump(mode="json") for layout_id, layout in state.layouts.items()}),
            bgms=self.format_json({bgm_id: bgm.model_dump(mode="json") for bgm_id, bgm in state.bgms.items()}),
            visual_style_label=state.metadata.get("visual_style_label", "真人电影质感"),
            visual_style_prompt=state.metadata.get(
                "visual_style_prompt",
                "真人电影质感：真实摄影、自然光或电影布光、真实材质、真实皮肤纹理和电影镜头语言。",
            ),
        )
        return await provider.generate_json(
            prompt,
            StoryboardEpisodeOutput,
            temperature=0.6,
            metadata={
                "node_name": "storyboard_generation",
                "project_id": state.project_id,
                "episode_key": episode_key,
            },
        )
