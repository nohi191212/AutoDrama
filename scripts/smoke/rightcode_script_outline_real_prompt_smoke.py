from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.core.schemas import BudgetState, ProjectState, ScriptBundle, ScriptOutlineOutput
from autodrama.providers.router import ProviderRouter
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore


def build_state(config_path: Path, *, project_id: str | None) -> ProjectState:
    settings = load_settings(config_path)
    if not settings.project.script_outline_file:
        raise ValueError("project.script_outline_file is not configured")
    raw_script = settings.project.script_outline_file.read_text(encoding="utf-8")
    resolved_project_id = project_id or settings.project.id or "rightcode_script_outline_real_prompt_smoke"
    return ProjectState(
        project_id=resolved_project_id,
        title=settings.project.title or resolved_project_id,
        raw_script=raw_script,
        script=ScriptBundle(raw_script=raw_script),
        budget=BudgetState.model_validate(settings.budget.model_dump()),
        metadata={
            "episode_count": settings.project.episode_count,
            "episode_duration_seconds": settings.project.episode_duration_seconds,
        },
    )


def summarize(output: ScriptOutlineOutput) -> dict[str, object]:
    episode_keys = sorted(output.episode_outlines)
    return {
        "outline_chars": len(output.outline.strip()),
        "episode_count": len(episode_keys),
        "episode_keys": episode_keys,
        "episode_outline_chars": {
            key: len(str(output.episode_outlines[key]).strip())
            for key in episode_keys
        },
    }


async def run_smoke(config_path: Path, *, project_id: str | None, output_path: Path) -> None:
    settings = load_settings(config_path)
    state = build_state(config_path, project_id=project_id)
    router = ProviderRouter(settings)
    provider = router.text("script", node_name="script_outline")
    target = getattr(provider, "_provider", provider)
    print(
        "provider="
        f"{getattr(provider, 'name', 'unknown')} model={getattr(provider, 'model', '-')}"
        f" stream={getattr(target, 'stream', '-')}"
        f" endpoint={getattr(target, 'endpoint', '-')}"
    )

    output = await ScriptService(PromptStore()).script_outline(state, provider)
    if not output.outline.strip():
        raise AssertionError("script_outline outline is empty")
    if not output.episode_outlines:
        raise AssertionError("script_outline episode_outlines is empty")

    result = {
        "summary": summarize(output),
        "output": output.model_dump(mode="json"),
    }
    resolved_output_path = (ROOT / output_path).resolve()
    resolved_output_path.parent.mkdir(exist_ok=True)
    resolved_output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"rightcode_script_outline_real_prompt_smoke: ok output={resolved_output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="huyao.yaml")
    parser.add_argument("--project", default=None)
    parser.add_argument("--output", default=".tmp/rightcode_script_outline_real_prompt_smoke.json")
    args = parser.parse_args()
    asyncio.run(
        run_smoke(
            Path(args.config),
            project_id=args.project,
            output_path=Path(args.output),
        )
    )


if __name__ == "__main__":
    main()
