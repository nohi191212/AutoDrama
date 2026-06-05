from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TypeVar
from uuid import uuid4

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.logging import get_pregen_detail_logger
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class RightCodeTextProvider:
    """RightCode text provider via the OpenAI-compatible Chat Completions API."""

    name = "rightcode"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings, *, model_key: str = "text") -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://www.right.codes/draw").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model_key = model_key
        self.model = settings.models.get(model_key) or settings.models.get("text", "gpt-5.5")
        self.api_key = settings.secret("api_key_env")
        self.reasoning_effort = str(
            settings.options.get(f"{model_key}_reasoning_effort", settings.options.get("reasoning_effort", "xhigh"))
        )
        self.use_response_format = self._bool_option(
            settings.options.get(
                f"{model_key}_response_format",
                settings.options.get("text_response_format", settings.options.get("response_format", True)),
            ),
            default=True,
        )

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        for suffix in (
            "/v1/images/generations",
            "/images/generations",
            "/v1/chat/completions",
            "/chat/completions",
        ):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)].rstrip("/")
                break
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    @staticmethod
    def _bool_option(value: object, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "n", "off", "disabled"}:
                return False
        return bool(value)

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

    def _extra_parameters(self, metadata: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for key in (
            "parameters",
            "text_parameters",
            f"{self.model_key}_parameters",
            "extra_body",
            f"{self.model_key}_extra_body",
        ):
            value = self.settings.options.get(key)
            if isinstance(value, dict):
                merged.update(value)

        metadata_parameters = metadata.get("parameters")
        if isinstance(metadata_parameters, dict):
            merged.update(metadata_parameters)
        return merged

    def build_payload(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
        repair: bool = False,
        original_content: str | None = None,
        parse_error: Exception | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        schema_dict = schema.model_json_schema()
        schema_json = json.dumps(schema_dict, ensure_ascii=False)
        schema_pretty_json = json.dumps(schema_dict, ensure_ascii=False, indent=2)
        metadata_constraints = self._metadata_constraints_text(metadata)
        metadata_constraints_block = (
            f"\n\nAdditional structured constraints:\n{metadata_constraints}" if metadata_constraints else ""
        )

        if repair:
            system_prompt = (
                "You repair malformed JSON for a structured generation pipeline. "
                "Return only one valid JSON object. Do not use Markdown. "
                "Preserve the original story content and wording as much as possible; "
                "only fix JSON syntax and schema mismatches."
            )
            user_prompt = (
                "The previous model response could not be parsed or validated.\n\n"
                f"Error:\n{parse_error!r}\n\n"
                f"Required JSON schema:\n{schema_pretty_json}"
                f"{metadata_constraints_block}\n\n"
                "Invalid response content:\n"
                f"{original_content or ''}\n\n"
                "Return only the repaired JSON object."
            )
            temperature = 0
        else:
            system_prompt = (
                "You are a structured JSON generation engine. "
                "Return only valid JSON that matches the requested schema. "
                "Do not wrap the answer in Markdown."
            )
            user_prompt = f"{prompt}\n\nRequired JSON schema:\n{schema_json}{metadata_constraints_block}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if self.use_response_format:
            payload["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        payload.update(self._extra_parameters(metadata))
        return payload

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
            f"endpoint: {self.endpoint}",
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
            detail_logger.info(
                "%s",
                "\n".join(lines),
                extra={
                    "node_name": metadata.get("node_name"),
                    "episode_key": metadata.get("episode_key"),
                    "shot_id": metadata.get("shot_id"),
                },
            )
        except Exception:
            return

    async def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderAuthError("Missing RightCode API key environment variable")

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"RightCode text request failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"RightCode text response is not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderBadResponseError(f"RightCode text response must be a JSON object: {body!r}")
        return body

    @classmethod
    def _message_content(cls, payload: dict[str, Any]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderBadResponseError(f"RightCode text response message content is unavailable: {exc}") from exc

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
                    continue
                if isinstance(item, str):
                    chunks.append(item)
            if chunks:
                return "\n".join(chunks)
        raise ProviderBadResponseError(f"RightCode text response content is not text: {content!r}")

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
            raise ProviderAuthError("Missing RightCode API key environment variable")

        call_id = uuid4().hex[:12]
        max_attempts = max(1, int(self.runtime.max_text_retry))
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            content = ""
            payload = self.build_payload(prompt, schema, temperature=temperature, metadata=metadata)
            self._write_detail_log(
                "RIGHTCODE TEXT REQUEST",
                metadata=metadata,
                fields={
                    "call_id": call_id,
                    "attempt": f"{attempt}/{max_attempts}",
                    "schema": schema.__name__,
                    "temperature": temperature,
                    "response_format": payload.get("response_format"),
                    "reasoning_effort": self.reasoning_effort,
                },
                sections=[
                    ("SYSTEM MESSAGE", str(payload["messages"][0]["content"])),
                    ("USER MESSAGE SENT TO RIGHTCODE", str(payload["messages"][1]["content"])),
                ],
            )

            try:
                response_payload = await self._post_chat_completion(payload)
                content = self._message_content(response_payload)
                if not content.strip():
                    raise ProviderBadResponseError("RightCode text response content is empty")
                parsed, validated = self._parse_and_validate_content(content, schema)
                self._validate_expected_mapping_keys(validated, metadata)
                self._write_detail_log(
                    "RIGHTCODE TEXT RESPONSE",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                    },
                    sections=[
                        ("RAW RIGHTCODE MESSAGE CONTENT", content),
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
                    "RIGHTCODE TEXT RESPONSE ERROR",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(exc),
                    },
                    sections=[
                        ("RAW RIGHTCODE MESSAGE CONTENT", content or "<unavailable>"),
                    ],
                )

            if not content:
                continue

            try:
                repair_payload = self.build_payload(
                    prompt,
                    schema,
                    metadata=metadata,
                    repair=True,
                    original_content=content,
                    parse_error=last_error,
                )
                repaired_response_payload = await self._post_chat_completion(repair_payload)
                repaired_content = self._message_content(repaired_response_payload)
                if not repaired_content.strip():
                    raise ProviderBadResponseError("RightCode JSON repair response content is empty")
                repaired_parsed, repaired_validated = self._parse_and_validate_content(repaired_content, schema)
                self._validate_expected_mapping_keys(repaired_validated, metadata)
                self._write_detail_log(
                    "RIGHTCODE JSON REPAIR RESPONSE",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                    },
                    sections=[
                        ("RAW REPAIRED RIGHTCODE MESSAGE CONTENT", repaired_content),
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
                    f"Failed to parse RightCode JSON response: {last_error}; "
                    f"JSON repair also failed: {repair_exc}"
                )
                self._write_detail_log(
                    "RIGHTCODE JSON REPAIR ERROR",
                    metadata=metadata,
                    fields={
                        "call_id": call_id,
                        "attempt": f"{attempt}/{max_attempts}",
                        "schema": schema.__name__,
                        "error": repr(repair_exc),
                    },
                    sections=[
                        (
                            "RAW REPAIRED RIGHTCODE MESSAGE CONTENT",
                            str(locals().get("repaired_content", "<unavailable>")),
                        ),
                    ],
                )

        if last_error is None:
            last_error = ProviderBadResponseError("RightCode did not return a response")
        raise ProviderBadResponseError(
            f"RightCode failed to produce valid {schema.__name__} after {max_attempts} attempt(s): {last_error}"
        ) from last_error
