from __future__ import annotations

from pathlib import Path


class PromptStore:
    def __init__(self, prompt_dir: Path | None = None) -> None:
        self.prompt_dir = prompt_dir or Path(__file__).resolve().parents[1] / "prompts"

    def render(self, name: str, **variables: object) -> str:
        path = self.prompt_dir / f"{name}.md"
        template = path.read_text(encoding="utf-8")
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered
