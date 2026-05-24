from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

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
        judge_model = str(getattr(judge, "model", "") or "")
        updated_voices: list[VoiceCatalogVoiceItem] = []

        for voice in manifest.voices:
            if voice_types and voice.voice_type not in voice_types:
                updated_voices.append(voice)
                continue
            updated_voice = voice.model_copy(deep=True)
            profile_hash = self.profile_hash(
                updated_voice,
                judge_name=str(getattr(judge, "name", "unknown")),
                judge_model=judge_model,
                profile_prompt_version=self.PROFILE_PROMPT_VERSION,
            )
            if not force_profiles and updated_voice.omni_profile is not None and updated_voice.profile_hash == profile_hash:
                refs = self.sample_refs_for_voice(updated_voice, manifest.sample_emotions)
                self.repo.save_voice_profile(
                    manifest,
                    updated_voice,
                    judge_name=str(getattr(judge, "name", "unknown")),
                    judge_model=judge_model,
                    profile_prompt_version=self.PROFILE_PROMPT_VERSION,
                    sample_refs=self.profile_sample_ref_payload(refs),
                )
                updated_voices.append(updated_voice)
                continue

            refs = self.sample_refs_for_voice(updated_voice, manifest.sample_emotions)
            missing = sorted(set(manifest.sample_emotions).difference({str(ref.metadata.get("emotion")) for ref in refs}))
            if missing:
                raise ValueError(
                    f"Cannot profile {voice.voice_type}: missing sample audio for {', '.join(missing)}"
                )
            prompt = prompts.render(
                "voice_catalog_profile",
                official_metadata=json.dumps(updated_voice.official, ensure_ascii=False, indent=2),
                sample_refs=json.dumps(self.profile_sample_ref_payload(refs), ensure_ascii=False, indent=2),
            )
            profile = await judge.judge_audio_json(
                prompt,
                VoiceCatalogProfile,
                refs=refs,
                temperature=0.2,
                metadata={
                    "node_name": "voice_catalog_profile",
                    "voice_catalog_key": updated_voice.voice_catalog_key,
                    "voice_type": updated_voice.voice_type,
                    "voice_label": updated_voice.voice_label,
                },
            )
            updated_voice.omni_profile = profile
            updated_voice.profile_hash = profile_hash
            self.repo.save_voice_profile(
                manifest,
                updated_voice,
                judge_name=str(getattr(judge, "name", "unknown")),
                judge_model=judge_model,
                profile_prompt_version=self.PROFILE_PROMPT_VERSION,
                sample_refs=self.profile_sample_ref_payload(refs),
            )
            updated_voices.append(updated_voice)
            manifest = manifest.model_copy(update={"voices": updated_voices + manifest.voices[len(updated_voices):]})
            self.repo.save_manifest(manifest)

        manifest = manifest.model_copy(update={"voices": updated_voices})
        self.repo.save_manifest(manifest)
        return manifest

    @staticmethod
    def candidate_from_voice(
        voice: VoiceCatalogVoiceItem,
        *,
        score: float | None = None,
        reason: str,
    ) -> VoiceCandidateItem:
        profile_summary = voice.omni_profile.summary if voice.omni_profile else None
        sample_paths = {
            emotion: sample.asset_path
            for emotion, sample in voice.samples.items()
        }
        return VoiceCandidateItem(
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
        if gender in {"female", "woman", "女", "女性"}:
            return "female"
        if gender in {"male", "man", "男", "男性"}:
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
    def role_design_for_shortlist(role: Role) -> dict[str, Any]:
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

    def voice_profiles_for_prompt(self, role: Role, manifest: VoiceCatalogManifest) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for voice in manifest.voices:
            heuristic_score, heuristic_reason = self.score_voice_for_role(role, voice)
            official = voice.official
            profiles.append(
                {
                    "voice_label": voice.voice_label,
                    "voice_type": voice.voice_type,
                    "voice_resource_id": voice.voice_resource_id,
                    "voice_model_family": voice.voice_model_family,
                    "voice_catalog_key": voice.voice_catalog_key,
                    "official": {
                        "gender": official.get("gender"),
                        "language": official.get("language"),
                        "scene": official.get("scene"),
                        "abilities": official.get("abilities"),
                        "tags": official.get("tags"),
                        "emotion_capable": official.get("emotion_capable"),
                        "supported_emotions": official.get("supported_emotions"),
                    },
                    "omni_profile": (
                        voice.omni_profile.model_dump(mode="json")
                        if voice.omni_profile is not None
                        else None
                    ),
                    "heuristic_score": heuristic_score,
                    "heuristic_reason": heuristic_reason,
                    "available_sample_emotions": sorted(voice.samples),
                }
            )
        return profiles

    def candidates_from_shortlist_output(
        self,
        output: VoiceSelectShortlistOutput,
        manifest: VoiceCatalogManifest,
        *,
        fallback_candidates: list[VoiceCandidateItem],
        limit: int = 5,
    ) -> list[VoiceCandidateItem]:
        candidates: list[VoiceCandidateItem] = []
        seen_voice_types: set[str] = set()
        for item in output.candidates:
            voice_type = str(item.voice_type or "").strip()
            if not voice_type or voice_type in seen_voice_types:
                continue
            voice = self.repo.voice_by_type(manifest, voice_type)
            if voice is None:
                continue
            candidates.append(
                self.candidate_from_voice(
                    voice,
                    score=item.score,
                    reason=item.reason,
                )
            )
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
        role_design_hash: str,
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
            role_design_hash=role_design_hash,
            catalog_version=manifest.catalog_version,
            catalog_hash=catalog_hash,
            provider=manifest.provider,
            model=manifest.model,
        )


__all__ = ["VoiceCatalogService"]
