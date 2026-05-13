"""Script generation — convert a concept into a structured Script."""

from __future__ import annotations

import json

from autodrama.llm.base import LLMProvider
from autodrama.models.script import Script
from autodrama.script.templates import SCRIBE_SYSTEM_PROMPT, GENRE_PROMPTS
from autodrama.script.validators import validate_script_structure


class ScriptGenerator:
    """Generate a complete, structured drama script from a concept."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def generate(self, concept: str, params: dict | None = None) -> Script:
        """Generate a Script from a high-level concept.

        Args:
            concept: Text description of the desired drama (e.g. "A detective
                     finds a mysterious letter on a rainy night").
            params: Optional overrides — ``genre``, ``style``, ``num_scenes``.

        Returns:
            A validated ``Script`` instance.
        """
        params = params or {}

        system_prompt = self._build_system_prompt(params)
        user_prompt = self._build_user_prompt(concept, params)
        output_schema = Script.model_json_schema()

        response = self._llm.chat_with_structured_output(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            output_schema=output_schema,
            temperature=0.7,
        )

        data = json.loads(response.content)
        script = Script(**data)
        validate_script_structure(script)
        return script

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(params: dict) -> str:
        genre = params.get("genre", "")
        genre_note = GENRE_PROMPTS.get(genre, "") if genre else ""
        return SCRIBE_SYSTEM_PROMPT + ("\n\n" + genre_note if genre_note else "")

    @staticmethod
    def _build_user_prompt(concept: str, params: dict) -> str:
        parts = [f"Write a short drama script based on this concept: {concept}"]

        genre = params.get("genre")
        if genre:
            parts.append(f"Genre: {genre}")

        style = params.get("style")
        if style:
            parts.append(f"Visual style: {style}")

        num_scenes = params.get("num_scenes")
        if num_scenes:
            parts.append(f"Use approximately {num_scenes} scenes.")

        # Grounding instruction for structured output
        parts.append("\nReturn a single JSON object conforming to the provided schema.")
        return "\n".join(parts)
