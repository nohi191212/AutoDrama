from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TypeVar
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.logging import get_pregen_detail_logger
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class DeepSeekTextProvider:
    name = "deepseek"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://api.deepseek.com").rstrip("/")
        self.model = settings.models.get("text", "deepseek-v4-pro")
        self.api_key = settings.secret("api_key_env") or settings.api_key_env
        self.reasoning_effort = str(settings.options.get("reasoning_effort", "high"))
        self.thinking_enabled = bool(settings.options.get("thinking_enabled", True))

    def _write_detail_log(
        self,
        title: str,
        *,
        metadata: dict[str, Any],
        fields: dict[str, Any],
        sections: list[tuple[str, str]] | None = None,
    ) -> None:
        detail_logger = get_pregen_detail_logger()
        if not detail_logger.handlers:
            return

        lines = [
            "",
            "=" * 100,
            f"{datetime.now().isoformat(timespec='seconds')} {title}",
            "-" * 100,
            f"provider: {self.name}",
            f"model: {self.model}",
            f"base_url: {self.base_url}",
            f"node_name: {metadata.get('node_name', '-')}",
            f"project_id: {metadata.get('project_id', '-')}",
        ]
        for key, value in fields.items():
            lines.append(f"{key}: {value}")

        for section_title, section_content in sections or []:
            lines.extend(
                [
                    "-" * 100,
                    section_title,
                    section_content,
                ]
            )
        lines.append("=" * 100)

        try:
            detail_logger.info("%s", "\n".join(lines))
        except Exception:
            return

    @staticmethod
    def _parse_and_validate_content(content: str, schema: type[T]) -> tuple[dict[str, Any], T]:
        parsed = parse_json_object(content)
        return parsed, schema.model_validate(parsed)

    @staticmethod
    def _validate_expected_mapping_keys(validated: BaseModel, metadata: dict[str, Any]) -> None:
        field_name = metadata.get("required_mapping_field")
        expected_keys = metadata.get("expected_keys")
        if not field_name or expected_keys is None:
            return

        payload = getattr(validated, str(field_name), None)
        if not isinstance(payload, dict):
            raise ProviderBadResponseError(f"{field_name} must be a JSON object")

        expected = [str(key) for key in expected_keys]
        actual = [str(key) for key in payload.keys()]
        if set(actual) != set(expected):
            raise ProviderBadResponseError(
                f"{field_name} must contain exactly {', '.join(expected)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    @staticmethod
    def _metadata_constraints_text(metadata: dict[str, Any]) -> str:
        field_name = metadata.get("required_mapping_field")
        expected_keys = metadata.get("expected_keys")
        if not field_name or expected_keys is None:
            return ""

        expected = ", ".join(str(key) for key in expected_keys)
        return (
            f"- `{field_name}` must contain exactly these keys: {expected}.\n"
            f"- Do not add `note`, `summary`, or any other keys inside `{field_name}`."
        )

    async def _repair_json_response(
        self,
        client: AsyncOpenAI,
        *,
        original_content: str,
        parse_error: Exception,
        schema: type[T],
        schema_pretty_json: str,
        metadata: dict[str, Any],
        call_id: str,
        attempt: int,
        max_attempts: int,
        extra_body: dict[str, Any],
    ) -> str:
        repair_system_prompt = (
            "You repair malformed JSON for a structured generation pipeline. "
            "Return only one valid JSON object. Do not use Markdown. "
            "Preserve the original story content and wording as much as possible; "
            "only fix JSON syntax and schema mismatches."
        )
        metadata_constraints = self._metadata_constraints_text(metadata)
        metadata_constraints_block = (
            f"\nAdditional required constraints:\n{metadata_constraints}\n" if metadata_constraints else ""
        )
        repair_prompt = (
            "The previous model response could not be parsed or validated.\n\n"
            f"Error:\n{parse_error!r}\n\n"
            f"Required JSON schema:\n{schema_pretty_json}\n\n"
            f"{metadata_constraints_block}"
            "Invalid response content:\n"
            f"{original_content}\n\n"
            "Return only the repaired JSON object."
        )

        self._write_detail_log(
            "DEEPSEEK JSON REPAIR REQUEST",
            metadata=metadata,
            fields={
                "call_id": call_id,
                "attempt": f"{attempt}/{max_attempts}",
                "schema": schema.__name__,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "reasoning_effort": self.reasoning_effort,
                "thinking_enabled": self.thinking_enabled,
            },
            sections=[
                ("SYSTEM MESSAGE", repair_system_prompt),
                ("USER MESSAGE SENT TO DEEPSEEK FOR JSON REPAIR", repair_prompt),
            ],
        )

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": repair_system_prompt},
                {"role": "user", "content": repair_prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            reasoning_effort=self.reasoning_effort,
            extra_body=extra_body or None,
        )
        repaired_content = response.choices[0].message.content
        if repaired_content is None:
            raise ProviderBadResponseError("DeepSeek JSON repair response content is empty")
        return repaired_content

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        metadata = metadata or {}
        if not self.api_key:
            raise ProviderAuthError("Missing DeepSeek API key environment variable")

        system_prompt = (
            "You are a structured JSON generation engine. "
            "Return only valid JSON that matches the requested schema. "
            "Do not wrap the answer in Markdown."
        )
        schema_dict = schema.model_json_schema()
        schema_json = json.dumps(schema_dict, ensure_ascii=False)
        schema_pretty_json = json.dumps(schema_dict, ensure_ascii=False, indent=2)
        metadata_constraints = self._metadata_constraints_text(metadata)
        metadata_constraints_block = (
            f"\n\nAdditional structured constraints:\n{metadata_constraints}" if metadata_constraints else ""
        )
        user_prompt = f"{prompt}\n\nRequired JSON schema:\n{schema_json}{metadata_constraints_block}"
        call_id = uuid4().hex[:12]

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.runtime.request_timeout_seconds,
        )

        extra_body: dict[str, Any] = {}
        if self.thinking_enabled:
            extra_body["thinking"] = {"type": "enabled"}

        max_attempts = max(1, int(self.runtime.max_text_retry))
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            self._write_detail_log(
                "DEEPSEEK REQUEST",
                metadata=metadata,
                fields={
                    "call_id": call_id,
                    "attempt": f"{attempt}/{max_attempts}",
                    "schema": schema.__name__,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                    "reasoning_effort": self.reasoning_effort,
                    "thinking_enabled": self.thinking_enabled,
                },
                sections=[
                    ("SYSTEM MESSAGE", system_prompt),
                    ("RENDERED PROMPT BEFORE SCHEMA INJECTION", prompt),
                    ("JSON SCHEMA INJECTED INTO USER MESSAGE", schema_pretty_json),
                    ("USER MESSAGE SENT TO DEEPSEEK", user_prompt),
                ],
            )

            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    reasoning_effort=self.reasoning_effort,
                    extra_body=extra_body or None,
                )
            except Exception as exc:
                last_error = ProviderBadResponseError(f"DeepSeek request failed: {exc}")
                self._write_detail_log(
                    "DEEPSEEK REQUEST ERROR",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(exc),
                    },
                )
                continue

            try:
                content = response.choices[0].message.content
            except Exception as exc:
                content = "<unavailable>"
                last_error = ProviderBadResponseError(f"DeepSeek response message content is unavailable: {exc}")
                self._write_detail_log(
                    "DEEPSEEK RESPONSE PARSE ERROR",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(last_error),
                    },
                    sections=[
                        ("RAW DEEPSEEK MESSAGE CONTENT", content),
                    ],
                )
                continue

            if content is None or not content.strip():
                last_error = ProviderBadResponseError("DeepSeek response content is empty")
                self._write_detail_log(
                    "DEEPSEEK EMPTY RESPONSE",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(last_error),
                    },
                    sections=[
                        ("RAW DEEPSEEK MESSAGE CONTENT", content or ""),
                    ],
                )
                continue

            try:
                parsed, validated = self._parse_and_validate_content(content, schema)
                self._validate_expected_mapping_keys(validated, metadata)
                self._write_detail_log(
                    "DEEPSEEK RESPONSE",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                    },
                    sections=[
                        ("RAW DEEPSEEK MESSAGE CONTENT", content),
                        ("PARSED JSON OBJECT", json.dumps(parsed, ensure_ascii=False, indent=2)),
                        (
                            "VALIDATED OUTPUT",
                            json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2),
                        ),
                    ],
                )
                return validated
            except Exception as exc:
                last_error = exc
                self._write_detail_log(
                    "DEEPSEEK RESPONSE PARSE ERROR",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(exc),
                    },
                    sections=[
                        ("RAW DEEPSEEK MESSAGE CONTENT", content),
                    ],
                )

            try:
                repaired_content = await self._repair_json_response(
                    client,
                    original_content=content,
                    parse_error=last_error,
                    schema=schema,
                    schema_pretty_json=schema_pretty_json,
                    metadata=metadata,
                    call_id=call_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    extra_body=extra_body,
                )
                if not repaired_content.strip():
                    raise ProviderBadResponseError("DeepSeek JSON repair response content is empty")

                repaired_parsed, repaired_validated = self._parse_and_validate_content(repaired_content, schema)
                self._validate_expected_mapping_keys(repaired_validated, metadata)
                self._write_detail_log(
                    "DEEPSEEK JSON REPAIR RESPONSE",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                    },
                    sections=[
                        ("RAW REPAIRED DEEPSEEK MESSAGE CONTENT", repaired_content),
                        ("PARSED REPAIRED JSON OBJECT", json.dumps(repaired_parsed, ensure_ascii=False, indent=2)),
                        (
                            "VALIDATED REPAIRED OUTPUT",
                            json.dumps(repaired_validated.model_dump(mode="json"), ensure_ascii=False, indent=2),
                        ),
                    ],
                )
                return repaired_validated
            except Exception as repair_exc:
                last_error = ProviderBadResponseError(
                    f"Failed to parse DeepSeek JSON response: {last_error}; "
                    f"JSON repair also failed: {repair_exc}"
                )
                self._write_detail_log(
                    "DEEPSEEK JSON REPAIR ERROR",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(repair_exc),
                    },
                    sections=[
                        (
                            "RAW REPAIRED DEEPSEEK MESSAGE CONTENT",
                            str(locals().get("repaired_content", "<unavailable>")),
                        ),
                    ],
                )

        if last_error is None:
            last_error = ProviderBadResponseError("DeepSeek did not return a response")
        raise ProviderBadResponseError(
            f"DeepSeek failed to produce valid {schema.__name__} after {max_attempts} attempt(s): {last_error}"
        ) from last_error
