from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import VoiceSynthesisResult


class VolcengineSeedTTSProvider:
    """Volcengine Seed TTS provider for direct role voice sample synthesis."""

    name = "volcengine"
    supports_direct_emotion_synthesis = True
    supports_local_voice_clone = False
    _SPEAKER_CATALOG_PATH = Path(__file__).with_name("seed_tts_speakers.json")
    _speaker_catalog_cache: list[dict[str, Any]] | None = None

    _AUDIO_PARAM_KEYS = {
        "emotion",
        "emotion_scale",
        "speech_rate",
        "loudness_rate",
        "bit_rate",
        "enable_timestamp",
    }
    _EMOTION_ALIASES = {
        "neutral": "normal",
        "tension": "tense",
        "calm": "normal",
        "asmr": "whisper",
    }
    _DEFAULT_EMOTION_INSTRUCTIONS: dict[str, dict[str, Any]] = {
        "normal": {
            "emotion": "neutral",
            "emotion_scale": 2,
            "speech_rate": 0,
            "loudness_rate": 0,
            "instruction": "自然、克制、口语化，像影视短剧对白，保持角色身份感。",
        },
        "tense": {
            "emotion": "tension",
            "emotion_scale": 4,
            "speech_rate": 6,
            "loudness_rate": -2,
            "instruction": "压低声音，呼吸略紧，像强忍情绪，语尾收住，不要夸张表演。",
        },
        "angry": {
            "emotion": "angry",
            "emotion_scale": 4,
            "speech_rate": 5,
            "loudness_rate": 4,
            "instruction": "愤怒但不要吼叫，语气锋利，停顿短促，压迫感强。",
        },
        "sad": {
            "emotion": "sad",
            "emotion_scale": 4,
            "speech_rate": -8,
            "loudness_rate": -4,
            "instruction": "低落、疲惫，语速稍慢，尾音略沉，带一点忍住不哭的感觉。",
        },
        "happy": {
            "emotion": "happy",
            "emotion_scale": 3,
            "speech_rate": 4,
            "loudness_rate": 2,
            "instruction": "轻松、有笑意，节奏明快，但不要广告腔。",
        },
        "whisper": {
            "emotion": "ASMR",
            "emotion_scale": 3,
            "speech_rate": -10,
            "loudness_rate": -8,
            "instruction": "贴近耳语，音量较低，气声更明显，但吐字保持清晰。",
        },
        "other": {
            "emotion": "neutral",
            "emotion_scale": 2,
            "speech_rate": 0,
            "loudness_rate": 0,
            "instruction": "按角色设定自然表达，根据文本上下文微调情绪。",
        },
    }

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://openspeech.bytedance.com").rstrip("/")
        self.endpoint = self._endpoint("unidirectional")
        self.model = (
            settings.models.get("speech_synthesis")
            or settings.models.get("seed_tts")
            or settings.models.get("tts_model")
            or "seed-tts-2.0"
        )
        self.fallback_model = (
            settings.models.get("speech_synthesis_fallback")
            or settings.models.get("seed_tts_fallback")
            or "seed-tts-1.0"
        )
        self.request_model = (
            settings.models.get("speech_synthesis_request_model")
            or settings.models.get("req_params_model")
            or settings.options.get("req_params_model")
        )
        self.resource_id = str(settings.options.get("tts_resource_id") or self.model)
        self.fallback_resource_id = str(settings.options.get("tts_fallback_resource_id") or self.fallback_model)
        self.api_key = self._seed_tts_api_key(settings)
        self.app_key = settings.secret("app_key_env") or settings.secret("app_id_env")
        self.access_key = settings.secret("access_key_env") or settings.secret("secret_key_env")
        self.sample_rate = int(settings.options.get("sample_rate", 24000))
        self.response_format = str(settings.options.get("response_format", "mp3")).lower()
        self.default_speaker = str(
            settings.options.get("default_speaker")
            or settings.options.get("default_voice_type")
            or "zh_male_m191_uranus_bigtts"
        )
        self.default_male_speaker = str(
            settings.options.get("default_male_speaker") or self.default_speaker
        )
        self.default_female_speaker = str(
            settings.options.get("default_female_speaker")
            or settings.options.get("default_speaker")
            or "zh_female_xiaohe_uranus_bigtts"
        )
        self.instruction_mode = str(settings.options.get("instruction_mode", "text_prefix")).lower()
        self.additions_as_json_string = bool(settings.options.get("additions_as_json_string", True))
        self.max_text_chars = int(settings.options.get("max_text_chars", 1024))

    @classmethod
    def available_speakers(cls) -> list[dict[str, Any]]:
        if cls._speaker_catalog_cache is None:
            payload = json.loads(cls._SPEAKER_CATALOG_PATH.read_text(encoding="utf-8"))
            speakers = payload.get("speakers") if isinstance(payload, dict) else None
            if not isinstance(speakers, list):
                raise ValueError(f"Invalid Volcengine speaker catalog: {cls._SPEAKER_CATALOG_PATH}")
            cls._speaker_catalog_cache = [
                speaker
                for speaker in speakers
                if isinstance(speaker, dict) and speaker.get("voice_type")
            ]
        return [dict(speaker) for speaker in cls._speaker_catalog_cache]

    @classmethod
    def speaker_by_voice_type(cls, voice_type: str | None) -> dict[str, Any] | None:
        key = str(voice_type or "").strip()
        if not key:
            return None
        for speaker in cls.available_speakers():
            if speaker.get("voice_type") == key:
                return speaker
        return None

    @classmethod
    def available_speakers_for_prompt(cls) -> list[dict[str, Any]]:
        prompt_speakers: list[dict[str, Any]] = []
        for speaker in cls.available_speakers():
            base_fields = {
                "name": speaker.get("name"),
                "voice_type": speaker.get("voice_type"),
                "resource_id": speaker.get("resource_id"),
                "model_family": speaker.get("model_family"),
                "scene": speaker.get("scene"),
                "language": speaker.get("language"),
                "gender": speaker.get("gender"),
            }
            prompt_speaker: dict[str, Any] = {
                key: value
                for key, value in base_fields.items()
                if value is not None and value != ""
            }
            for key in ("abilities", "supported_emotions", "tags"):
                value = speaker.get(key)
                if value:
                    prompt_speaker[key] = value
            if speaker.get("emotion_capable"):
                prompt_speaker["emotion_capable"] = True
            if speaker.get("corresponding_2_0_voice"):
                prompt_speaker["corresponding_2_0_voice"] = speaker["corresponding_2_0_voice"]
            if speaker.get("supports_mix") is not None:
                prompt_speaker["supports_mix"] = speaker["supports_mix"]
            prompt_speakers.append(prompt_speaker)
        return prompt_speakers

    def _endpoint(self, operation: str) -> str:
        if self.base_url.endswith(f"/api/v3/tts/{operation}"):
            return self.base_url
        if self.base_url.endswith("/api/v3/tts"):
            return f"{self.base_url}/{operation}"
        return f"{self.base_url}/api/v3/tts/{operation}"

    @staticmethod
    def _seed_tts_api_key(settings: ProviderSettings) -> str | None:
        api_key_ref = settings.options.get("seed_tts_api_key_env")
        if api_key_ref:
            copied_settings = settings.model_copy(update={"api_key_env": str(api_key_ref)})
            return copied_settings.secret("api_key_env")
        return settings.secret("api_key_env")

    def _auth_headers(self, *, resource_id: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Request-Id": uuid4().hex,
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        elif self.app_key and self.access_key:
            headers["X-Api-App-Key"] = self.app_key
            headers["X-Api-App-Id"] = self.app_key
            headers["X-Api-Access-Key"] = self.access_key
        else:
            raise ProviderAuthError(
                "Missing Volcengine Seed TTS credentials. Set providers.volcengine.options.seed_tts_api_key_env "
                "or api_key_env, or app_id_env/app_key_env plus access_key_env."
            )
        if resource_id:
            headers["X-Api-Resource-Id"] = resource_id
        return headers

    def resolve_role_voice(
        self,
        *,
        role_id: str,
        role_name: str,
        role_intro: str | None = None,
        role_voice_summary: str | None = None,
        role_personality: str | None = None,
    ) -> str:
        role_speakers = self.settings.options.get("role_speakers")
        if isinstance(role_speakers, dict):
            for key in (role_id, role_name, f"{role_id}:default", "default"):
                if key and role_speakers.get(key):
                    return str(role_speakers[key])

        hint = " ".join(
            item
            for item in (role_name, role_intro, role_voice_summary, role_personality)
            if item
        )
        gender_hint = self._infer_gender_hint(hint)
        if gender_hint == "female":
            return self.default_female_speaker
        if gender_hint == "male":
            return self.default_male_speaker
        return self.default_speaker

    def resolve_voice_resource_id(self, voice_type: str | None) -> str:
        speaker = self.speaker_by_voice_type(voice_type)
        if speaker and speaker.get("resource_id"):
            return str(speaker["resource_id"])
        return self.resource_id

    def resolve_emotion_plan(self, emotion: str) -> dict[str, Any]:
        key = self._normalize_emotion_key(emotion)
        plan = dict(self._DEFAULT_EMOTION_INSTRUCTIONS.get(key) or self._DEFAULT_EMOTION_INSTRUCTIONS["other"])

        configured = self.settings.options.get("emotion_instructions")
        if isinstance(configured, dict):
            for candidate_key in (emotion, key):
                candidate = configured.get(candidate_key)
                if isinstance(candidate, dict):
                    plan.update(candidate)
                    break
        return plan

    def emotion_params_from_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in plan.items() if key in self._AUDIO_PARAM_KEYS and value is not None}

    def build_synthesis_payload(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        emotion = str(metadata.get("emotion") or "normal")
        plan = self.resolve_emotion_plan(emotion)
        metadata_emotion_params = metadata.get("emotion_params")
        if isinstance(metadata_emotion_params, dict):
            plan.update(metadata_emotion_params)
        if "emotion_instruction" in metadata:
            plan["instruction"] = metadata.get("emotion_instruction")

        response_format = str(metadata.get("response_format", self.response_format)).lower()
        sample_rate = int(metadata.get("sample_rate", self.sample_rate))
        request_model = metadata.get("req_params_model") or metadata.get("request_model") or self.request_model

        audio_params: dict[str, Any] = {
            "format": response_format,
            "sample_rate": sample_rate,
        }
        audio_params.update(self.emotion_params_from_plan(plan))

        metadata_audio_params = metadata.get("audio_params")
        if isinstance(metadata_audio_params, dict):
            audio_params.update(metadata_audio_params)

        instruction = str(plan.get("instruction") or "").strip()
        instruction_mode = str(metadata.get("instruction_mode") or self.instruction_mode).lower()
        additions = self._merged_additions(metadata)

        payload_text = self._text_with_instruction(
            text[: self.max_text_chars],
            instruction=instruction,
            mode=instruction_mode,
        )
        if instruction and instruction_mode == "additions":
            additions.setdefault("voice_instruction", instruction)

        req_params: dict[str, Any] = {
            "text": payload_text,
            "speaker": voice,
            "audio_params": audio_params,
        }
        if request_model:
            req_params["model"] = str(request_model)

        metadata_req_params = metadata.get("req_params")
        if isinstance(metadata_req_params, dict):
            extra_audio_params = metadata_req_params.get("audio_params")
            if isinstance(extra_audio_params, dict):
                audio_params.update(extra_audio_params)
            extra_additions = metadata_req_params.get("additions")
            if isinstance(extra_additions, dict):
                additions.update(extra_additions)
            for key, value in metadata_req_params.items():
                if key not in {"audio_params", "additions"}:
                    req_params[key] = value

        if instruction and instruction_mode == "req_params":
            req_params.setdefault("instruction", instruction)
        if additions:
            req_params["additions"] = (
                json.dumps(additions, ensure_ascii=False) if self.additions_as_json_string else additions
            )

        return {
            "user": {
                "uid": str(metadata.get("uid") or metadata.get("project_id") or "autodrama"),
            },
            "req_params": req_params,
        }

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        metadata = metadata or {}
        payload = self.build_synthesis_payload(voice=voice, text=text, metadata=metadata)
        req_params = payload["req_params"]
        resource_id = str(metadata.get("resource_id") or metadata.get("model") or self.resource_id)
        model = resource_id
        response_format = str(req_params["audio_params"].get("format") or self.response_format).lower()
        sample_rate = int(req_params["audio_params"].get("sample_rate") or self.sample_rate)
        headers = self._auth_headers(resource_id=resource_id)

        audio_chunks: list[bytes] = []
        response_headers: dict[str, str] = {}
        raw_events: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            try:
                async with client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                    response_headers = self._selected_response_headers(response)
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ProviderBadResponseError(
                            f"Volcengine speech synthesis failed with HTTP {response.status_code}: "
                            f"{body.decode('utf-8', errors='replace')[:500]}\n"
                            f"Raw Headers: {headers}"
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type.startswith("audio/") or content_type == "application/octet-stream":
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                audio_chunks.append(chunk)
                        raw_events.append({"content_type": content_type, "audio": "<binary audio omitted>"})
                    else:
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            event = self._parse_stream_event(line)
                            if event is None:
                                continue
                            raw_events.append(self._without_event_audio(event))
                            audio_value = self._event_audio_data(event)
                            if audio_value:
                                audio_chunks.append(base64.b64decode(audio_value))
                            if self._event_is_error(event):
                                raise ProviderBadResponseError(f"Volcengine speech synthesis error event: {event}")
            except httpx.ConnectError as exc:
                raise ProviderBadResponseError(
                    f"Volcengine connection failed before receiving an HTTP response. "
                    f"Check network/proxy/TLS settings for {self.endpoint}: {exc}"
                ) from exc

        if not audio_chunks:
            raise ProviderBadResponseError("Volcengine speech synthesis returned no audio chunks")

        audio_bytes = b"".join(audio_chunks)
        return VoiceSynthesisResult(
            provider=self.name,
            model=model,
            voice=voice,
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_sample_rate=sample_rate,
            audio_format=response_format,
            request_id=(
                response_headers.get("X-Tt-Logid")
                or response_headers.get("x-tt-logid")
                or response_headers.get("X-Api-Request-Id")
                or response_headers.get("x-api-request-id")
            ),
            usage={"chunk_count": len(audio_chunks), "byte_count": len(audio_bytes)},
            raw_response={
                "request_payload": payload,
                "request_headers": self._safe_headers(httpx.Headers(headers)),
                "events": raw_events,
                "response_headers": response_headers,
            },
        )

    def _merged_additions(self, metadata: dict[str, Any]) -> dict[str, Any]:
        additions: dict[str, Any] = {}
        configured = self.settings.options.get("additions") or self.settings.options.get("tts_additions")
        if isinstance(configured, dict):
            additions.update(configured)
        metadata_additions = metadata.get("additions")
        if isinstance(metadata_additions, dict):
            additions.update(metadata_additions)
        return additions

    @staticmethod
    def _text_with_instruction(text: str, *, instruction: str, mode: str) -> str:
        if not instruction or mode in {"none", "off", "disabled", "additions", "req_params"}:
            return text
        if mode == "text_prefix":
            return f"[语音指令：{instruction}]\n{text}"
        if mode == "qa_prefix":
            return f"请按以下语音指令朗读：{instruction}\n朗读文本：{text}"
        return text

    @classmethod
    def _normalize_emotion_key(cls, emotion: str) -> str:
        key = str(emotion or "normal").strip().lower()
        return cls._EMOTION_ALIASES.get(key, key or "normal")

    @staticmethod
    def _infer_gender_hint(text: str) -> str | None:
        normalized = text.lower()
        words = set(normalized.replace("/", " ").replace(",", " ").replace(";", " ").split())
        female_words = {"female", "woman", "girl"}
        male_words = {"male", "man", "boy"}
        female_tokens = (
            "女",
            "她",
            "母亲",
            "妈妈",
            "妻",
            "姑娘",
            "小姐",
            "姐姐",
            "妹妹",
            "夫人",
            "女孩",
            "少女",
        )
        male_tokens = (
            "男",
            "他",
            "父亲",
            "爸爸",
            "丈夫",
            "哥哥",
            "弟弟",
            "先生",
            "男孩",
            "少年",
            "叔",
        )
        female_score = sum(1 for token in female_words if token in words)
        female_score += sum(1 for token in female_tokens if token in normalized)
        male_score = sum(1 for token in male_words if token in words)
        male_score += sum(1 for token in male_tokens if token in normalized)
        if female_score > male_score:
            return "female"
        if male_score > female_score:
            return "male"
        return None

    @staticmethod
    def _parse_stream_event(line: str) -> dict[str, Any] | None:
        value = line.strip()
        if value.startswith("data:"):
            value = value[5:].strip()
        if not value or value == "[DONE]":
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _event_audio_data(event: dict[str, Any]) -> str | None:
        for key in ("data", "audio", "audio_data"):
            value = event.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = value.get("data") or value.get("audio") or value.get("content")
                if isinstance(nested, str):
                    return nested
        result = event.get("result")
        if isinstance(result, dict):
            nested = result.get("data") or result.get("audio") or result.get("audio_data")
            if isinstance(nested, str):
                return nested
        return None

    @staticmethod
    def _event_is_error(event: dict[str, Any]) -> bool:
        code = event.get("code")
        if code in {None, 0, "0", 20000000, "20000000"}:
            return False
        return True

    @staticmethod
    def _without_event_audio(event: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(event)
        for key in ("data", "audio", "audio_data", "result"):
            value = sanitized.get(key)
            if isinstance(value, str):
                sanitized[key] = "<base64 audio omitted>"
            elif isinstance(value, dict):
                nested = dict(value)
                for nested_key in ("data", "audio", "audio_data", "content"):
                    if nested_key in nested:
                        nested[nested_key] = "<base64 audio omitted>"
                sanitized[key] = nested
        return sanitized

    @staticmethod
    def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
        return {
            key: ("<secret omitted>" if key.lower() in {"x-api-key", "x-api-access-key"} else value)
            for key, value in headers.items()
            if key.lower().startswith("x-api-")
        }

    @staticmethod
    def _selected_response_headers(response: httpx.Response) -> dict[str, str]:
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() in {"x-tt-logid", "x-api-request-id", "content-type"}
        }
