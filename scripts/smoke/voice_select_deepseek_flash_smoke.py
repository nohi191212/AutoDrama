from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, Settings  # noqa: E402
from autodrama.core.voice_catalog import VoiceCatalogManifest  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class DummyJudge:
    name = "qwen_omni"
    model = "qwen3.5-omni-plus"


class DummyRouter:
    def judge(self, purpose: str):
        if purpose != "voice_select":
            raise ValueError(f"unexpected judge purpose: {purpose}")
        return DummyJudge()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings()
    settings.providers["deepseek"] = ProviderSettings(
        api_key_env="sk-direct",
        models={"text": "deepseek-v4-pro"},
        options={"reasoning_effort": "max", "thinking_enabled": True},
    )
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(repo=repo, router=DummyRouter())
    node = workflow._voice_node_runner("voice_select")

    provider, error = node.voice_select_text_provider()
    require(error is None, f"unexpected provider error: {error}")
    require(getattr(provider, "name", None) == "deepseek", f"unexpected provider: {provider}")
    require(getattr(provider, "model", None) == "deepseek-v4-flash", f"unexpected model: {provider.model}")
    require(getattr(provider, "thinking_enabled", None) is False, "voice_select text provider should disable thinking")
    require(getattr(provider, "reasoning_effort", None) == "low", "voice_select text provider should use low reasoning effort")
    require(settings.providers["deepseek"].models["text"] == "deepseek-v4-pro", "global deepseek model was mutated")
    require(settings.providers["deepseek"].options["thinking_enabled"] is True, "global deepseek thinking option was mutated")

    manifest = VoiceCatalogManifest(
        catalog_version="smoke",
        provider="volcengine",
        model="seed-tts-2.0",
        sample_emotions=["normal"],
        voices=[],
    )
    selection_model = node.selection_model(manifest)
    require("text_shortlist=deepseek:deepseek-v4-flash" in selection_model, selection_model)
    require("audio_judge=qwen_omni:qwen3.5-omni-plus" in selection_model, selection_model)

    print("voice_select_deepseek_flash_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
