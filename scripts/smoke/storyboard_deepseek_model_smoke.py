from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import build_parser  # noqa: E402
from autodrama.config import ProviderSettings, Settings, load_settings  # noqa: E402
from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider  # noqa: E402
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


class SmokeOutput(BaseModel):
    ok: bool


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
        ),
        "rightcode": ProviderSettings(
            base_url="https://www.right.codes/draw",
            api_key_env="RIGHTCODE_API_KEY",
            models={
                "text": "gpt-5.5-mini",
                "storyboard": "gpt-5.5",
            },
            options={
                "text_response_format": True,
                "reasoning_effort": "medium",
                "storyboard_reasoning_effort": "xhigh",
            },
        ),
    }
    settings.routing = {
        "text": {
            "storyboard": "rightcode",
            "edit_plan": "deepseek",
        }
    }

    router = ProviderRouter(settings)
    storyboard_provider = router.text("storyboard")
    edit_provider = router.text("edit_plan")
    require(isinstance(storyboard_provider, RightCodeTextProvider), "storyboard should route to RightCode text provider")
    require(
        storyboard_provider.model == "gpt-5.5",
        f"storyboard should use purpose-specific RightCode model: {storyboard_provider.model}",
    )
    require(
        storyboard_provider.reasoning_effort == "xhigh",
        f"storyboard should use xhigh reasoning effort: {storyboard_provider.reasoning_effort}",
    )
    require(
        storyboard_provider.endpoint == "https://www.right.codes/draw/v1/chat/completions",
        f"storyboard should use RightCode chat endpoint: {storyboard_provider.endpoint}",
    )
    payload = storyboard_provider.build_payload(
        "Return ok=true.",
        SmokeOutput,
        metadata={
            "node_name": "storyboard_generation",
            "parameters": {"seed": 1234},
        },
    )
    require(payload["model"] == "gpt-5.5", f"payload model mismatch: {payload['model']}")
    require(payload["reasoning_effort"] == "xhigh", f"payload reasoning mismatch: {payload['reasoning_effort']}")
    require(payload["response_format"] == {"type": "json_object"}, "payload should request JSON object output")
    require(payload["seed"] == 1234, "metadata parameters should pass through to RightCode payload")
    require("Required JSON schema" in payload["messages"][1]["content"], "payload should inject the schema")
    require(
        edit_provider.model == "deepseek-chat",
        f"other deepseek text purposes should keep default text model: {edit_provider.model}",
    )
    require(edit_provider.reasoning_effort == "low", "other deepseek text purposes should keep default reasoning")
    require(edit_provider.thinking_enabled is False, "other deepseek text purposes should keep default thinking option")

    project_settings = load_settings(ROOT_DIR / "config.yaml")
    project_storyboard_provider = ProviderRouter(project_settings).text("storyboard")
    require(
        isinstance(project_storyboard_provider, RightCodeTextProvider),
        f"config.yaml storyboard should route to RightCode text provider: {type(project_storyboard_provider).__name__}",
    )
    require(
        project_storyboard_provider.model == "gpt-5.5",
        f"config.yaml storyboard model mismatch: {project_storyboard_provider.model}",
    )
    require(
        project_storyboard_provider.reasoning_effort == "xhigh",
        f"config.yaml storyboard reasoning effort mismatch: {project_storyboard_provider.reasoning_effort}",
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
            "--shots",
            "1-7",
        ]
    )
    require(args.shots == "1-7", f"CLI --shots should parse storyboard big-loop range, got {args.shots}")
    require(not hasattr(args, "max_shots"), "CLI should not expose a max shot override")

    print("storyboard_rightcode_model_smoke=ok")
    print(f"storyboard_model={project_storyboard_provider.model}")
    print(f"storyboard_reasoning_effort={project_storyboard_provider.reasoning_effort}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
