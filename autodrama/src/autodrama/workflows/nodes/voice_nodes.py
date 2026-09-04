from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from autodrama.core.schemas import (
    ProjectState,
    Role,
    RoleVoiceRequirements,
    SemanticProvenance,
)
from autodrama.core.voice_catalog import (
    RoleVoiceSelectionItem,
    VoiceCandidateItem,
    VoiceCatalogManifest,
    VoiceSelectAudioJudgeOutput,
    VoiceSelectOutput,
    VoiceSelectShortlistOutput,
)
from autodrama.providers.base import AssetRef
from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.media_store import MediaStore
from autodrama.services.voice_catalog_service import VoiceCatalogService
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

VOICE_NODE_NAMES = [
    "role_voice_select",
]


class VoiceNodeBase:
    def __init__(
        self,
        *,
        workflow: Any,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        script_service: ScriptService,
        role_service: RoleService,
        script_contents: ScriptContentRepository,
        media_store: MediaStore,
        logger: Any,
    ) -> None:
        self.workflow = workflow
        self.repo = repo
        self.layout = layout
        self.router = router
        self.script_service = script_service
        self.role_service = role_service
        self.script_contents = script_contents
        self.media_store = media_store
        self.logger = logger

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.state_episode_keys(state)

    def active_episode_keys(self, state: ProjectState) -> list[str]:
        context = getattr(self.workflow, "_run_context", None)
        has_selected_context = context is not None and getattr(context, "selected_episode_keys", None) is not None
        if not has_selected_context and getattr(self.workflow, "_active_episode_keys", None) is None:
            return []
        getter = getattr(self.workflow, "_active_episode_keys_in_order", None)
        if callable(getter):
            return list(getter(state))
        active_episode_keys = getattr(self.workflow, "_active_episode_keys", None)
        if active_episode_keys is None:
            return []
        active = {str(key) for key in active_episode_keys}
        return [episode_key for episode_key in self.expected_episode_keys(state) if episode_key in active]

    def active_role_names(self) -> list[str]:
        context = getattr(self.workflow, "_run_context", None)
        if context is not None and getattr(context, "selected_role_names", None) is not None:
            return list(context.selected_role_names or [])
        return list(getattr(self.workflow, "_active_role_names", None) or [])

    @staticmethod
    def role_matches_active_episode_keys(role: Role, active_episode_keys: list[str], *, label: str) -> bool:
        if not active_episode_keys:
            return True
        role_episode_keys = [str(key).strip() for key in role.episode_keys if str(key).strip()]
        if not role_episode_keys:
            raise ValueError(f"{label} cannot scope role {role.name}: missing episode_keys")
        return bool(set(role_episode_keys).intersection(active_episode_keys))

    @staticmethod
    def normalize_role_selector(value: str) -> str:
        return str(value or "").strip().casefold()

    @classmethod
    def role_selector_keys(cls, role: Role) -> set[str]:
        return {
            cls.normalize_role_selector(role.id),
            cls.normalize_role_selector(role.name),
        }

    def target_roles(self, state: ProjectState, active_episode_keys: list[str], *, label: str) -> list[Role]:
        roles = list(state.roles.values())
        active_role_names = [name for name in self.active_role_names() if str(name).strip()]
        if active_role_names:
            selector_keys = {
                self.normalize_role_selector(name): str(name).strip()
                for name in active_role_names
                if self.normalize_role_selector(name)
            }
            matched_selector_keys: set[str] = set()
            filtered_roles: list[Role] = []
            for role in roles:
                role_keys = self.role_selector_keys(role)
                matched = role_keys.intersection(selector_keys)
                if not matched:
                    continue
                matched_selector_keys.update(matched)
                filtered_roles.append(role)
            missing = [selector_keys[key] for key in selector_keys if key not in matched_selector_keys]
            if missing:
                available = ", ".join(role.name for role in roles) or "-"
                raise ValueError(f"{label} role selector(s) not found: {', '.join(missing)}. Available roles: {available}")
            roles = filtered_roles

        filtered = [
            role
            for role in roles
            if self.role_matches_active_episode_keys(role, active_episode_keys, label=label)
        ]
        if active_role_names and active_episode_keys and not filtered:
            raise ValueError(
                f"{label} selected role(s) {', '.join(active_role_names)} do not appear in selected episodes: "
                f"{', '.join(active_episode_keys)}"
            )
        return filtered


