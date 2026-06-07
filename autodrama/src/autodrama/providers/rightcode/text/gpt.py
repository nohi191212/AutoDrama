from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.logging import get_pregen_detail_logger
from autodrama.providers.base import AssetRef
from autodrama.providers.json_utils import parse_json_object
from autodrama.providers.media_refs import ref_url_or_data

T = TypeVar("T", bound=BaseModel)


class RightCodeTextProvider:
    """RightCode text provider via the OpenAI-compatible Responses API."""

    name = "rightcode"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings, *, model_key: str = "text") -> None:
        self.settings = settings
        self.runtime = runtime
        base_url = (
            settings.options.get(f"{model_key}_base_url")
            or settings.options.get("text_base_url")
            or settings.base_url
            or "https://www.right.codes/codex"
        )
        self.base_url = str(base_url).rstrip("/")
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
            "/v1/responses",
            "/responses",
            "/v1/images/generations",
            "/images/generations",
            "/v1/chat/completions",
            "/chat/completions",
        ):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)].rstrip("/")
                break
        if base_url.endswith("/draw"):
            base_url = f"{base_url[:-5]}/codex"
        if base_url.endswith("/v1"):
            return f"{base_url}/responses"
        return f"{base_url}/v1/responses"

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

    @staticmethod
    def _audio_format(ref: AssetRef) -> str:
        metadata = ref.metadata or {}
        explicit = metadata.get("format") or metadata.get("audio_format") or metadata.get("response_format")
        if explicit:
            return str(explicit).lower().lstrip(".")
        for value in (ref.url, ref.path):
            if not value:
                continue
            suffix = Path(str(value).split("?", 1)[0]).suffix.lower().lstrip(".")
            if suffix in {"mp3", "wav", "m4a", "aac", "ogg", "opus", "pcm"}:
                return suffix
        return "mp3"

    @staticmethod
    def _media_part(ref: AssetRef) -> dict[str, Any] | None:
        if ref.type == "image":
            value = ref_url_or_data(ref, expected_type="image", default_mime="image/png")
            if not value:
                raise ProviderBadResponseError(f"RightCode image ref {ref.id or '-'} is missing a usable URL/path")
            return {"type": "input_image", "image_url": value}
        if ref.type == "video":
            value = ref_url_or_data(ref, expected_type="video", default_mime="video/mp4")
            if not value:
                raise ProviderBadResponseError(f"RightCode video ref {ref.id or '-'} is missing a usable URL/path")
            return {
                "type": "input_file",
                "filename": Path(str(ref.path or ref.url or ref.id or "video.mp4")).name,
                "file_data": value,
            }
        if ref.type == "audio":
            audio_format = RightCodeTextProvider._audio_format(ref)
            value = ref_url_or_data(ref, expected_type="audio", default_mime=f"audio/{audio_format}")
            if not value:
                raise ProviderBadResponseError(f"RightCode audio ref {ref.id or '-'} is missing a usable URL/path")
            return {
                "type": "input_file",
                "filename": Path(str(ref.path or ref.url or ref.id or f"audio.{audio_format}")).name,
                "file_data": value,
            }
        return None

    @classmethod
    def _input_content_parts(cls, prompt: str, refs: list[AssetRef]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for ref in refs:
            part = cls._media_part(ref)
            if part is not None:
                parts.append(part)
        return parts

    def build_payload(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
        refs: list[AssetRef] | None = None,
        repair: bool = False,
        original_content: str | None = None,
        parse_error: Exception | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        refs = refs or []
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
            "instructions": system_prompt,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": self._input_content_parts(user_prompt, refs if not repair else []),
                },
            ],
            "temperature": temperature,
            "stream": False,
        }
        if self.use_response_format:
            payload["text"] = {"format": {"type": "json_object"}}
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        payload.update(self._extra_parameters(metadata))
        return payload

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
        for message in sanitized.get("input", []):
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "input_image" and isinstance(part.get("image_url"), str):
                    if str(part["image_url"]).startswith("data:"):
                        part["image_url"] = "<base64 image omitted>"
                if part.get("type") == "input_video" and isinstance(part.get("video_url"), str):
                    if str(part["video_url"]).startswith("data:"):
                        part["video_url"] = "<base64 video omitted>"
                if part.get("type") == "input_file" and isinstance(part.get("file_data"), str):
                    if str(part["file_data"]).startswith("data:"):
                        part["file_data"] = "<base64 file omitted>"
                if part.get("type") == "input_audio":
                    input_audio = part.get("input_audio")
                    if isinstance(input_audio, dict) and isinstance(input_audio.get("data"), str):
                        if str(input_audio["data"]).startswith("data:"):
                            input_audio["data"] = "<base64 audio omitted>"
        return sanitized

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

    async def _post_responses(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            body = self._parse_sse_response(response.text)
            if body is None:
                raise ProviderBadResponseError(
                    "RightCode text response is not JSON: "
                    f"{exc}; status={response.status_code}; content_type={response.headers.get('content-type')}; "
                    f"body={response.text[:500]!r}"
                ) from exc
        if not isinstance(body, dict):
            raise ProviderBadResponseError(f"RightCode text response must be a JSON object: {body!r}")
        return body

    @classmethod
    def _parse_sse_response(cls, text: str) -> dict[str, Any] | None:
        events: list[dict[str, Any]] = []
        chunks: list[str] = []
        completed_response: dict[str, Any] | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            event_type = str(event.get("type") or "")
            delta = event.get("delta")
            if event_type.endswith(".delta") and isinstance(delta, str):
                chunks.append(delta)
            if event_type in {"response.output_text.done", "response.output_text.completed"}:
                text_value = event.get("text")
                if isinstance(text_value, str):
                    chunks.append(text_value)
            response = event.get("response")
            if event_type == "response.completed" and isinstance(response, dict):
                completed_response = response

        if completed_response is not None:
            return completed_response
        output_text = "".join(chunks).strip()
        if output_text:
            return {"output_text": output_text, "raw_events": events[-5:]}
        return None

    @classmethod
    def _message_content(cls, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        texts: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text") or part.get("content")
                    if isinstance(text, str):
                        texts.append(text)
            if texts:
                return "\n".join(texts)

        # Compatibility for older OpenAI-compatible chat responses.
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
        refs: list[AssetRef] | None = None,
    ) -> T:
        metadata = metadata or {}
        refs = refs or []
        if not self.api_key:
            raise ProviderAuthError("Missing RightCode API key environment variable")

        call_id = uuid4().hex[:12]
        max_attempts = max(1, int(self.runtime.max_text_retry))
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            content = ""
            payload = self.build_payload(prompt, schema, temperature=temperature, metadata=metadata, refs=refs)
            self._write_detail_log(
                "RIGHTCODE TEXT REQUEST",
                metadata=metadata,
                fields={
                    "call_id": call_id,
                    "attempt": f"{attempt}/{max_attempts}",
                    "schema": schema.__name__,
                    "temperature": temperature,
                    "response_format": payload.get("text"),
                    "reasoning_effort": self.reasoning_effort,
                },
                sections=[
                    ("INSTRUCTIONS", str(payload.get("instructions"))),
                    ("REQUEST PAYLOAD SENT TO RIGHTCODE", json.dumps(self._safe_payload(payload), ensure_ascii=False, indent=2)),
                ],
            )

            try:
                response_payload = await self._post_responses(payload)
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
                    refs=[],
                    repair=True,
                    original_content=content,
                    parse_error=last_error,
                )
                repaired_response_payload = await self._post_responses(repair_payload)
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
