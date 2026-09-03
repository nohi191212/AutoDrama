from __future__ import annotations

from pathlib import Path

from autodrama.config import load_settings
from autodrama.core.schemas import ScriptWorldviewExtractOutput
from autodrama.prompt_evolution.sol_client import SolVisionClient
from autodrama.providers.aibox.text.gpt import AiboxGPTTextProvider
from autodrama.providers.router import ProviderRouter
from autodrama.workflows.nodes.director_nodes import DirectorNodeBase


ROOT = Path(__file__).resolve().parents[2]
CONFIGS = (
    "config.yaml.example",
    "config.chonghui_jiuba.yaml",
    "config.saodi_bashinian.yaml",
    "config.saodi_bashinian_terra_image2.yaml",
    "config.yushou_xianchao.yaml",
    "saodi.yaml",
)


def main() -> int:
    for name in CONFIGS:
        settings = load_settings(ROOT / name)
        assert not hasattr(settings.project, "script_type")
        binding = settings.nodes.get("script_worldview_extract")
        if binding is None:
            raise AssertionError(f"{name} has no script_worldview_extract node binding")
        assert binding.model == "aibox:gpt-5.6-sol"
        assert settings.model_catalog.require(binding.model).provider_name == "aibox"
        assert settings.model_catalog.require(binding.model).family == "gpt"
        assert binding.params["response_format"] == "json_object"
        image_provider = ProviderRouter(settings).image(
            "key_vision",
            node_name="key_vision_image_generation",
        )
        expected_canvas = {
            "9:16": "2160x3840",
            "16:9": "3840x2160",
        }[str(settings.nodes["key_vision_image_generation"].params["aspectRatio"])]
        assert DirectorNodeBase.key_vision_image_canvas(image_provider) == expected_canvas

    settings = load_settings(ROOT / CONFIGS[0])
    provider = ProviderRouter(settings).text("script", node_name="script_worldview_extract")
    implementation = provider._provider
    assert isinstance(implementation, AiboxGPTTextProvider)
    assert implementation.endpoint == "https://api.lk888.ai/v1/chat/completions"
    payload = implementation.build_payload(
        "Extract the screenplay worldview.",
        ScriptWorldviewExtractOutput,
        temperature=0.2,
    )
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False

    # GPT-5.6 must never fall back to the Responses transport, even when a
    # legacy caller explicitly requests it.
    sol_client = SolVisionClient(
        api_key="smoke-key",
        model="gpt-5.6-sol",
        api_mode="responses",
    )
    assert sol_client.api_mode == "chat_completions"
    assert sol_client._chat_endpoint() == "https://api.lk888.ai/v1/chat/completions"

    print("key vision script type config smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
