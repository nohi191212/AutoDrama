from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import build_parser  # noqa: E402
from autodrama.config import ProviderSettings, Settings, load_settings  # noqa: E402
from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings()
    settings.providers = {
        "deepseek": ProviderSettings(
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            models={
                "text": "deepseek-chat",
                "storyboard": "deepseek-v4-pro",
            },
            options={
                "reasoning_effort": "low",
                "thinking_enabled": False,
                "storyboard_reasoning_effort": "max",
                "storyboard_thinking_enabled": True,
            },
        )
    }
    settings.routing = {
        "text": {
            "storyboard": "deepseek",
            "edit_plan": "deepseek",
        }
    }

    router = ProviderRouter(settings)
    storyboard_provider = router.text("storyboard")
    edit_provider = router.text("edit_plan")
    require(isinstance(storyboard_provider, DeepSeekTextProvider), "storyboard should route to DeepSeek text provider")
    require(
        storyboard_provider.model == "deepseek-v4-pro",
        f"storyboard should use purpose-specific DeepSeek model: {storyboard_provider.model}",
    )
    require(
        storyboard_provider.reasoning_effort == "max",
        f"storyboard should use max reasoning effort: {storyboard_provider.reasoning_effort}",
    )
    require(storyboard_provider.thinking_enabled is True, "storyboard should enable DeepSeek thinking")
    require(
        edit_provider.model == "deepseek-chat",
        f"other deepseek text purposes should keep default text model: {edit_provider.model}",
    )
    require(edit_provider.reasoning_effort == "low", "other deepseek text purposes should keep default reasoning")
    require(edit_provider.thinking_enabled is False, "other deepseek text purposes should keep default thinking option")

    project_settings = load_settings(ROOT_DIR / "config.yaml")
    project_storyboard_provider = ProviderRouter(project_settings).text("storyboard")
    require(
        isinstance(project_storyboard_provider, DeepSeekTextProvider),
        f"config.yaml storyboard should route to DeepSeek text provider: {type(project_storyboard_provider).__name__}",
    )
    require(
        project_storyboard_provider.model == "deepseek-v4-pro",
        f"config.yaml storyboard model mismatch: {project_storyboard_provider.model}",
    )
    require(
        project_storyboard_provider.reasoning_effort == "max",
        f"config.yaml storyboard reasoning effort mismatch: {project_storyboard_provider.reasoning_effort}",
    )
    require(
        project_storyboard_provider.thinking_enabled is True,
        "config.yaml storyboard should enable DeepSeek thinking",
    )
    require(
        load_settings(ROOT_DIR / "config.yaml.example").generation.max_shots == 10,
        "config.yaml.example should default generation.max_shots to 10",
    )
    require(
        project_settings.generation.max_shots == 10,
        f"config.yaml generation.max_shots mismatch: {project_settings.generation.max_shots}",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "generation",
            "--config",
            str(ROOT_DIR / "config.yaml.example"),
            "--max-shots",
            "7",
        ]
    )
    require(args.max_shots == 7, f"CLI --max-shots should parse override value, got {args.max_shots}")

    print("storyboard_deepseek_model_smoke=ok")
    print(f"storyboard_model={project_storyboard_provider.model}")
    print(f"storyboard_reasoning_effort={project_storyboard_provider.reasoning_effort}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
