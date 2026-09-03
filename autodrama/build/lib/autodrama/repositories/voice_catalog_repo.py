from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from autodrama.config import Settings
from autodrama.core.voice_catalog import VoiceCatalogManifest, VoiceCatalogSampleItem, VoiceCatalogVoiceItem
from autodrama.repositories.project_repo import ProjectRepository


class VoiceCatalogRepository:
    """Persistence helper for reusable provider voice catalogs."""

    FULL_SAMPLE_EMOTIONS = ["normal", "angry", "sad", "happy", "low"]
    DEFAULT_SAMPLE_EMOTIONS = ["normal"]
    DEFAULT_SAMPLE_TEXTS = {
        "normal": "今天的事先这样吧，等你想清楚了，我们再谈。",
        "angry": "我已经说过很多遍了，不要再这样做。",
        "sad": "我只是没想到，事情最后会变成这样。",
        "happy": "太好了，我就知道你一定能做到。",
        "low": "这件事不要声张，先听我说完。",
    }
    DEFAULT_SAMPLE_TEXTS_BY_LANGUAGE = {
        "en": {
            "normal": "Let's leave it there for today. When you have thought it through, we can talk again.",
            "angry": "I have told you many times. Do not do this again.",
            "sad": "I just never expected things to end up like this.",
            "happy": "That's wonderful. I knew you could make it happen.",
            "low": "Keep this quiet for now. Listen to me before you say anything.",
        },
        "ja": {
            "normal": "今日のところはここまでにしましょう。考えがまとまったら、また話しましょう。",
            "angry": "何度も言ったはずです。もう二度とこんなことはしないでください。",
            "sad": "まさか最後にこんなことになるなんて、思ってもいませんでした。",
            "happy": "よかった。あなたならきっとできると信じていました。",
            "low": "このことはまだ誰にも言わないでください。まず私の話を最後まで聞いて。",
        },
        "es": {
            "normal": "Dejemos esto por hoy. Cuando lo hayas pensado bien, volveremos a hablar.",
            "angry": "Te lo he dicho muchas veces. No vuelvas a hacerlo.",
            "sad": "Nunca imaginé que las cosas terminarían así.",
            "happy": "Qué bien. Sabía que podrías lograrlo.",
            "low": "No digas nada sobre esto todavía. Escúchame antes de responder.",
        },
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_settings(cls, settings: Settings) -> "VoiceCatalogRepository":
        return cls(settings.output.root_dir.parent / ".assets" / "voice_catalog")

    @staticmethod
    def _path_key(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[\\/:\*\?\"<>\|]+", "_", text)
        return text or "unknown"

    def catalog_dir(self, provider: str, model: str) -> Path:
        return self.root / self._path_key(provider) / self._path_key(model)

    def manifest_path(self, provider: str, model: str) -> Path:
        return self.catalog_dir(provider, model) / "manifest.json"

    def sample_dir(self, provider: str, model: str, voice_type: str) -> Path:
        return self.catalog_dir(provider, model) / "samples" / self._path_key(voice_type)

    def sample_manifest_path(self, provider: str, model: str, voice_type: str) -> Path:
        return self.sample_dir(provider, model, voice_type) / "manifest.json"

    def profile_dir(self, provider: str, model: str) -> Path:
        return self.catalog_dir(provider, model) / "profiles"

    def voice_profile_path(self, provider: str, model: str, voice_type: str) -> Path:
        return self.profile_dir(provider, model) / f"{self._path_key(voice_type)}.json"

    def sample_path(
        self,
        *,
        provider: str,
        model: str,
        voice_type: str,
        emotion: str,
        response_format: str = "mp3",
    ) -> Path:
        extension = str(response_format or "mp3").lower().lstrip(".")
        if extension not in {"mp3", "wav", "m4a", "aac", "ogg", "opus", "pcm"}:
            extension = "mp3"
        return self.sample_dir(provider, model, voice_type) / f"{self._path_key(emotion)}.{extension}"

    @staticmethod
    def sample_hash(
        *,
        provider: str,
        model: str,
        voice_type: str,
        emotion: str,
        sample_text: str,
        emotion_params: dict[str, Any] | None = None,
        response_format: str | None = None,
        sample_rate: int | None = None,
    ) -> str:
        payload = {
            "provider": provider,
            "model": model,
            "voice_type": voice_type,
            "emotion": emotion,
            "sample_text": sample_text,
            "emotion_params": emotion_params or {},
            "response_format": response_format,
            "sample_rate": sample_rate,
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def load_manifest(self, provider: str, model: str) -> VoiceCatalogManifest:
        path = self.manifest_path(provider, model)
        return VoiceCatalogManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def try_load_manifest(self, provider: str, model: str) -> VoiceCatalogManifest | None:
        path = self.manifest_path(provider, model)
        if not path.exists():
            return None
        return self.load_manifest(provider, model)

    def save_manifest(self, manifest: VoiceCatalogManifest) -> Path:
        path = self.manifest_path(manifest.provider, manifest.model)
        ProjectRepository.write_json(path, manifest)
        return path

    def save_voice_sample_manifest(self, manifest: VoiceCatalogManifest, voice: VoiceCatalogVoiceItem) -> Path:
        path = self.sample_manifest_path(manifest.provider, manifest.model, voice.voice_type)
        ProjectRepository.write_json(
            path,
            {
                "schema_version": manifest.schema_version,
                "catalog_version": manifest.catalog_version,
                "provider": manifest.provider,
                "model": manifest.model,
                "voice_label": voice.voice_label,
                "voice_type": voice.voice_type,
                "voice_resource_id": voice.voice_resource_id,
                "voice_model_family": voice.voice_model_family,
                "voice_catalog_key": voice.voice_catalog_key,
                "samples": {
                    emotion: sample.model_dump(mode="json")
                    for emotion, sample in sorted(voice.samples.items())
                },
            },
        )
        return path

    def save_voice_profile(
        self,
        manifest: VoiceCatalogManifest,
        voice: VoiceCatalogVoiceItem,
        *,
        judge_name: str,
        judge_model: str | None,
        profile_prompt_version: str,
        sample_refs: list[dict[str, Any]],
    ) -> Path:
        path = self.voice_profile_path(manifest.provider, manifest.model, voice.voice_type)
        ProjectRepository.write_json(
            path,
            {
                "schema_version": manifest.schema_version,
                "catalog_version": manifest.catalog_version,
                "provider": manifest.provider,
                "model": manifest.model,
                "voice_label": voice.voice_label,
                "voice_type": voice.voice_type,
                "voice_resource_id": voice.voice_resource_id,
                "voice_model_family": voice.voice_model_family,
                "voice_catalog_key": voice.voice_catalog_key,
                "official": voice.official,
                "sample_emotions": manifest.sample_emotions,
                "sample_refs": sample_refs,
                "judge": {
                    "name": judge_name,
                    "model": judge_model,
                    "profile_prompt_version": profile_prompt_version,
                },
                "profile_hash": voice.profile_hash,
                "omni_profile": (
                    voice.omni_profile.model_dump(mode="json")
                    if voice.omni_profile is not None
                    else None
                ),
            },
        )
        return path

    @staticmethod
    def manifest_hash(manifest: VoiceCatalogManifest) -> str:
        payload = manifest.model_dump(mode="json")
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @staticmethod
    def voice_by_type(manifest: VoiceCatalogManifest, voice_type: str | None) -> VoiceCatalogVoiceItem | None:
        key = str(voice_type or "").strip()
        if not key:
            return None
        for voice in manifest.voices:
            if voice.voice_type == key:
                return voice
        return None

    @staticmethod
    def voices_by_label(manifest: VoiceCatalogManifest, voice_label: str | None) -> list[VoiceCatalogVoiceItem]:
        key = str(voice_label or "").strip().casefold()
        if not key:
            return []
        return [voice for voice in manifest.voices if voice.voice_label.casefold() == key]

    @staticmethod
    def duplicate_labels(manifest: VoiceCatalogManifest) -> dict[str, list[str]]:
        by_label: dict[str, list[str]] = {}
        display_labels: dict[str, str] = {}
        for voice in manifest.voices:
            key = voice.voice_label.casefold()
            display_labels.setdefault(key, voice.voice_label)
            by_label.setdefault(key, []).append(voice.voice_type)
        return {
            display_labels[key]: voice_types
            for key, voice_types in by_label.items()
            if len(voice_types) > 1
        }

    @classmethod
    def build_manifest_from_speakers(
        cls,
        *,
        provider: str,
        model: str,
        speakers: list[dict[str, Any]],
        catalog_version: str | None = None,
        sample_emotions: list[str] | None = None,
    ) -> VoiceCatalogManifest:
        voices: list[VoiceCatalogVoiceItem] = []
        for speaker in speakers:
            voice_type = str(speaker.get("voice_type") or "").strip()
            if not voice_type:
                continue
            voice_label = str(speaker.get("name") or speaker.get("voice_label") or voice_type).strip()
            resource_id = speaker.get("resource_id") or speaker.get("voice_resource_id")
            model_family = speaker.get("model_family") or speaker.get("voice_model_family")
            voices.append(
                VoiceCatalogVoiceItem(
                    voice_label=voice_label,
                    voice_type=voice_type,
                    voice_resource_id=str(resource_id) if resource_id else None,
                    voice_model_family=str(model_family) if model_family else None,
                    voice_catalog_key=f"{provider}:{model}:{voice_type}",
                    official=dict(speaker),
                    samples={},
                    omni_profile=None,
                )
            )
        version = catalog_version or cls._catalog_version(provider=provider, model=model, speakers=voices)
        return VoiceCatalogManifest(
            catalog_version=version,
            provider=provider,
            model=model,
            sample_emotions=sample_emotions or cls.DEFAULT_SAMPLE_EMOTIONS,
            voices=voices,
        )

    @staticmethod
    def _catalog_version(*, provider: str, model: str, speakers: list[VoiceCatalogVoiceItem]) -> str:
        payload = [
            {
                "voice_label": voice.voice_label,
                "voice_type": voice.voice_type,
                "voice_resource_id": voice.voice_resource_id,
                "voice_model_family": voice.voice_model_family,
            }
            for voice in speakers
        ]
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(data.encode("utf-8")).hexdigest()[:12]
        return f"{provider}:{model}:speaker-catalog:{digest}"

    def sample_item_for_voice(
        self,
        *,
        provider: str,
        model: str,
        voice_type: str,
        voice_resource_id: str | None,
        emotion: str,
        audio_params: dict[str, Any] | None = None,
        response_format: str = "mp3",
        sample_rate: int | None = None,
        sample_text: str | None = None,
    ) -> VoiceCatalogSampleItem:
        sample_text = sample_text or self.DEFAULT_SAMPLE_TEXTS.get(emotion, self.DEFAULT_SAMPLE_TEXTS["normal"])
        sample_path = self.sample_path(
            provider=provider,
            model=model,
            voice_type=voice_type,
            emotion=emotion,
            response_format=response_format,
        )
        return VoiceCatalogSampleItem(
            emotion=emotion,
            sample_text=sample_text,
            asset_path=str(sample_path.relative_to(self.root)).replace("\\", "/"),
            sample_hash=self.sample_hash(
                provider=provider,
                model=model,
                voice_type=voice_type,
                emotion=emotion,
                sample_text=sample_text,
                emotion_params=audio_params or {},
                response_format=response_format,
                sample_rate=sample_rate,
            ),
            provider=provider,
            model=model,
            voice_type=voice_type,
            voice_resource_id=voice_resource_id,
            audio_params=audio_params or {},
        )


__all__ = ["VoiceCatalogRepository"]
