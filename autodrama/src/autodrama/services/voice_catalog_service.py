from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from autodrama.core.schemas import Role
from autodrama.core.voice_catalog import (
    RoleVoiceSelectionItem,
    VoiceCandidateItem,
    VoiceCatalogManifest,
    VoiceCatalogProfile,
    VoiceCatalogVoiceItem,
    VoiceSelectShortlistOutput,
)
from autodrama.providers.base import AssetRef, AudioJudgeLLM
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.services.audio_duration import probe_audio_duration_seconds
from autodrama.utils.prompts import PromptStore


class VoiceCatalogService:
    """Project-facing voice catalog selection helpers.

    The service owns provider manifest persistence, reusable sample/profile
    generation, and conversion between compact catalog records and project-level
    voice selection outputs.
    """

    def __init__(self, repo: VoiceCatalogRepository) -> None:
        self.repo = repo

    PROFILE_PROMPT_VERSION = "voice_catalog_profile.natural_sketch.v1"
    PROFILE_BUILD_CONCURRENCY = 5

    @staticmethod
    def provider_identity(provider: Any) -> tuple[str, str]:
        provider_name = str(getattr(provider, "name", None) or "unknown")
        model = (
            getattr(provider, "resource_id", None)
            or getattr(provider, "model", None)
            or getattr(provider, "target_model", None)
            or "unknown"
        )
        return provider_name, str(model)

    @staticmethod
    def _available_speakers(provider: Any) -> list[dict[str, Any]]:
        for method_name in ("available_speakers", "available_speakers_for_prompt"):
            getter = getattr(provider, method_name, None)
            if not callable(getter):
                continue
            speakers = getter()
            if isinstance(speakers, list):
                return [dict(speaker) for speaker in speakers if isinstance(speaker, dict)]
        return []

    def load_or_bootstrap_manifest(
        self,
        provider: Any,
        *,
        force_bootstrap: bool = False,
    ) -> VoiceCatalogManifest:
        provider_name, model = self.provider_identity(provider)
        if not force_bootstrap:
            manifest = self.repo.try_load_manifest(provider_name, model)
            if manifest is not None:
                return manifest

        speakers = self._available_speakers(provider)
        manifest = self.repo.build_manifest_from_speakers(
            provider=provider_name,
            model=model,
            speakers=speakers,
        )
        self.repo.save_manifest(manifest)
        return manifest

    async def sync_official_preset_catalog(
        self,
        provider: Any,
        *,
        refresh_manifest: bool = False,
        force_samples: bool = False,
        download_trials: bool = True,
        download_concurrency: int = 8,
    ) -> VoiceCatalogManifest:
        """Sync a provider's official preset voices and cache their trial audio.

        A previously saved catalog remains usable when the remote list endpoint is
        temporarily unavailable. A first-time sync still fails clearly because
        there is no safe voice_id fallback to invent.
        """

        provider_name, model = self.provider_identity(provider)
        existing = self.repo.try_load_manifest(provider_name, model)
        manifest = existing
        list_voices = getattr(provider, "list_preset_voices", None)
        if manifest is None or refresh_manifest:
            if not callable(list_voices):
                raise ValueError(f"Provider {provider_name} does not expose an official preset voice list")
            try:
                speakers = await list_voices()
            except Exception as exc:
                if existing is None:
                    raise RuntimeError(
                        f"Cannot refresh {provider_name} official voice catalog and no local cache exists: "
                        f"{type(exc).__name__}: {exc or 'no detail returned'}"
                    ) from exc
                manifest = existing
            else:
                if not speakers:
                    if existing is None:
                        raise ValueError(f"Provider {provider_name} returned an empty official voice catalog")
                    manifest = existing
                else:
                    refreshed = self.repo.build_manifest_from_speakers(
                        provider=provider_name,
                        model=model,
                        speakers=speakers,
                    )
                    manifest = self._merge_existing_voice_assets(refreshed, existing)
                    self.repo.save_manifest(manifest)

        if manifest is None:
            raise ValueError(f"No official voice catalog is available for {provider_name}:{model}")
        provider_settings = getattr(provider, "settings", None)
        provider_options = getattr(provider_settings, "options", {}) if provider_settings is not None else {}
        if download_trials:
            manifest = await self.build_official_trial_samples(
                manifest,
                force_samples=force_samples,
                concurrency=download_concurrency,
                trust_env=bool(provider_options.get("httpx_trust_env", True)),
            )
        if bool(provider_options.get("synthesize_short_official_voice_trials", False)):
            manifest = await self.build_short_official_tts_samples(
                provider,
                manifest,
                min_duration_seconds=float(provider_options.get("official_voice_min_sample_seconds", 5.0)),
                target_duration_seconds=float(provider_options.get("official_voice_tts_target_seconds", 8.0)),
                force_samples=force_samples,
            )
        return manifest

    @staticmethod
    def _merge_existing_voice_assets(
        refreshed: VoiceCatalogManifest,
        existing: VoiceCatalogManifest | None,
    ) -> VoiceCatalogManifest:
        if existing is None:
            return refreshed
        old_by_type = {voice.voice_type: voice for voice in existing.voices}
        voices: list[VoiceCatalogVoiceItem] = []
        for voice in refreshed.voices:
            old = old_by_type.get(voice.voice_type)
            if old is None:
                voices.append(voice)
                continue
            voices.append(
                voice.model_copy(
                    update={
                        "samples": dict(old.samples),
                        "omni_profile": old.omni_profile,
                        "profile_hash": old.profile_hash,
                    }
                )
            )
        return refreshed.model_copy(update={"voices": voices})

    @staticmethod
    def _trial_audio_extension(response: httpx.Response) -> str:
        content_type = str(response.headers.get("content-type") or "").lower()
        if "wav" in content_type:
            return "wav"
        if "ogg" in content_type:
            return "ogg"
        if "aac" in content_type:
            return "aac"
        if "mp4" in content_type or "m4a" in content_type:
            return "m4a"
        return "mp3"

    async def build_official_trial_samples(
        self,
        manifest: VoiceCatalogManifest,
        *,
        force_samples: bool = False,
        concurrency: int = 8,
        trust_env: bool = True,
    ) -> VoiceCatalogManifest:
        """Download provider-supplied trial clips into the reusable local catalog."""

        semaphore = asyncio.Semaphore(max(1, min(20, int(concurrency))))

        async def download_one(client: httpx.AsyncClient, voice: VoiceCatalogVoiceItem) -> VoiceCatalogVoiceItem:
            updated = voice.model_copy(deep=True)
            existing = updated.samples.get("official_trial")
            if existing is not None and not force_samples:
                existing_path = self.repo.root / Path(existing.asset_path)
                if existing_path.exists() and existing_path.stat().st_size > 0:
                    return updated
            trial_url = str(updated.official.get("trial_url") or "").strip()
            if not trial_url.startswith(("http://", "https://")):
                return updated
            async with semaphore:
                try:
                    response = await client.get(trial_url, follow_redirects=True)
                    response.raise_for_status()
                    content_type = str(response.headers.get("content-type") or "").lower()
                    if "text/html" in content_type or not response.content:
                        return updated
                except Exception:
                    return updated
            extension = self._trial_audio_extension(response)
            sample_text = f"可灵官方试听音频：{updated.voice_label}"
            sample = self.repo.sample_item_for_voice(
                provider=manifest.provider,
                model=manifest.model,
                voice_type=updated.voice_type,
                voice_resource_id=updated.voice_resource_id,
                emotion="official_trial",
                sample_text=sample_text,
                audio_params={"source": "official_trial"},
                response_format=extension,
            )
            sample_path = self.repo.root / Path(sample.asset_path)
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sample_path.write_bytes(response.content)
            updated.samples["official_trial"] = sample
            current_normal = updated.samples.get("normal")
            if current_normal is None or current_normal.audio_params.get("source") == "official_trial":
                updated.samples["normal"] = sample.model_copy(update={"emotion": "normal"})
            return updated

        timeout = httpx.Timeout(60.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
            updated_voices = await asyncio.gather(
                *(download_one(client, voice) for voice in manifest.voices)
            )
        manifest = manifest.model_copy(update={"sample_emotions": ["normal"], "voices": updated_voices})
        for voice in manifest.voices:
            self.repo.save_voice_sample_manifest(manifest, voice)
        self.repo.save_manifest(manifest)
        return manifest

    async def build_short_official_tts_samples(
        self,
        provider: Any,
        manifest: VoiceCatalogManifest,
        *,
        min_duration_seconds: float = 5.0,
        target_duration_seconds: float = 8.0,
        force_samples: bool = False,
    ) -> VoiceCatalogManifest:
        """Replace too-short trial samples with speech synthesized by the same Kling voice_id."""

        synthesize = getattr(provider, "synthesize_speech", None)
        if not callable(synthesize):
            raise ValueError(f"Provider {manifest.provider} cannot synthesize official voice samples")
        provider_settings = getattr(provider, "settings", None)
        provider_options = getattr(provider_settings, "options", {}) if provider_settings is not None else {}
        raw_tts_map = provider_options.get("official_voice_tts_map") or {}
        if not isinstance(raw_tts_map, dict):
            raise ValueError("Kling official_voice_tts_map must be an object keyed by Omni voice_id")
        sample_text = "今天我们在这里认真说一段完整的话，让你听清声音的气息、节奏、语调和情绪变化。"
        updated_voices: list[VoiceCatalogVoiceItem] = []
        for voice in manifest.voices:
            updated = voice.model_copy(deep=True)
            trial = updated.samples.get("official_trial") or updated.samples.get("normal")
            if trial is None:
                updated_voices.append(updated)
                continue
            trial_path = self.repo.root / Path(trial.asset_path)
            trial_duration = probe_audio_duration_seconds(trial_path)
            if trial_duration is None or trial_duration >= min_duration_seconds:
                updated_voices.append(updated)
                continue
            tts_voice_id = str(raw_tts_map.get(updated.voice_type) or "").strip()
            if not tts_voice_id:
                updated.official["tts_sample_error"] = (
                    "No official Omni voice_id to /v1/audio/tts voice_id mapping is configured"
                )
                updated_voices.append(updated)
                continue
            existing_normal = updated.samples.get("normal")
            if existing_normal is not None and not force_samples:
                existing_path = self.repo.root / Path(existing_normal.asset_path)
                existing_duration = probe_audio_duration_seconds(existing_path)
                if (
                    existing_normal.audio_params.get("source") == "official_tts"
                    and existing_duration is not None
                    and existing_duration >= min_duration_seconds
                ):
                    updated_voices.append(updated)
                    continue
            try:
                result = await synthesize(
                    voice=tts_voice_id,
                    text=sample_text,
                    metadata={
                        "node_name": "voice_catalog_official_tts",
                        "voice_language": str(updated.official.get("language") or "zh"),
                        "voice_speed": 1.0,
                        "target_duration_seconds": target_duration_seconds,
                    },
                )
                if not result.audio_data:
                    raise ValueError("TTS returned no audio data")
                response_format = str(result.audio_format or "mp3").lower()
                sample = self.repo.sample_item_for_voice(
                    provider=manifest.provider,
                    model=manifest.model,
                    voice_type=updated.voice_type,
                    voice_resource_id=updated.voice_resource_id,
                    emotion="normal",
                    sample_text=sample_text,
                    audio_params={
                        "source": "official_tts",
                        "tts_voice_id": tts_voice_id,
                        "target_duration_seconds": target_duration_seconds,
                        "trial_duration_seconds": trial_duration,
                    },
                    response_format=response_format,
                )
                sample_path = self.repo.root / Path(sample.asset_path)
                sample_path.parent.mkdir(parents=True, exist_ok=True)
                sample_path.write_bytes(base64.b64decode(self._base64_payload(result.audio_data)))
                generated_duration = probe_audio_duration_seconds(sample_path)
                if generated_duration is None or generated_duration < min_duration_seconds:
                    raise ValueError(
                        f"generated sample duration {generated_duration!r} is below {min_duration_seconds:.1f}s"
                    )
                updated.samples["normal"] = sample.model_copy(
                    update={
                        "audio_params": {
                            **sample.audio_params,
                            "duration_seconds": generated_duration,
                            "request_id": result.request_id,
                        }
                    }
                )
                updated.official.pop("tts_sample_error", None)
            except Exception as exc:
                updated.official["tts_sample_error"] = f"{type(exc).__name__}: {exc or 'no detail returned'}"
            updated_voices.append(updated)

        manifest = manifest.model_copy(update={"voices": updated_voices})
        for voice in manifest.voices:
            self.repo.save_voice_sample_manifest(manifest, voice)
        self.repo.save_manifest(manifest)
        return manifest

    @staticmethod
    def _base64_payload(data: str) -> str:
        if data.startswith("data:") and ";base64," in data:
            return data.split(";base64,", 1)[1]
        return data

    @staticmethod
    def _emotion_plan(provider: Any, emotion: str) -> tuple[str | None, dict[str, Any]]:
        resolver = getattr(provider, "resolve_emotion_plan", None)
        plan = resolver(emotion) if callable(resolver) else {}
        if not isinstance(plan, dict):
            plan = {}
        instruction_value = plan.get("instruction")
        instruction = str(instruction_value).strip() if instruction_value is not None else None
        if instruction == "":
            instruction = None
        params_resolver = getattr(provider, "emotion_params_from_plan", None)
        if callable(params_resolver):
            params = params_resolver(plan)
        else:
            params = {key: value for key, value in plan.items() if key != "instruction"}
        return instruction, dict(params)

    @staticmethod
    def _voice_language_key(voice: VoiceCatalogVoiceItem) -> str:
        language = str(voice.official.get("language") or "").casefold()
        voice_type = voice.voice_type.casefold()
        label = voice.voice_label.casefold()
        text = " ".join([language, label])
        if voice_type.startswith(("zh_", "icl_zh_")):
            return "zh"
        if voice_type.startswith(("en_", "icl_en_")):
            return "en"
        if voice_type.startswith(("ja_", "jp_", "icl_ja_", "icl_jp_")):
            return "ja"
        if voice_type.startswith(("es_", "icl_es_")):
            return "es"
        if "中文" in text or "汉语" in text or "chinese" in text:
            return "zh"
        if "英语" in text or "english" in text:
            return "en"
        if "日语" in text or "日文" in text or "japanese" in text:
            return "ja"
        if "西语" in text or "西班牙" in text or "spanish" in text:
            return "es"
        return "zh"

    @classmethod
    def _sample_text_for_voice(cls, voice: VoiceCatalogVoiceItem, emotion: str) -> str:
        language_key = cls._voice_language_key(voice)
        texts = VoiceCatalogRepository.DEFAULT_SAMPLE_TEXTS_BY_LANGUAGE.get(language_key)
        if texts is not None:
            return texts.get(emotion, texts["normal"])
        return VoiceCatalogRepository.DEFAULT_SAMPLE_TEXTS.get(
            emotion,
            VoiceCatalogRepository.DEFAULT_SAMPLE_TEXTS["normal"],
        )

    @staticmethod
    def with_sample_emotions(
        manifest: VoiceCatalogManifest,
        sample_emotions: list[str],
    ) -> VoiceCatalogManifest:
        normalized = []
        for emotion in sample_emotions:
            value = str(emotion or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        if not normalized:
            normalized = list(VoiceCatalogRepository.DEFAULT_SAMPLE_EMOTIONS)

        updated_voices = [
            voice.model_copy(
                update={
                    "samples": {
                        emotion: sample
                        for emotion, sample in voice.samples.items()
                        if emotion in normalized
                    }
                }
            )
            for voice in manifest.voices
        ]
        return manifest.model_copy(update={"sample_emotions": normalized, "voices": updated_voices})

    async def build_samples(
        self,
        provider: Any,
        manifest: VoiceCatalogManifest,
        *,
        force_samples: bool = False,
        voice_types: set[str] | None = None,
        sample_emotions: list[str] | None = None,
    ) -> VoiceCatalogManifest:
        manifest = self.with_sample_emotions(
            manifest,
            sample_emotions or list(VoiceCatalogRepository.DEFAULT_SAMPLE_EMOTIONS),
        )
        self.repo.save_manifest(manifest)
        response_format = str(getattr(provider, "response_format", "mp3") or "mp3").lower()
        sample_rate = int(getattr(provider, "sample_rate", 24000) or 24000)
        updated_voices: list[VoiceCatalogVoiceItem] = []

        for voice in manifest.voices:
            if voice_types and voice.voice_type not in voice_types:
                updated_voices.append(voice)
                continue
            updated_voice = voice.model_copy(deep=True)
            for emotion in manifest.sample_emotions:
                instruction, emotion_params = self._emotion_plan(provider, emotion)
                sample_text = self._sample_text_for_voice(voice, emotion)
                sample = self.repo.sample_item_for_voice(
                    provider=manifest.provider,
                    model=manifest.model,
                    voice_type=voice.voice_type,
                    voice_resource_id=voice.voice_resource_id,
                    emotion=emotion,
                    sample_text=sample_text,
                    audio_params=emotion_params,
                    response_format=response_format,
                    sample_rate=sample_rate,
                )
                sample_path = self.repo.root / Path(sample.asset_path)
                existing = updated_voice.samples.get(emotion)
                should_skip = (
                    not force_samples
                    and existing is not None
                    and existing.sample_hash == sample.sample_hash
                    and sample_path.exists()
                    and sample_path.stat().st_size > 0
                )
                if should_skip:
                    continue

                try:
                    result = await provider.synthesize_speech(
                        voice=voice.voice_type,
                        text=sample.sample_text,
                        metadata={
                            "node_name": "voice_catalog_build",
                            "catalog_version": manifest.catalog_version,
                            "voice_catalog_key": voice.voice_catalog_key,
                            "voice_label": voice.voice_label,
                            "voice_type": voice.voice_type,
                            "emotion": emotion,
                            "emotion_instruction": instruction,
                            "emotion_params": emotion_params,
                            "resource_id": voice.voice_resource_id,
                            "response_format": response_format,
                            "sample_rate": sample_rate,
                        },
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "voice-catalog sample generation failed for "
                        f"{voice.voice_type}/{emotion} ({voice.voice_label}): {exc}"
                    ) from exc
                if not result.audio_data:
                    raise ValueError(f"voice-catalog sample for {voice.voice_type}/{emotion} returned no audio data")
                sample_path.parent.mkdir(parents=True, exist_ok=True)
                sample_path.write_bytes(base64.b64decode(self._base64_payload(result.audio_data)))
                updated_voice.samples[emotion] = sample

            self.repo.save_voice_sample_manifest(manifest, updated_voice)
            updated_voices.append(updated_voice)
            manifest = manifest.model_copy(update={"voices": updated_voices + manifest.voices[len(updated_voices):]})
            self.repo.save_manifest(manifest)

        manifest = manifest.model_copy(update={"voices": updated_voices})
        self.repo.save_manifest(manifest)
        return manifest

    @staticmethod
    def profile_hash(
        voice: VoiceCatalogVoiceItem,
        *,
        judge_name: str,
        judge_model: str | None = None,
        profile_prompt_version: str = PROFILE_PROMPT_VERSION,
    ) -> str:
        payload = {
            "voice_label": voice.voice_label,
            "voice_type": voice.voice_type,
            "voice_resource_id": voice.voice_resource_id,
            "official": voice.official,
            "samples": {
                emotion: sample.sample_hash
                for emotion, sample in sorted(voice.samples.items())
            },
            "judge_name": judge_name,
            "judge_model": judge_model,
            "profile_prompt_version": profile_prompt_version,
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @staticmethod
    def profile_sample_ref_payload(refs: list[AssetRef]) -> list[dict[str, Any]]:
        return [
            {
                "id": ref.id,
                "emotion": ref.metadata.get("emotion"),
                "sample_text": ref.metadata.get("sample_text"),
                "path": ref.path,
            }
            for ref in refs
        ]

    def sample_refs_for_voice(self, voice: VoiceCatalogVoiceItem, sample_emotions: list[str]) -> list[AssetRef]:
        refs: list[AssetRef] = []
        for emotion in sample_emotions:
            sample = voice.samples.get(emotion)
            if sample is None:
                continue
            sample_path = self.repo.root / Path(sample.asset_path)
            if not sample_path.exists() or not sample_path.is_file():
                continue
            refs.append(
                AssetRef(
                    id=f"{voice.voice_type}_{emotion}",
                    type="audio",
                    path=str(sample_path),
                    metadata={
                        "voice_label": voice.voice_label,
                        "voice_type": voice.voice_type,
                        "emotion": emotion,
                        "sample_text": sample.sample_text,
                    },
                )
            )
        return refs

    async def build_profiles(
        self,
        judge: AudioJudgeLLM,
        manifest: VoiceCatalogManifest,
        *,
        force_profiles: bool = False,
        voice_types: set[str] | None = None,
        prompts: PromptStore | None = None,
    ) -> VoiceCatalogManifest:
        prompts = prompts or PromptStore()
        judge_name = str(getattr(judge, "name", "unknown"))
        judge_model = str(getattr(judge, "model", "") or "")
        updated_voices: list[VoiceCatalogVoiceItem | None] = [None] * len(manifest.voices)
        profile_jobs: list[dict[str, Any]] = []

        for index, voice in enumerate(manifest.voices):
            if voice_types and voice.voice_type not in voice_types:
                updated_voices[index] = voice
                continue
            updated_voice = voice.model_copy(deep=True)
            profile_hash = self.profile_hash(
                updated_voice,
                judge_name=judge_name,
                judge_model=judge_model,
                profile_prompt_version=self.PROFILE_PROMPT_VERSION,
            )
            if not force_profiles and updated_voice.omni_profile is not None and updated_voice.profile_hash == profile_hash:
                refs = self.sample_refs_for_voice(updated_voice, manifest.sample_emotions)
                self.repo.save_voice_profile(
                    manifest,
                    updated_voice,
                    judge_name=judge_name,
                    judge_model=judge_model,
                    profile_prompt_version=self.PROFILE_PROMPT_VERSION,
                    sample_refs=self.profile_sample_ref_payload(refs),
                )
                updated_voices[index] = updated_voice
                continue

            refs = self.sample_refs_for_voice(updated_voice, manifest.sample_emotions)
            missing = sorted(set(manifest.sample_emotions).difference({str(ref.metadata.get("emotion")) for ref in refs}))
            if missing:
                raise ValueError(
                    f"Cannot profile {voice.voice_type}: missing sample audio for {', '.join(missing)}"
                )
            sample_refs = self.profile_sample_ref_payload(refs)
            prompt = prompts.render(
                "voice_catalog_profile",
                official_metadata=json.dumps(updated_voice.official, ensure_ascii=False, indent=2),
                sample_refs=json.dumps(sample_refs, ensure_ascii=False, indent=2),
            )
            profile_jobs.append(
                {
                    "index": index,
                    "voice": updated_voice,
                    "profile_hash": profile_hash,
                    "refs": refs,
                    "sample_refs": sample_refs,
                    "prompt": prompt,
                }
            )

        semaphore = asyncio.Semaphore(self.PROFILE_BUILD_CONCURRENCY)

        async def build_profile_job(job: dict[str, Any]) -> tuple[int, VoiceCatalogVoiceItem]:
            updated_voice = job["voice"]
            async with semaphore:
                profile = await judge.judge_audio_json(
                    job["prompt"],
                    VoiceCatalogProfile,
                    refs=job["refs"],
                    temperature=0.2,
                    metadata={
                        "node_name": "voice_catalog_profile",
                        "voice_catalog_key": updated_voice.voice_catalog_key,
                        "voice_type": updated_voice.voice_type,
                        "voice_label": updated_voice.voice_label,
                    },
                )
            updated_voice.omni_profile = profile
            updated_voice.profile_hash = job["profile_hash"]
            self.repo.save_voice_profile(
                manifest,
                updated_voice,
                judge_name=judge_name,
                judge_model=judge_model,
                profile_prompt_version=self.PROFILE_PROMPT_VERSION,
                sample_refs=job["sample_refs"],
            )
            return int(job["index"]), updated_voice

        tasks = [asyncio.create_task(build_profile_job(job)) for job in profile_jobs]
        try:
            for task in asyncio.as_completed(tasks):
                index, updated_voice = await task
                updated_voices[index] = updated_voice
                progress_voices = [
                    updated if updated is not None else original
                    for updated, original in zip(updated_voices, manifest.voices)
                ]
                progress_manifest = manifest.model_copy(update={"voices": progress_voices})
                self.repo.save_manifest(progress_manifest)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        final_voices = [
            updated if updated is not None else original
            for updated, original in zip(updated_voices, manifest.voices)
        ]
        manifest = manifest.model_copy(update={"voices": final_voices})
        self.repo.save_manifest(manifest)
        return manifest

    @staticmethod
    def candidate_from_voice(
        voice: VoiceCatalogVoiceItem,
        *,
        score: float | None = None,
        reason: str,
        candidate_id: str | None = None,
    ) -> VoiceCandidateItem:
        profile_summary = voice.omni_profile.summary if voice.omni_profile else None
        sample_paths = {
            emotion: sample.asset_path
            for emotion, sample in voice.samples.items()
        }
        return VoiceCandidateItem(
            candidate_id=candidate_id,
            voice_label=voice.voice_label,
            voice_type=voice.voice_type,
            voice_resource_id=voice.voice_resource_id,
            voice_model_family=voice.voice_model_family,
            voice_catalog_key=voice.voice_catalog_key,
            score=score,
            reason=reason,
            profile_summary=profile_summary,
            sample_paths=sample_paths,
        )

    @staticmethod
    def _role_text(role: Role) -> str:
        audio_text = " ".join(
            " ".join(
                str(value or "")
                for value in (audio.emotion, audio.desc, audio.sample_text)
            )
            for audio in role.audio.values()
        )
        return " ".join(
            str(value or "")
            for value in (
                role.name,
                role.intro,
                role.personality,
                role.voice_summary,
                role.importance,
                audio_text,
            )
        )

    @staticmethod
    def _infer_role_gender(role_text: str) -> str | None:
        female_markers = (
            "女性",
            "女声",
            "女主",
            "女孩",
            "少女",
            "母亲",
            "妈妈",
            "妻子",
            "姐姐",
            "妹妹",
            "她",
        )
        male_markers = (
            "男性",
            "男声",
            "男主",
            "青年男",
            "父亲",
            "爸爸",
            "丈夫",
            "哥哥",
            "弟弟",
            "主管",
            "他",
        )
        female_score = sum(1 for marker in female_markers if marker in role_text)
        male_score = sum(1 for marker in male_markers if marker in role_text)
        if female_score > male_score:
            return "female"
        if male_score > female_score:
            return "male"
        return None

    @staticmethod
    def _voice_gender(voice: VoiceCatalogVoiceItem) -> str | None:
        gender = str(voice.official.get("gender") or "").strip().lower()
        label = voice.voice_label.lower()
        voice_type = voice.voice_type.lower()
        gender_text = " ".join([gender, label, voice_type])
        if gender in {"female", "woman", "girl", "女", "女性", "女声"}:
            return "female"
        if gender in {"male", "man", "boy", "男", "男性", "男声"}:
            return "male"
        if any(marker in gender_text for marker in ("female", "woman", "girl", "女", "女生", "少女", "御姐")):
            return "female"
        if any(
            marker in gender_text
            for marker in ("male", " man ", "boy", "男", "男生", "少年", "青年", "青叔", "大叔", "叔", "爷")
        ):
            return "male"
        if voice.voice_type.startswith("zh_female"):
            return "female"
        if voice.voice_type.startswith("zh_male"):
            return "male"
        return None

    @staticmethod
    def _profile_text(voice: VoiceCatalogVoiceItem) -> str:
        parts: list[str] = [
            voice.voice_label,
            voice.voice_type,
            str(voice.official.get("scene") or ""),
            str(voice.official.get("language") or ""),
            " ".join(str(item) for item in voice.official.get("abilities") or []),
            " ".join(str(item) for item in voice.official.get("tags") or []),
        ]
        if voice.omni_profile:
            profile = voice.omni_profile
            parts.extend(
                [
                    profile.summary,
                    profile.gender_presentation or "",
                    profile.age_impression or "",
                    " ".join(profile.texture),
                    " ".join(profile.performance_style),
                    " ".join(profile.best_role_types),
                    " ".join(profile.avoid_role_types),
                ]
            )
        return " ".join(parts)

    @staticmethod
    def _voice_is_doubao_2_0(voice: VoiceCatalogVoiceItem) -> bool:
        official = voice.official
        values = [
            voice.voice_resource_id,
            voice.voice_model_family,
            official.get("resource_id"),
            official.get("voice_resource_id"),
            official.get("model_family"),
            official.get("voice_model_family"),
        ]
        text = " ".join(str(value or "") for value in values)
        return "seed-tts-2.0" in text or "豆包语音合成模型2.0" in text

    def role_voice_select_candidate_pool(
        self,
        role: Role,
        manifest: VoiceCatalogManifest,
    ) -> tuple[list[VoiceCandidateItem], dict[str, Any]]:
        role_text = self._role_text(role)
        role_gender = self._infer_role_gender(role_text)
        require_doubao_2 = str(manifest.provider or "").strip().lower() == "volcengine"
        skipped = {
            "not_auto_selectable": 0,
            "non_chinese": 0,
            "unknown_gender": 0,
            "gender_mismatch": 0,
            "non_doubao_2_0": 0,
        }
        scored: list[tuple[float, int, VoiceCatalogVoiceItem, str]] = []
        for index, voice in enumerate(manifest.voices):
            if voice.official.get("auto_selectable") is False:
                skipped["not_auto_selectable"] += 1
                continue
            if self._voice_language_key(voice) != "zh":
                skipped["non_chinese"] += 1
                continue
            voice_gender = self._voice_gender(voice)
            if role_gender:
                if voice_gender is None:
                    skipped["unknown_gender"] += 1
                    continue
                if role_gender != voice_gender:
                    skipped["gender_mismatch"] += 1
                    continue
            if require_doubao_2 and not self._voice_is_doubao_2_0(voice):
                skipped["non_doubao_2_0"] += 1
                continue
            score, reason = self.score_voice_for_role(role, voice)
            scored.append((score, -index, voice, reason))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        candidates = [
            self.candidate_from_voice(
                voice,
                score=score,
                reason=reason,
                candidate_id=f"V{position:03d}",
            )
            for position, (score, _index, voice, reason) in enumerate(scored, start=1)
        ]
        metadata = {
            "role_gender": role_gender or "unknown",
            "require_language": "zh",
            "require_same_gender": bool(role_gender),
            "require_doubao_2_0": require_doubao_2,
            "candidate_count": len(candidates),
            "skipped": skipped,
        }
        return candidates, metadata

    @staticmethod
    def compact_candidate_profile(candidate: VoiceCandidateItem, voice: VoiceCatalogVoiceItem) -> dict[str, Any]:
        official = voice.official
        profile = voice.omni_profile
        compact_profile: dict[str, Any] | None = None
        if profile is not None:
            compact_profile = {
                "summary": profile.summary,
                "gender_presentation": profile.gender_presentation,
                "age_impression": profile.age_impression,
                "texture": profile.texture[:3],
                "performance_style": profile.performance_style[:3],
                "best_role_types": profile.best_role_types[:3],
                "avoid_role_types": profile.avoid_role_types[:3],
            }
        abilities = [str(item) for item in official.get("abilities") or [] if str(item).strip()]
        tags = [str(item) for item in official.get("tags") or [] if str(item).strip()]
        return {
            "candidate_id": candidate.candidate_id,
            "voice_label": candidate.voice_label,
            "voice_type": candidate.voice_type,
            "gender": VoiceCatalogService._voice_gender(voice),
            "language": VoiceCatalogService._voice_language_key(voice),
            "model_family": voice.voice_model_family or official.get("model_family"),
            "scene": official.get("scene"),
            "abilities": abilities[:4],
            "tags": tags[:6],
            "emotion_capable": bool(official.get("emotion_capable") or official.get("supported_emotions")),
            "profile": compact_profile,
            "local_score": candidate.score,
            "local_reason": candidate.reason,
        }

    def voice_profiles_for_prompt(
        self,
        role: Role,
        manifest: VoiceCatalogManifest,
        candidates: list[VoiceCandidateItem] | None = None,
    ) -> list[dict[str, Any]]:
        if candidates is None:
            candidates, _metadata = self.role_voice_select_candidate_pool(role, manifest)
        profiles: list[dict[str, Any]] = []
        for candidate in candidates:
            voice = self.repo.voice_by_type(manifest, candidate.voice_type)
            if voice is None:
                continue
            profiles.append(self.compact_candidate_profile(candidate, voice))
        return profiles

    def score_voice_for_role(self, role: Role, voice: VoiceCatalogVoiceItem) -> tuple[float, str]:
        role_text = self._role_text(role)
        profile_text = self._profile_text(voice)
        score = 5.0
        reasons: list[str] = []

        role_gender = self._infer_role_gender(role_text)
        voice_gender = self._voice_gender(voice)
        if role_gender and voice_gender:
            if role_gender == voice_gender:
                score += 2.0
                reasons.append("性别呈现匹配")
            else:
                score -= 3.0
                reasons.append("性别呈现可能不匹配")

        official = voice.official
        if official.get("emotion_capable") or official.get("supported_emotions"):
            score += 0.7
            reasons.append("官方标注支持情绪变化")
        if any(key in role_text for key in ("克制", "冷静", "理性", "沉稳")) and any(
            key in profile_text for key in ("清晰", "稳定", "通用", "理性", "自然")
        ):
            score += 0.6
            reasons.append("适合克制自然的短剧对白")
        if any(key in role_text for key in ("强势", "压迫", "反派", "主管", "控制")) and any(
            key in profile_text for key in ("低沉", "成熟", "力量", "情感")
        ):
            score += 0.6
            reasons.append("可能适合强势或压迫型角色")
        if voice.omni_profile and voice.omni_profile.avoid_role_types:
            avoid_hits = [
                item
                for item in voice.omni_profile.avoid_role_types
                if item and item in role_text
            ]
            if avoid_hits:
                score -= 1.0
                reasons.append("画像中存在规避角色类型")

        if not reasons:
            reasons.append("基于官方元数据和音色画像文本的基础匹配")
        return round(score, 2), "，".join(reasons) + "。"

    def shortlist(self, role: Role, manifest: VoiceCatalogManifest, *, limit: int = 5) -> list[VoiceCandidateItem]:
        scored = []
        for index, voice in enumerate(manifest.voices):
            score, reason = self.score_voice_for_role(role, voice)
            scored.append((score, -index, voice, reason))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            self.candidate_from_voice(voice, score=score, reason=reason)
            for score, _index, voice, reason in scored[:limit]
        ]

    @staticmethod
    def role_profile_for_shortlist(role: Role) -> dict[str, Any]:
        return {
            "role_id": role.id,
            "role_name": role.name,
            "intro": role.intro,
            "personality": role.personality,
            "role_tier": role.role_tier,
            "importance": role.importance,
            "voice_summary": role.voice_summary,
            "audio": [
                {
                    "emotion": audio.emotion,
                    "desc": audio.desc,
                    "sample_text": audio.sample_text,
                }
                for audio in role.audio.values()
            ],
        }

    def candidates_from_shortlist_output(
        self,
        output: VoiceSelectShortlistOutput,
        manifest: VoiceCatalogManifest,
        *,
        fallback_candidates: list[VoiceCandidateItem],
        candidate_by_id: dict[str, VoiceCandidateItem] | None = None,
        limit: int = 5,
    ) -> list[VoiceCandidateItem]:
        candidates: list[VoiceCandidateItem] = []
        seen_voice_types: set[str] = set()
        for item in output.candidates:
            candidate_id = str(item.candidate_id or "").strip()
            source_candidate = candidate_by_id.get(candidate_id) if candidate_by_id else None
            voice_type = str(
                source_candidate.voice_type if source_candidate is not None else item.voice_type or ""
            ).strip()
            if not voice_type or voice_type in seen_voice_types:
                continue
            voice = self.repo.voice_by_type(manifest, voice_type)
            if voice is None:
                continue
            base_candidate = source_candidate or self.candidate_from_voice(
                voice,
                candidate_id=candidate_id or None,
                reason=item.reason,
            )
            candidates.append(base_candidate.model_copy(update={"score": item.score, "reason": item.reason}))
            seen_voice_types.add(voice_type)
            if len(candidates) >= limit:
                break

        for fallback in fallback_candidates:
            if len(candidates) >= limit:
                break
            if fallback.voice_type in seen_voice_types:
                continue
            candidates.append(fallback)
            seen_voice_types.add(fallback.voice_type)
        return candidates

    @staticmethod
    def _provider_role_speakers(provider: Any) -> dict[str, Any]:
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", None)
        if isinstance(options, dict):
            value = options.get("role_speakers")
            if isinstance(value, dict):
                return value
        return {}

    def manual_override_candidate(
        self,
        *,
        provider: Any,
        manifest: VoiceCatalogManifest,
        role: Role,
    ) -> VoiceCandidateItem | None:
        role_speakers = self._provider_role_speakers(provider)
        if not role_speakers:
            return None

        override_value = None
        for key in (role.id, role.name, f"{role.id}:default"):
            if key and role_speakers.get(key):
                override_value = role_speakers[key]
                break
        if override_value is None:
            return None

        if isinstance(override_value, dict):
            voice_type = str(
                override_value.get("voice_type")
                or override_value.get("speaker")
                or override_value.get("hash_type")
                or ""
            ).strip()
            voice_label = str(
                override_value.get("voice_label")
                or override_value.get("voice_name")
                or override_value.get("name")
                or voice_type
            ).strip()
            resource_id = override_value.get("voice_resource_id") or override_value.get("resource_id")
            model_family = override_value.get("voice_model_family") or override_value.get("model_family")
        else:
            voice_type = str(override_value or "").strip()
            voice_label = voice_type
            resource_id = None
            model_family = None

        if not voice_type:
            return None

        catalog_voice = self.repo.voice_by_type(manifest, voice_type)
        if catalog_voice is not None:
            return self.candidate_from_voice(
                catalog_voice,
                score=None,
                reason="命中 config.yaml role_speakers 手工覆盖。",
            )

        return VoiceCandidateItem(
            voice_label=voice_label or voice_type,
            voice_type=voice_type,
            voice_resource_id=str(resource_id) if resource_id else None,
            voice_model_family=str(model_family) if model_family else None,
            voice_catalog_key=f"{manifest.provider}:{manifest.model}:{voice_type}",
            score=None,
            reason="命中 config.yaml role_speakers 手工覆盖，但该 voice_type 不在当前 catalog 中。",
            profile_summary=None,
            sample_paths={},
        )

    def fallback_candidate(
        self,
        *,
        provider: Any,
        manifest: VoiceCatalogManifest,
        role: Role,
    ) -> VoiceCandidateItem | None:
        resolver = getattr(provider, "resolve_role_voice", None)
        voice_type = None
        if callable(resolver):
            voice_type = resolver(
                role_id=role.id,
                role_name=role.name,
                role_intro=role.intro,
                role_voice_summary=role.voice_summary,
                role_personality=role.personality,
            )
        if not voice_type:
            voice_type = getattr(provider, "default_speaker", None)
        if not voice_type:
            return None
        catalog_voice = self.repo.voice_by_type(manifest, str(voice_type))
        if catalog_voice is not None:
            return self.candidate_from_voice(
                catalog_voice,
                score=None,
                reason="使用 provider fallback 默认音色。",
            )
        return VoiceCandidateItem(
            voice_label=str(voice_type),
            voice_type=str(voice_type),
            voice_resource_id=None,
            voice_model_family=None,
            voice_catalog_key=f"{manifest.provider}:{manifest.model}:{voice_type}",
            score=None,
            reason="使用 provider fallback 默认音色。",
            profile_summary=None,
            sample_paths={},
        )

    @staticmethod
    def selection_from_candidate(
        *,
        role: Role,
        candidate: VoiceCandidateItem,
        top_candidates: list[VoiceCandidateItem],
        role_profile_hash: str,
        manifest: VoiceCatalogManifest,
        catalog_hash: str,
        selection_source: str,
        selected_reason: str | None = None,
    ) -> RoleVoiceSelectionItem:
        return RoleVoiceSelectionItem(
            role_id=role.id,
            role_name=role.name,
            selected_voice_label=candidate.voice_label,
            selected_voice_type=candidate.voice_type,
            selected_voice_resource_id=candidate.voice_resource_id,
            selected_voice_model_family=candidate.voice_model_family,
            selected_voice_catalog_key=candidate.voice_catalog_key,
            selected_reason=selected_reason or candidate.reason,
            selection_source=selection_source,
            top_candidates=top_candidates,
            role_profile_hash=role_profile_hash,
            catalog_version=manifest.catalog_version,
            catalog_hash=catalog_hash,
            provider=manifest.provider,
            model=manifest.model,
        )


__all__ = ["VoiceCatalogService"]
