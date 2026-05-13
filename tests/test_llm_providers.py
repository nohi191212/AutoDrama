"""Tests for LLM providers (with mocked HTTP)."""

from unittest.mock import MagicMock, patch

from autodrama.config.schema import LLMProviderConfig
from autodrama.llm.anthropic_provider import AnthropicChat
from autodrama.llm.factory import LLMFactory
from autodrama.llm.openai_provider import OpenAIChat


def test_openai_chat_returns_llm_response() -> None:
    cfg = LLMProviderConfig(api_key="sk-test", model="gpt-4o")
    provider = OpenAIChat(cfg)

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Hello"
    mock_resp.model = "gpt-4o"
    mock_resp.usage.prompt_tokens = 5
    mock_resp.usage.completion_tokens = 3
    mock_resp.usage.total_tokens = 8

    with patch.object(provider._client.chat.completions, "create", return_value=mock_resp):
        resp = provider.chat([{"role": "user", "content": "Hi"}])
        assert resp.content == "Hello"
        assert resp.model == "gpt-4o"
        assert resp.usage["total_tokens"] == 8


def test_openai_structured_output() -> None:
    cfg = LLMProviderConfig(api_key="sk-test", model="gpt-4o")
    provider = OpenAIChat(cfg)

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = '{"title": "Test"}'
    mock_resp.model = "gpt-4o"
    mock_resp.usage.prompt_tokens = 10
    mock_resp.usage.completion_tokens = 5
    mock_resp.usage.total_tokens = 15

    schema = {"type": "object", "properties": {"title": {"type": "string"}}}
    with patch.object(provider._client.chat.completions, "create", return_value=mock_resp):
        resp = provider.chat_with_structured_output(
            [{"role": "user", "content": "Gen"}],
            output_schema=schema,
        )
        assert resp.content == '{"title": "Test"}'


def test_factory_registers_providers() -> None:
    providers = LLMFactory.list_providers()
    assert "openai" in providers
    assert "anthropic" in providers
    assert "ernie" in providers
    assert "qwen" in providers
    assert "deepseek" in providers


def test_factory_creates_openai() -> None:
    cfg = LLMProviderConfig(api_key="sk-test", model="gpt-4o")
    provider = LLMFactory.create("openai", cfg)
    assert isinstance(provider, OpenAIChat)


def test_factory_qwen_uses_openai_class() -> None:
    cfg = LLMProviderConfig(api_key="sk-test", model="qwen-plus", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    provider = LLMFactory.create("qwen", cfg)
    assert isinstance(provider, OpenAIChat)
