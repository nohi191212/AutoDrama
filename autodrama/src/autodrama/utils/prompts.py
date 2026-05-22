from __future__ import annotations

import re
from pathlib import Path

from autodrama.logging import get_logger

_VARIABLE_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


class PromptStore:
    def __init__(self, prompt_dir: Path | None = None, *, strict: bool = True) -> None:
        self.prompt_dir = prompt_dir or Path(__file__).resolve().parents[1] / "prompts"
        self.strict = strict

    def render(self, name: str, **variables: object) -> str:
        path = self.prompt_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        template = path.read_text(encoding="utf-8")
        expected_variables = set(_VARIABLE_PATTERN.findall(template))
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        if self.strict:
            missing = sorted(set(_VARIABLE_PATTERN.findall(rendered)))
            if missing:
                raise ValueError(
                    f"Prompt template {name!r} has unresolved variables: {', '.join(missing)}"
                )
            unknown = sorted(set(variables).difference(expected_variables))
            if unknown:
                get_logger().debug(
                    "Prompt template %s received unused variables: %s",
                    name,
                    ", ".join(unknown),
                )
        return rendered
