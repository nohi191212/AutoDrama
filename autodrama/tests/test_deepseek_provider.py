import asyncio

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.schemas import RoleDesignOutput, ScriptNovelExtractBatchOutput
from autodrama.logging import setup_logging
from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider


def test_deepseek_reads_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    provider = DeepSeekTextProvider(
        ProviderSettings(api_key_env="DEEPSEEK_API_KEY", models={"text": "deepseek-v4-pro"}),
        RuntimeSettings(),
    )

    assert provider.api_key == "sk-from-env"
    assert provider.model == "deepseek-v4-pro"
    assert provider.reasoning_effort == "max"
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


def test_deepseek_writes_prompt_and_output_detail_log(tmp_path, monkeypatch) -> None:
    captured_request = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured_request.update(kwargs)

            class Message:
                content = '{"roles":[{"name":"林舟","intro":"被陷害的青年","personality":"冷静","aliases":["男主"]}]}'

            class Choice:
                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured_request["client"] = kwargs
            self.chat = FakeChat()

    monkeypatch.setattr("autodrama.providers.deepseek.text.deepseek.AsyncOpenAI", FakeAsyncOpenAI)
    setup_logging(tmp_path)
    provider = DeepSeekTextProvider(
        ProviderSettings(
            api_key_env="sk-direct",
            models={"text": "deepseek-v4-pro"},
            options={"thinking_enabled": False},
        ),
        RuntimeSettings(),
    )

    output = asyncio.run(
        provider.generate_json(
            "请设计人物。",
            RoleDesignOutput,
            temperature=0.4,
            metadata={"node_name": "role_design", "project_id": "test_project"},
        )
    )

    assert output.roles[0].name == "林舟"
    assert captured_request["response_format"] == {"type": "json_object"}
    assert "Required JSON schema" in captured_request["messages"][1]["content"]

    detail_log = tmp_path / "logs" / "pregen_detail.log"
    content = detail_log.read_text(encoding="utf-8")
    assert "DEEPSEEK REQUEST" in content
    assert "DEEPSEEK RESPONSE" in content
    assert "USER MESSAGE SENT TO DEEPSEEK" in content
    assert "JSON SCHEMA INJECTED INTO USER MESSAGE" in content
    assert "RoleDesignOutput" in content
    assert "role_design" in content
    assert "RAW DEEPSEEK MESSAGE CONTENT" in content
    assert "林舟" in content


def test_deepseek_repairs_invalid_json_response(tmp_path, monkeypatch) -> None:
    calls = []
    responses = [
        '{"novel_extract":{"episode_001":"第一集内容"}',
        '{"novel_extract":{"episode_001":"第一集内容"}}',
    ]

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)

            class Message:
                content = responses[len(calls) - 1]

            class Choice:
                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr("autodrama.providers.deepseek.text.deepseek.AsyncOpenAI", FakeAsyncOpenAI)
    setup_logging(tmp_path)
    provider = DeepSeekTextProvider(
        ProviderSettings(
            api_key_env="sk-direct",
            models={"text": "deepseek-v4-pro"},
            options={"thinking_enabled": False},
        ),
        RuntimeSettings(),
    )

    output = asyncio.run(
        provider.generate_json(
            "请打磨剧本。",
            ScriptNovelExtractBatchOutput,
            metadata={"node_name": "script_novel_extract", "project_id": "test_project"},
        )
    )

    assert output.novel_extract["episode_001"] == "第一集内容"
    assert len(calls) == 2
    assert "Return only the repaired JSON object" in calls[1]["messages"][1]["content"]

    detail_log = tmp_path / "logs" / "pregen_detail.log"
    content = detail_log.read_text(encoding="utf-8")
    assert "DEEPSEEK RESPONSE PARSE ERROR" in content
    assert "DEEPSEEK JSON REPAIR REQUEST" in content
    assert "DEEPSEEK JSON REPAIR RESPONSE" in content
