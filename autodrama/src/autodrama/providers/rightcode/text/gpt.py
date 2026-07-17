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
from autodrama.providers.rightcode.url_utils import resolve_rightcode_endpoint

T = TypeVar("T", bound=BaseModel)


class RightCodeTextProvider:
    """RightCode text provider via the OpenAI-compatible Responses API."""

    name = "rightcode"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings, *, model_key: str = "text") -> None:
        self.settings = settings
        self.runtime = runtime
        self.model_key = model_key
        self.model = settings.models.get(model_key) or settings.models.get("text", "gpt-5.6-terra")
        self.base_url = str(settings.base_url or "https://www.right.codes").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.api_key = settings.secret("api_key_env")
        self.reasoning_effort = str(
            settings.options.get(f"{model_key}_reasoning_effort", settings.options.get("reasoning_effort", "xhigh"))
        )
        self.stream = self._bool_option(
            settings.options.get(
                f"{model_key}_stream",
                settings.options.get(
                    "text_stream",
                    settings.options.get("rightcode_stream", settings.options.get("stream", True)),
                ),
            ),
            default=True,
        )
        self.use_response_format = self._bool_option(
            settings.options.get(
                f"{model_key}_response_format",
                settings.options.get("text_response_format", settings.options.get("response_format", True)),
            ),
            default=True,
        )
        self.console_stream = self._bool_option(
            settings.options.get(
                f"{model_key}_console_stream",
                settings.options.get(
                    "text_console_stream",
                    settings.options.get("rightcode_console_stream", settings.options.get("console_stream", False)),
                ),
            ),
            default=False,
        )

    def refresh_endpoint(self) -> None:
        self.endpoint = self._resolve_endpoint(self.base_url)

    def _resolve_endpoint(self, base_url: str) -> str:
        settings = self.settings.model_copy(deep=True)
        settings.base_url = base_url
        return resolve_rightcode_endpoint(
            settings,
            capability="text",
            model_key=self.model_key,
            model=self.model,
            default_base_path="/codex",
            api_path="/v1/responses",
        )

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

    def _console_stream_enabled(self, metadata: dict[str, Any]) -> bool:
        if "console_stream" in metadata:
            return self._bool_option(metadata.get("console_stream"), default=False)
        return self.console_stream

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
            value = str(ref.url or "").strip()
            if not value and str(ref.path or "").startswith(("http://", "https://")):
                value = str(ref.path)
            if not value.startswith(("http://", "https://")):
                raise ProviderBadResponseError(
                    f"RightCode image ref {ref.id or '-'} requires a public URL; "
                    "local files and inline base64 are not supported"
                )
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
        json_mode_prefix = "This request uses json response_format. Return only valid json."

        if repair:
            system_prompt = (
                "You repair malformed JSON for a structured generation pipeline. "
                "Return only one valid JSON object. Do not use Markdown. "
                "Preserve the original story content and wording as much as possible; "
                "only fix JSON syntax and schema mismatches."
            )
            user_prompt = (
                f"{json_mode_prefix}\n\n"
                "The previous model response could not be parsed or validated.\n\n"
                f"Error:\n{parse_error!r}\n\n"
                f"Required json schema:\n{schema_pretty_json}"
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
            user_prompt = f"{json_mode_prefix}\n\n{prompt}\n\nRequired json schema:\n{schema_json}{metadata_constraints_block}"

        content_parts = self._input_content_parts(user_prompt, refs if not repair else [])
        if self.use_response_format:
            content_parts.insert(0, {"type": "input_text", "text": "json"})

        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": system_prompt,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": content_parts,
                },
            ],
            "temperature": temperature,
            "stream": self.stream,
        }
        if self.use_response_format:
            payload["response_format"] = {"type": "json_object"}
        reasoning_effort = (
            metadata.get(f"{self.model_key}_reasoning_effort")
            or metadata.get("reasoning_effort")
            or self.reasoning_effort
        )
        if reasoning_effort:
            payload["reasoning"] = {"effort": str(reasoning_effort)}
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

    async def _post_responses(
        self,
        payload: dict[str, Any],
        *,
        console_stream: bool = False,
        console_stream_label: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderAuthError("Missing RightCode API key environment variable")

        if payload.get("stream"):
            return await self._stream_responses(
                payload,
                console_stream=console_stream,
                console_stream_label=console_stream_label,
            )

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

    async def _stream_responses(
        self,
        payload: dict[str, Any],
        *,
        console_stream: bool = False,
        console_stream_label: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            try:
                async with client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ProviderBadResponseError(
                            "RightCode text streaming request failed with HTTP "
                            f"{response.status_code}: {body.decode('utf-8', errors='replace')[:500]}"
                        )
                    parsed = await self._read_stream_response(
                        response,
                        console_stream=console_stream,
                        console_stream_label=console_stream_label,
                    )
                    if parsed is None:
                        raise ProviderBadResponseError("RightCode text streaming response did not contain output")
                    return parsed
            except httpx.ConnectError as exc:
                raise ProviderBadResponseError(
                    f"RightCode text streaming connection failed before receiving an HTTP response. "
                    f"Check network/proxy/TLS settings for {self.endpoint}: {exc}"
                ) from exc

    @classmethod
    async def _read_stream_response(
        cls,
        response: httpx.Response,
        *,
        console_stream: bool = False,
        console_stream_label: str | None = None,
    ) -> dict[str, Any] | None:
        events: list[dict[str, Any]] = []
        printed_any = False
        if console_stream:
            cls._print_stream_boundary(console_stream_label, start=True)
        async for raw_line in response.aiter_lines():
            if cls._stream_line_is_done(raw_line):
                break
            event = cls._stream_event_from_line(raw_line)
            if event is None:
                continue
            events.append(event)
            if console_stream:
                for chunk in cls._stream_console_chunks(event):
                    cls._print_stream_chunk(chunk)
                    printed_any = True
            event_type = str(event.get("type") or "")
            if event_type in {"response.failed", "response.incomplete"}:
                error = event.get("error")
                response_payload = event.get("response")
                if isinstance(response_payload, dict) and response_payload.get("error") is not None:
                    error = response_payload.get("error")
                if console_stream:
                    cls._print_stream_boundary(console_stream_label, start=False, printed_any=printed_any)
                raise ProviderBadResponseError(f"RightCode text stream ended with {event_type}: {error!r}")
            if event_type == "error":
                if console_stream:
                    cls._print_stream_boundary(console_stream_label, start=False, printed_any=printed_any)
                raise ProviderBadResponseError(f"RightCode text stream error: {event.get('error') or event!r}")
            if event_type == "response.completed":
                payload = cls._payload_from_stream_events(events)
                if console_stream and not printed_any and payload is not None:
                    try:
                        content = cls._message_content(payload)
                    except Exception:
                        content = ""
                    if content:
                        cls._print_stream_chunk(content)
                        printed_any = True
                if console_stream:
                    cls._print_stream_boundary(console_stream_label, start=False, printed_any=printed_any)
                return payload
            if event_type in {"response.output_text.done", "response.output_text.completed"} and event.get("text"):
                payload = cls._payload_from_stream_events(events)
                if console_stream:
                    cls._print_stream_boundary(console_stream_label, start=False, printed_any=printed_any)
                return payload
        payload = cls._payload_from_stream_events(events)
        if console_stream:
            cls._print_stream_boundary(console_stream_label, start=False, printed_any=printed_any)
        return payload

    @staticmethod
    def _print_stream_chunk(chunk: str) -> None:
        try:
            print(chunk, end="", flush=True)
        except OSError:
            return

    @staticmethod
    def _print_stream_boundary(
        label: str | None,
        *,
        start: bool,
        printed_any: bool = False,
    ) -> None:
        title = label or "RightCode text stream"
        if start:
            message = f"\n{'=' * 100}\n{title}\n{'-' * 100}\n"
        else:
            prefix = "\n" if printed_any else ""
            message = f"{prefix}\n{'-' * 100}\nEND {title}\n{'=' * 100}\n"
        try:
            print(message, end="", flush=True)
        except OSError:
            return

    @staticmethod
    def _stream_console_chunks(event: dict[str, Any]) -> list[str]:
        chunks: list[str] = []
        event_type = str(event.get("type") or "")
        delta = event.get("delta")
        if event_type == "response.output_text.delta" and isinstance(delta, str):
            chunks.append(delta)

        choices = event.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                value = choice.get("delta")
                if isinstance(value, dict):
                    content = value.get("content")
                    if isinstance(content, str):
                        chunks.append(content)
        return chunks

    @staticmethod
    def _stream_line_is_done(raw_line: str) -> bool:
        line = raw_line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        return line == "[DONE]"

    @staticmethod
    def _stream_event_from_line(raw_line: str) -> dict[str, Any] | None:
        line = raw_line.strip()
        if not line:
            return None
        if line.startswith("data:"):
            line = line[5:].strip()
        elif line.startswith("event:"):
            return None
        elif not line.startswith("{"):
            return None
        if not line or line == "[DONE]":
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        return event

    @classmethod
    def _parse_sse_response(cls, text: str) -> dict[str, Any] | None:
        events = [
            event
            for raw_line in text.splitlines()
            if (event := cls._stream_event_from_line(raw_line)) is not None
        ]
        return cls._payload_from_stream_events(events)

    @classmethod
    def _payload_from_stream_events(cls, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        delta_chunks: list[str] = []
        done_texts: list[str] = []
        completed_response: dict[str, Any] | None = None
        direct_payload: dict[str, Any] | None = None
        for event in events:
            event_type = str(event.get("type") or "")
            delta = event.get("delta")
            if event_type == "response.output_text.delta" and isinstance(delta, str):
                delta_chunks.append(delta)
            if event_type in {"response.output_text.done", "response.output_text.completed"}:
                text_value = event.get("text")
                if isinstance(text_value, str):
                    done_texts.append(text_value)
            response = event.get("response")
            if event_type == "response.completed" and isinstance(response, dict):
                completed_response = response
            if not event_type and any(key in event for key in ("output", "output_text", "choices")):
                direct_payload = event
            cls._append_chat_completion_delta(event, delta_chunks)

        output_text = "\n".join(done_texts).strip() if done_texts else "".join(delta_chunks).strip()
        if completed_response is not None:
            if output_text and not isinstance(completed_response.get("output_text"), str):
                completed_response = dict(completed_response)
                completed_response["output_text"] = output_text
                completed_response["raw_events"] = events[-5:]
            return completed_response
        if direct_payload is not None:
            if output_text and not isinstance(direct_payload.get("output_text"), str):
                direct_payload = dict(direct_payload)
                direct_payload["output_text"] = output_text
                direct_payload["raw_events"] = events[-5:]
            return direct_payload
        if output_text:
            return {"output_text": output_text, "raw_events": events[-5:]}
        return None

    @staticmethod
    def _append_chat_completion_delta(event: dict[str, Any], chunks: list[str]) -> None:
        choices = event.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            for key in ("delta", "message"):
                value = choice.get(key)
                if isinstance(value, dict):
                    content = value.get("content")
                    if isinstance(content, str):
                        chunks.append(content)

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
            console_stream = self._console_stream_enabled(metadata)
            console_stream_label = (
                f"RIGHTCODE STREAM node={metadata.get('node_name', '-')}"
                f" schema={schema.__name__} attempt={attempt}/{max_attempts}"
            )
            self._write_detail_log(
                "RIGHTCODE TEXT REQUEST",
                metadata=metadata,
                fields={
                    "call_id": call_id,
                    "attempt": f"{attempt}/{max_attempts}",
                    "schema": schema.__name__,
                    "temperature": temperature,
                    "response_format": payload.get("response_format"),
                    "reasoning_effort": (payload.get("reasoning") or {}).get("effort"),
                    "stream": payload.get("stream"),
                    "console_stream": console_stream,
                },
                sections=[
                    ("INSTRUCTIONS", str(payload.get("instructions"))),
                    ("REQUEST PAYLOAD SENT TO RIGHTCODE", json.dumps(self._safe_payload(payload), ensure_ascii=False, indent=2)),
                ],
            )

            try:
                response_payload = await self._post_responses(
                    payload,
                    console_stream=console_stream,
                    console_stream_label=console_stream_label,
                )
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
