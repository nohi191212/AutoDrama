"""Scene planning — break a Script into detailed shot compositions."""

from __future__ import annotations

import json

from autodrama.llm.base import LLMProvider
from autodrama.models.script import Scene, Script
from autodrama.models.scene_plan import ScenePlan
from autodrama.scene.prompt_builder import build_image_prompt

SCENE_PLANNER_SYSTEM = """You are a cinematography director planning shots for a short drama video.
For each scene, create a shot-by-shot plan that includes:

- Camera angle (eye-level, high-angle, low-angle, dutch, bird-eye, worm-eye)
- Frame type (close-up, medium, long, wide, extreme-wide)
- Subject focus description
- Background description
- Lighting style
- A detailed image-generation prompt in English (for AI image generators)
- Negative prompt (what to avoid)

Each dialogue line gets its own shot. Narration gets an establishing shot.
Return a JSON array of scene plans matching the provided schema."""


class ScenePlanner:
    """Plan cinematography for each scene."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def plan(self, script: Script) -> list[ScenePlan]:
        """Generate a ScenePlan for every script scene.

        Uses the LLM with structured output to produce detailed shot
        compositions including image-generation prompts.
        """
        # ----- Build the LLM message -----
        scene_summaries = self._describe_scenes(script)

        from autodrama.models.scene_plan import ScenePlan

        # Request one ScenePlan per scene
        output_schema = {
            "type": "array",
            "items": ScenePlan.model_json_schema(),
        }

        response = self._llm.chat_with_structured_output(
            messages=[
                {"role": "system", "content": SCENE_PLANNER_SYSTEM},
                {"role": "user", "content": scene_summaries},
            ],
            output_schema=output_schema,
            temperature=0.5,
        )

        plans_data = json.loads(response.content)
        plans = [ScenePlan(**item) for item in plans_data]

        # ----- Post-process: build polished image prompts -----
        for plan in plans:
            for shot in plan.shots:
                if not shot.image_prompt:
                    shot.image_prompt = build_image_prompt(
                        scene_description=self._get_scene_desc(script, plan.scene_number),
                        shot=shot,
                        style=script.style,
                    )

        return plans

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _describe_scenes(script: Script) -> str:
        lines: list[str] = [
            f"Title: {script.title}  |  Genre: {script.genre}  |  Style: {script.style}\n"
        ]
        for s in script.scenes:
            lines.append(f"--- Scene {s.scene_number}: {s.location} ({s.time_of_day}) ---")
            lines.append(f"Atmosphere: {s.atmosphere}")
            lines.append(f"Description: {s.description}")
            if s.narration:
                lines.append(f"Narration: {s.narration}")
            if s.dialogue:
                lines.append("Dialogue:")
                for d in s.dialogue:
                    lines.append(
                        f"  [{d.character}] ({d.emotion}) [{d.shot_type}]: {d.text}"
                    )
            if s.image_keywords:
                lines.append(f"Image keywords: {', '.join(s.image_keywords)}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _get_scene_desc(script: Script, scene_number: int) -> str:
        for s in script.scenes:
            if s.scene_number == scene_number:
                return s.description
        return ""