class RoleVoiceSelectNode(VoiceNodeBase):
    name = "role_voice_select"
    selection_prompt_version = "role_voice_select.structured_contract.v5"
    voice_contract_version = 2
    candidate_limit = 3
    text_shortlist_model = "deepseek-v4-flash"
    text_shortlist_batch_size = 80

    @staticmethod
    def call_router_getter(getter, purpose: str, *, node_name: str):
        try:
            return getter(purpose, node_name=node_name)
        except TypeError as exc:
            if "node_name" not in str(exc):
                raise
            return getter(purpose)

    @classmethod
    def role_profile_hash(cls, role: Role) -> str:
        payload = {
            "role_id": role.id,
            "role_name": role.name,
            "role_tier": role.role_tier,
            "has_dialogue": role.has_dialogue,
            "episode_keys": role.episode_keys,
            "voice_requirements": role.voice_requirements.model_dump(mode="json"),
            "appearances": {
                name: {
                    "id": appearance.id,
                    "desc": appearance.desc,
                    "asset_path": appearance.asset_path,
                    "asset_url": appearance.asset_url,
                    "design_image_asset_path": appearance.design_image_asset_path,
                    "design_image_asset_url": appearance.design_image_asset_url,
                }
                for name, appearance in sorted(role.appearances.items())
            },
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @staticmethod
    def voice_requirements_need_extraction(role: Role) -> bool:
        requirements = role.voice_requirements
        return (
            requirements.provenance.source == "migration"
            and requirements.language == "unspecified"
            and requirements.gender_presentation == "unspecified"
            and requirements.age_impression == "unspecified"
            and not requirements.performance_traits
            and requirements.baseline_emotion is None
            and not requirements.hard_constraints
        )

    async def ensure_role_voice_requirements(self, role: Role) -> RoleVoiceRequirements:
        if not self.voice_requirements_need_extraction(role):
            return role.voice_requirements
        provider, provider_error = self.text_shortlist_provider()
        prompts = getattr(self.workflow, "prompts", None)
        if provider is None or prompts is None:
            self.logger.warning(
                "role_voice_requirements extraction skipped role=%s reason=%s",
                role.name,
                provider_error or "workflow prompt store is unavailable",
            )
            return role.voice_requirements
        role_context = {
            "role_name": role.name,
            "intro": role.intro,
            "personality": role.personality,
            "role_tier": role.role_tier,
            "voice_summary": role.voice_summary,
            "appearance_age_bands": [
                appearance.age_band
                for appearance in role.appearances.values()
                if appearance.age_band
            ],
            "stable_identity_invariants": [
                invariant
                for appearance in role.appearances.values()
                for invariant in appearance.identity_invariants
            ],
            "voice_performance_notes": [
                {
                    "emotion": audio.emotion,
                    "description": audio.desc,
                }
                for audio in role.audio.values()
                if audio.desc
            ],
        }
        prompt = prompts.render(
            "role_voice_requirements",
            role_context=json.dumps(role_context, ensure_ascii=False, indent=2),
        )
        try:
            extracted = await provider.generate_json(
                prompt,
                RoleVoiceRequirements,
                temperature=0.1,
                metadata={
                    "node_name": "role_voice_requirements",
                    "role_id": role.id,
                    "role_name": role.name,
                },
            )
        except Exception as exc:
            self.logger.warning(
                "role_voice_requirements extraction failed role=%s error=%r",
                role.name,
                exc,
            )
            return role.voice_requirements
        provider_model = (
            getattr(provider, "model", None)
            or getattr(provider, "target_model", None)
            or getattr(provider, "name", None)
        )
        extracted = extracted.model_copy(
            update={
                "provenance": SemanticProvenance(
                    source="model",
                    evidence=list(extracted.provenance.evidence),
                    confidence=extracted.provenance.confidence,
                    model=str(provider_model) if provider_model else None,
                )
            }
        )
        role.voice_requirements = extracted
        return extracted

    def load_existing_output(self, project_dir: Path) -> VoiceSelectOutput | None:
        path = self.layout.node_output_path(project_dir, self.name)
        if not path.exists():
            return None
        return VoiceSelectOutput.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def existing_by_role(output: VoiceSelectOutput | None) -> dict[str, RoleVoiceSelectionItem]:
        if output is None:
            return {}
        return {
            item.role_id: item
            for item in output.selected_voices
            if item.role_id
        }

    @staticmethod
    def ordered_output(
        state: ProjectState,
        merged_by_role: dict[str, RoleVoiceSelectionItem],
    ) -> VoiceSelectOutput:
        ordered_selections = [
            merged_by_role[role.id]
            for role in state.roles.values()
            if role.id in merged_by_role
        ]
        for role_id, item in merged_by_role.items():
            if role_id not in state.roles:
                ordered_selections.append(item)
        return VoiceSelectOutput(selected_voices=ordered_selections)

    def save_progress_output(
        self,
        project_dir: Path,
        state: ProjectState,
        merged_by_role: dict[str, RoleVoiceSelectionItem],
    ) -> None:
        self.repo.save_node_output(project_dir, self.name, self.ordered_output(state, merged_by_role))

    @staticmethod
    def bind_selection_to_role(role: Role, selection: RoleVoiceSelectionItem) -> None:
        role.voice_name = selection.selected_voice_label
        role.voice_type = selection.selected_voice_type
        role.voice_resource_id = selection.selected_voice_resource_id
        role.voice_model_family = selection.selected_voice_model_family
        role.voice_selection_reason = selection.selected_reason
        for audio in role.audio.values():
            audio.voice_name = role.voice_name
            audio.voice_type = role.voice_type
            audio.voice_resource_id = role.voice_resource_id
            audio.voice_model_family = role.voice_model_family

    def cached_selection(
        self,
        *,
        existing: RoleVoiceSelectionItem | None,
        role_profile_hash: str,
        manifest: VoiceCatalogManifest,
        catalog_hash: str,
        force: bool,
        catalog_repo: VoiceCatalogRepository,
    ) -> RoleVoiceSelectionItem | None:
        if force or existing is None:
            return None
        if existing.role_profile_hash != role_profile_hash:
            return None
        if existing.catalog_version != manifest.catalog_version:
            return None
        if existing.catalog_hash != catalog_hash:
            return None
        if catalog_repo.voice_by_type(manifest, existing.selected_voice_type) is None:
            return None
        if existing.raw_response.get("selection_prompt_version") != self.selection_prompt_version:
            return None
        if existing.raw_response.get("voice_contract_version") != self.voice_contract_version:
            return None
        if existing.selection_source == "catalog_heuristic":
            return None
        if existing.raw_response.get("selection_model") != self.selection_model(manifest):
            return None
        if existing.raw_response.get("text_shortlist_error") or existing.raw_response.get("audio_judge_error"):
            return None
        return existing.model_copy(update={"selection_source": "cache"})

    @staticmethod
    def provider_model_label(provider: Any) -> str:
        name = str(getattr(provider, "name", "unknown") or "unknown")
        model = (
            getattr(provider, "model", None)
            or getattr(provider, "target_model", None)
            or getattr(provider, "resource_id", None)
            or "-"
        )
        return f"{name}:{model}"

    def text_shortlist_provider(self) -> tuple[Any | None, str | None]:
        text_getter = getattr(self.router, "text", None)
        if not callable(text_getter):
            return None, "router has no text provider"

        errors: list[str] = []
        for purpose in ("role_voice_select", "role"):
            try:
                return self.call_router_getter(text_getter, purpose, node_name=self.name), None
            except Exception as exc:
                errors.append(f"{purpose}: {exc!r}")
        return None, "; ".join(errors)

    def role_voice_select_text_provider(self) -> tuple[Any | None, str | None]:
        if str(getattr(self.router, "provider_override", "") or ""):
            return self.text_shortlist_provider()

        settings = getattr(self.repo, "settings", None)
        provider_settings = getattr(settings, "providers", {}).get("deepseek") if settings is not None else None
        runtime_settings = getattr(settings, "runtime", None)
        if settings is not None and self.name in getattr(settings, "nodes", {}):
            return self.text_shortlist_provider()
        if provider_settings is None or runtime_settings is None:
            return self.text_shortlist_provider()

        role_voice_select_settings = provider_settings.model_copy(deep=True)
        role_voice_select_settings.models = dict(role_voice_select_settings.models)
        role_voice_select_settings.models["text"] = self.text_shortlist_model
        role_voice_select_settings.options = dict(role_voice_select_settings.options)
        role_voice_select_settings.options["thinking_enabled"] = False
        role_voice_select_settings.options["reasoning_effort"] = "low"
        return DeepSeekTextProvider(role_voice_select_settings, runtime_settings), None

    def selection_model(self, manifest: VoiceCatalogManifest) -> str:
        text_provider, text_error = self.role_voice_select_text_provider()
        if text_provider is not None:
            text_model = self.provider_model_label(text_provider)
        else:
            text_model = f"unavailable:{text_error or '-'}"

        judge_getter = getattr(self.router, "judge", None)
        if callable(judge_getter):
            try:
                judge_model = self.provider_model_label(
                    self.call_router_getter(judge_getter, "role_voice_select", node_name="role_voice_select_audio_judge")
                )
            except Exception as exc:
                judge_model = f"unavailable:{exc!r}"
        else:
            judge_model = "unavailable:router has no judge provider"

        return (
            f"catalog={manifest.provider}:{manifest.model}|"
            f"text_shortlist={text_model}|audio_judge={judge_model}"
        )

    def with_selection_metadata(
        self,
        selection: RoleVoiceSelectionItem,
        manifest: VoiceCatalogManifest,
        *,
        extra_raw_response: dict[str, Any] | None = None,
    ) -> RoleVoiceSelectionItem:
        raw_response = dict(selection.raw_response)
        raw_response.setdefault("selection_prompt_version", self.selection_prompt_version)
        raw_response.setdefault("voice_contract_version", self.voice_contract_version)
        raw_response.setdefault("selection_model", self.selection_model(manifest))
        if extra_raw_response:
            raw_response.update(extra_raw_response)
        return selection.model_copy(update={"raw_response": raw_response})

    @staticmethod
    def role_visual_refs(project_dir: Path, role: Role) -> list[AssetRef]:
        refs: list[AssetRef] = []
        appearances = list(role.appearances.values())
        appearances.sort(key=lambda item: (0 if item.name == "base" else 1, item.name, item.id))
        for appearance in appearances:
            asset_path = appearance.asset_path or appearance.design_image_asset_path
            asset_url = appearance.asset_url or appearance.design_image_asset_url
            asset_id = appearance.asset_id or appearance.design_image_asset_id or appearance.id
            if not (asset_path or asset_url):
                continue
            path = None
            if asset_path:
                candidate = Path(asset_path)
                path = str(candidate if candidate.is_absolute() else project_dir / candidate)
            refs.append(
                AssetRef(
                    id=asset_id or appearance.id,
                    type="image",
                    path=path,
                    url=asset_url,
                    metadata={
                        "asset_type": "roleboard",
                        "reference_source": "role_voice_select_role_visual",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "desc": appearance.desc,
                    },
                )
            )
            break
        return refs

    @classmethod
    def role_visual_refs_for_prompt(cls, project_dir: Path, role: Role) -> list[dict[str, Any]]:
        refs = cls.role_visual_refs(project_dir, role)
        return [
            {
                "id": ref.id,
                "type": ref.type,
                "asset_type": ref.metadata.get("asset_type"),
                "role_name": ref.metadata.get("role_name"),
                "appearance_id": ref.metadata.get("appearance_id"),
                "appearance_name": ref.metadata.get("appearance_name"),
                "desc": ref.metadata.get("desc"),
                "path": ref.path,
                "url": ref.url,
            }
            for ref in refs
        ]

    @classmethod
    def role_profile_for_prompt(cls, project_dir: Path, role: Role) -> dict[str, Any]:
        profile = {
            "role_id": role.id,
            "role_name": role.name,
            "role_tier": role.role_tier,
            "voice_requirements": role.voice_requirements.model_dump(mode="json"),
        }
        visual_refs = cls.role_visual_refs_for_prompt(project_dir, role)
        if visual_refs:
            profile["visual_reference_assets"] = visual_refs
            profile["visual_voice_matching_requirement"] = (
                "选择音色时必须参考人物图呈现的视觉年龄、体态、气质、服装风格和角色能量；"
                "避免声线年龄感、厚度、甜度、成熟度或压迫感与人物形象明显脱节。"
            )
        return profile

    async def text_shortlist(
        self,
        *,
        project_dir: Path,
        service: VoiceCatalogService,
        manifest: VoiceCatalogManifest,
        role: Role,
        structured_candidates: list[VoiceCandidateItem],
        candidate_pool: list[VoiceCandidateItem] | None = None,
        filter_metadata: dict[str, Any] | None = None,
        limit: int = 3,
    ) -> tuple[list[VoiceCandidateItem], dict[str, Any]]:
        del structured_candidates
        if candidate_pool is None or filter_metadata is None:
            candidate_pool, filter_metadata = service.role_voice_select_candidate_pool(role, manifest)
        fallback_candidates = candidate_pool[:limit]

        provider, provider_error = self.role_voice_select_text_provider()
        if provider is None:
            return fallback_candidates, {
                "text_shortlist_skipped": provider_error or "text provider is unavailable",
                "text_shortlist_filters": filter_metadata,
            }

        prompts = getattr(self.workflow, "prompts", None)
        if prompts is None:
            return fallback_candidates, {
                "text_shortlist_skipped": "workflow prompt store is unavailable",
                "text_shortlist_filters": filter_metadata,
            }

        role_profile = self.role_profile_for_prompt(project_dir, role)
        voice_profiles = service.voice_profiles_for_prompt(role, manifest, candidates=candidate_pool)
        candidate_by_id = {
            str(candidate.candidate_id): candidate
            for candidate in candidate_pool
            if candidate.candidate_id
        }
        if not voice_profiles:
            return fallback_candidates, {
                "text_shortlist_skipped": "no role_voice_select candidates after language/gender/model filters",
                "text_shortlist_filters": filter_metadata,
                "text_shortlist_provider": self.provider_model_label(provider),
            }

        prompt = prompts.render(
            "role_voice_select_shortlist",
            role_profile=json.dumps(role_profile, ensure_ascii=False, indent=2),
            voice_profiles=json.dumps(voice_profiles, ensure_ascii=False, indent=2),
        )
        self.logger.info(
            "role_voice_select model_call=text_shortlist role=%s provider=%s voices=%d limit=%d filters=%s",
            role.name,
            self.provider_model_label(provider),
            len(voice_profiles),
            limit,
            json.dumps(filter_metadata, ensure_ascii=False, sort_keys=True),
        )
        output = await provider.generate_json(
            prompt,
            VoiceSelectShortlistOutput,
            temperature=0.2,
            metadata={
                "node_name": "role_voice_select_shortlist",
                "role_id": role.id,
                "role_name": role.name,
                "provider": manifest.provider,
                "model": manifest.model,
                "manifest_voice_count": len(manifest.voices),
                "voice_count": len(voice_profiles),
                "limit": limit,
                "filters": filter_metadata,
                "voice_profiles": voice_profiles,
            },
        )
        candidates = service.candidates_from_shortlist_output(
            output,
            manifest,
            fallback_candidates=fallback_candidates,
            candidate_by_id=candidate_by_id,
            limit=limit,
        )

        raw_shortlist = output.model_dump(mode="json")
        if not candidates:
            return fallback_candidates, {
                "text_shortlist": raw_shortlist,
                "text_shortlist_error": "text shortlist returned no valid catalog voice_type",
                "text_shortlist_provider": self.provider_model_label(provider),
                "text_shortlist_filters": filter_metadata,
            }
        return candidates, {
            "text_shortlist": raw_shortlist,
            "text_shortlist_provider": self.provider_model_label(provider),
            "text_shortlist_filters": filter_metadata,
        }

    def audio_judge_refs(
        self,
        *,
        service: VoiceCatalogService,
        manifest: VoiceCatalogManifest,
        top_candidates: list[VoiceCandidateItem],
    ) -> list[AssetRef]:
        refs: list[AssetRef] = []
        for candidate in top_candidates:
            voice = service.repo.voice_by_type(manifest, candidate.voice_type)
            if voice is None:
                continue
            for ref in service.sample_refs_for_voice(voice, manifest.sample_emotions):
                metadata = dict(ref.metadata)
                metadata["candidate_id"] = candidate.candidate_id
                refs.append(ref.model_copy(update={"metadata": metadata}))
        return refs

    async def audio_judge_selection(
        self,
        *,
        role: Role,
        project_dir: Path,
        service: VoiceCatalogService,
        manifest: VoiceCatalogManifest,
        top_candidates: list[VoiceCandidateItem],
    ) -> tuple[VoiceCandidateItem, VoiceSelectAudioJudgeOutput] | None:
        if not top_candidates:
            return None
        role_visual_refs = self.role_visual_refs(project_dir, role)
        audio_refs = self.audio_judge_refs(
            service=service,
            manifest=manifest,
            top_candidates=top_candidates,
        )
        refs = [*role_visual_refs, *audio_refs]
        expected_ref_count = len(top_candidates) * len(manifest.sample_emotions)
        if len(audio_refs) < expected_ref_count:
            return None
        judge_getter = getattr(self.router, "judge", None)
        if not callable(judge_getter):
            return None
        judge = self.call_router_getter(judge_getter, "role_voice_select", node_name="role_voice_select_audio_judge")
        prompts = getattr(self.workflow, "prompts", None)
        if prompts is None:
            return None
        candidate_profiles = [
            {
                "candidate_id": candidate.candidate_id,
                "voice_label": candidate.voice_label,
                "voice_type": candidate.voice_type,
                "score": candidate.score,
                "reason": candidate.reason,
                "profile_summary": candidate.profile_summary,
                "sample_paths": candidate.sample_paths,
            }
            for candidate in top_candidates
        ]
        prompt = prompts.render(
            "role_voice_select_audio_judge",
            role_profile=json.dumps(self.role_profile_for_prompt(project_dir, role), ensure_ascii=False, indent=2),
            candidate_profiles=json.dumps(candidate_profiles, ensure_ascii=False, indent=2),
            role_visual_refs=json.dumps(
                [
                    {
                        "id": ref.id,
                        "asset_type": ref.metadata.get("asset_type"),
                        "appearance_id": ref.metadata.get("appearance_id"),
                        "appearance_name": ref.metadata.get("appearance_name"),
                        "desc": ref.metadata.get("desc"),
                        "path": ref.path,
                        "url": ref.url,
                    }
                    for ref in role_visual_refs
                ],
                ensure_ascii=False,
                indent=2,
            ),
            sample_refs=json.dumps(
                [
                    {
                        "id": ref.id,
                        "candidate_id": ref.metadata.get("candidate_id"),
                        "voice_type": ref.metadata.get("voice_type"),
                        "voice_label": ref.metadata.get("voice_label"),
                        "emotion": ref.metadata.get("emotion"),
                        "sample_text": ref.metadata.get("sample_text"),
                        "path": ref.path,
                    }
                    for ref in audio_refs
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        self.logger.info(
            "role_voice_select model_call=audio_judge role=%s provider=%s candidates=%d refs=%d sample_emotions=%s",
            role.name,
            self.provider_model_label(judge),
            len(top_candidates),
            len(refs),
            ",".join(manifest.sample_emotions) or "-",
        )
        judge_output = await judge.judge_audio_json(
            prompt,
            VoiceSelectAudioJudgeOutput,
            refs=refs,
            temperature=0.2,
            metadata={
                "node_name": self.name,
                "role_id": role.id,
                "role_name": role.name,
                "candidates": candidate_profiles,
                "role_visual_refs": [ref.model_dump(mode="json") for ref in role_visual_refs],
                "audio_ref_count": len(audio_refs),
            },
        )
        by_id = {
            str(candidate.candidate_id): candidate
            for candidate in top_candidates
            if candidate.candidate_id
        }
        by_type = {candidate.voice_type: candidate for candidate in top_candidates}
        selected = by_id.get(str(judge_output.selected_candidate_id or "").strip())
        if selected is None:
            selected = by_type.get(judge_output.selected_voice_type)
        if selected is None:
            return None
        return selected, judge_output

    async def select_role_voice(
        self,
        *,
        provider: Any,
        project_dir: Path,
        service: VoiceCatalogService,
        manifest: VoiceCatalogManifest,
        catalog_hash: str,
        role: Role,
        existing: RoleVoiceSelectionItem | None,
        force: bool,
    ) -> RoleVoiceSelectionItem:
        role_hash = self.role_profile_hash(role)
        manual_candidate = service.manual_override_candidate(
            provider=provider,
            manifest=manifest,
            role=role,
        )
        if manual_candidate is not None:
            selection = service.selection_from_candidate(
                role=role,
                candidate=manual_candidate,
                top_candidates=[manual_candidate],
                role_profile_hash=role_hash,
                manifest=manifest,
                catalog_hash=catalog_hash,
                selection_source="manual_override",
                selected_reason=manual_candidate.reason,
            )
            return self.with_selection_metadata(selection, manifest)

        await self.ensure_role_voice_requirements(role)
        role_hash = self.role_profile_hash(role)
        cached = self.cached_selection(
            existing=existing,
            role_profile_hash=role_hash,
            manifest=manifest,
            catalog_hash=catalog_hash,
            force=force,
            catalog_repo=service.repo,
        )
        if cached is not None:
            return cached

        limit = max(1, int(self.candidate_limit))
        candidate_pool, filter_metadata = service.role_voice_select_candidate_pool(role, manifest)
        structured_candidates = candidate_pool[:limit]
        top_candidates = structured_candidates
        shortlist_response: dict[str, Any] = {
            "text_shortlist_filters": filter_metadata,
        }
        try:
            top_candidates, shortlist_response = await self.text_shortlist(
                project_dir=project_dir,
                service=service,
                manifest=manifest,
                role=role,
                structured_candidates=structured_candidates,
                candidate_pool=candidate_pool,
                filter_metadata=filter_metadata,
                limit=limit,
            )
        except Exception as exc:
            shortlist_response = {
                "text_shortlist_error": repr(exc),
                "text_shortlist_filters": filter_metadata,
            }

        if top_candidates:
            selected_candidate = top_candidates[0]
            raw_response = dict(shortlist_response)
            text_shortlist_used = (
                "text_shortlist" in raw_response
                and "text_shortlist_error" not in raw_response
            )
            try:
                judged = await self.audio_judge_selection(
                    role=role,
                    project_dir=project_dir,
                    service=service,
                    manifest=manifest,
                    top_candidates=top_candidates,
                )
            except Exception as exc:
                judged = None
                raw_response["audio_judge_error"] = repr(exc)
            if judged is not None:
                selected_candidate, judged_output = judged
                raw_response["audio_judge"] = judged_output.model_dump(mode="json")
                raw_response["audio_judge_role_visual_ref_count"] = len(self.role_visual_refs(project_dir, role))
            selection = service.selection_from_candidate(
                role=role,
                candidate=selected_candidate,
                top_candidates=top_candidates,
                role_profile_hash=role_hash,
                manifest=manifest,
                catalog_hash=catalog_hash,
                selection_source=(
                    "omni_judge"
                    if judged is not None
                    else ("text_shortlist" if text_shortlist_used else "structured_catalog")
                ),
                selected_reason=(
                    judged_output.selected_reason if judged is not None else selected_candidate.reason
                ),
            )
            return self.with_selection_metadata(selection, manifest, extra_raw_response=raw_response)

        fallback_candidate = service.fallback_candidate(
            provider=provider,
            manifest=manifest,
            role=role,
        )
        if fallback_candidate is None:
            raise ValueError(f"role_voice_select cannot select a voice for {role.name}: catalog is empty")
        selection = service.selection_from_candidate(
            role=role,
            candidate=fallback_candidate,
            top_candidates=[fallback_candidate],
            role_profile_hash=role_hash,
            manifest=manifest,
            catalog_hash=catalog_hash,
            selection_source="provider_fallback",
            selected_reason=fallback_candidate.reason,
        )
        return self.with_selection_metadata(selection, manifest)

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.audio("speech", node_name="role_voice_select_speech")
        self.logger.info(
            "node=role_voice_select provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )

        hydrate = getattr(self.workflow, "_hydrate_roles_from_design_files", None)
        if callable(hydrate):
            hydrate(project_dir, state)
        ensure_normal_audio = getattr(self.workflow, "_ensure_normal_role_audio", None)
        if callable(ensure_normal_audio):
            for role in state.roles.values():
                if self.workflow._role_needs_voice(role) and "normal" not in role.audio:
                    ensure_normal_audio(role)

        catalog_repo = VoiceCatalogRepository.from_settings(self.repo.settings)
        service = VoiceCatalogService(catalog_repo)
        manifest = service.load_or_bootstrap_manifest(provider)
        catalog_hash = catalog_repo.manifest_hash(manifest)
        manifest_path = catalog_repo.manifest_path(manifest.provider, manifest.model)
        self.logger.info(
            "node=role_voice_select catalog=%s voices=%d catalog_version=%s",
            manifest_path,
            len(manifest.voices),
            manifest.catalog_version,
        )

        active_episode_keys = self.active_episode_keys(state)
        active_role_names = self.active_role_names()
        target_roles = [
            role
            for role in self.target_roles(state, active_episode_keys, label=self.name)
            if self.workflow._role_needs_voice(role)
        ]
        if active_episode_keys:
            self.logger.info(
                "node=role_voice_select episode-scoped rerun episodes=%s target_roles=%s",
                ",".join(active_episode_keys),
                ",".join(role.name for role in target_roles) or "-",
            )
        if active_role_names:
            self.logger.info(
                "node=role_voice_select role-scoped rerun roles=%s target_roles=%s",
                ",".join(active_role_names),
                ",".join(role.name for role in target_roles) or "-",
            )

        force = bool(getattr(self.workflow, "_force_pregen", False))
        existing_output = self.load_existing_output(project_dir)
        existing_by_role = self.existing_by_role(existing_output)
        merged_by_role = dict(existing_by_role)

        progress_lock = asyncio.Lock()

        async def process_role(role: Role) -> None:
            selection = await self.select_role_voice(
                provider=provider,
                project_dir=project_dir,
                service=service,
                manifest=manifest,
                catalog_hash=catalog_hash,
                role=role,
                existing=existing_by_role.get(role.id),
                force=force,
            )
            self.bind_selection_to_role(role, selection)
            async with progress_lock:
                merged_by_role[role.id] = selection
                self.save_progress_output(project_dir, state, merged_by_role)
                self.logger.info(
                    "%s generated voice_type=%s source=%s",
                    role.name,
                    selection.selected_voice_type,
                    selection.selection_source,
                )

        tasks = [asyncio.create_task(process_role(role)) for role in target_roles]
        try:
            await asyncio.gather(*tasks)
        except Exception:
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        self.save_progress_output(project_dir, state, merged_by_role)
        return state


def build_voice_node_runners(workflow: Any) -> dict[str, VoiceNodeBase]:
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)
    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "role_service": workflow.role_service,
        "script_contents": script_contents,
        "media_store": workflow.media_store,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        RoleVoiceSelectNode.name: RoleVoiceSelectNode(**deps),
    }


def build_voice_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_voice_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in VOICE_NODE_NAMES
    ]


__all__ = [
    "VOICE_NODE_NAMES",
    "RoleVoiceSelectNode",
    "VoiceNodeBase",
    "build_voice_node_runners",
    "build_voice_nodes",
]
