from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.providers.deepseek import DeepSeekTextProvider


def test_deepseek_reads_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    provider = DeepSeekTextProvider(
        ProviderSettings(api_key_env="DEEPSEEK_API_KEY", models={"text": "deepseek-v4-pro"}),
        RuntimeSettings(),
    )

    assert provider.api_key == "sk-from-env"
    assert provider.model == "deepseek-v4-pro"
    assert provider.reasoning_effort == "high"
    assert provider.thinking_enabled is True


def test_deepseek_accepts_direct_api_key_for_local_config() -> None:
    provider = DeepSeekTextProvider(
        ProviderSettings(
            api_key_env="sk-direct",
            models={"text": "deepseek-v4-pro"},
            options={"reasoning_effort": "medium", "thinking_enabled": False},
        ),
        RuntimeSettings(),
    )

    assert provider.api_key == "sk-direct"
    assert provider.reasoning_effort == "medium"
    assert provider.thinking_enabled is False
