from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from autodrama.core.errors import ProviderBadResponseError, ProviderError
from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    ProjectState,
    Role,
    RoleAppearance,
    RoleKlingVoiceGenerationItem,
    RoleKlingVoiceGenerationOutput,
    RoleSubjectElementGenerationItem,
    RoleSubjectElementGenerationOutput,
    RoleSubjectFrontalImageGenerationItem,
    RoleSubjectFrontalImageGenerationOutput,
    RoleSubjectVideoGenerationItem,
    RoleSubjectVideoGenerationOutput,
    RoleSubjectVideoIntroTextOutput,
)
from autodrama.core.voice_catalog import RoleVoiceSelectionItem
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef, SubjectElementResult, VideoGenerationResult, VoiceAssetResult
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.services.voice_catalog_service import VoiceCatalogService
from autodrama.workflows.nodes.voice_nodes import build_voice_node_runners
from autodrama.workflows.runner import WorkflowNode


ROLE_SUBJECT_NODE_NAMES = [
    "role_subject_frontal_image_generation",
    "role_kling_voice_generation",
    "role_subject_video_generation",
    "role_subject_element_generation",
]


class RoleSubjectNodeBase:
    def __init__(self, *, workflow: Any) -> None:
        self.workflow = workflow
        self.repo = workflow.repo
        self.layout = workflow.layout
        self.router = workflow.router
        self.media_store = workflow.media_store
        self.logger = getattr(workflow, "logger", None) or get_logger()

    @staticmethod
    def _provider_supports_subject_elements(provider: object) -> bool:
        return bool(getattr(provider, "supports_subject_elements", False))

    def _video_provider(self, *, node_name: str):
        provider = None
        try:
            provider = self.router.video("shot", node_name=node_name)
            if self._provider_supports_subject_elements(provider):
                return provider
        except Exception:
            provider = None
        shot_provider = self.router.video("shot", node_name="shot_video_generation")
        if self._provider_supports_subject_elements(shot_provider):
            return shot_provider
        return provider

    def _target_role_appearances(self, state: ProjectState) -> list[tuple[Role, RoleAppearance]]:
        active_episode_keys = getattr(self.workflow, "_active_episode_keys", None)
        active = {str(key) for key in active_episode_keys or []}
        targets: list[tuple[Role, RoleAppearance]] = []
        for role in state.roles.values():
            if not role.visual_reuse_required:
                continue
            if active:
                role_episode_keys = {str(key) for key in role.episode_keys}
                if role_episode_keys and not role_episode_keys.intersection(active):
                    continue
            for appearance in role.appearances.values():
                targets.append((role, appearance))
        return targets

    def _roleboard_ref(self, project_dir: Path, role: Role, appearance: RoleAppearance) -> AssetRef | None:
        asset_path = appearance.asset_path or appearance.design_image_asset_path
        asset_url = appearance.asset_url or appearance.design_image_asset_url
        existing = self.layout.existing_project_file(project_dir, asset_path)
        if not existing and not asset_url:
            return None
        return AssetRef(
            id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
            type="image",
            path=str(project_dir / existing) if existing else None,
            url=asset_url,
            metadata={
                "asset_type": "roleboard",
                "reference_source": "roleboard_image_generation",
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": appearance.id,
                "appearance_name": appearance.name,
            },
        )

    def _key_vision_ref(self, project_dir: Path, state: ProjectState, *, reference_for: str) -> AssetRef | None:
        key_vision = state.metadata.get("key_vision_asset")
        if isinstance(key_vision, dict):
            asset_id = str(key_vision.get("asset_id") or state.metadata.get("key_vision_asset_id") or "key_vision_original")
            asset_path = key_vision.get("asset_path") or state.metadata.get("key_vision_asset_path")
            asset_url = key_vision.get("asset_url") or state.metadata.get("key_vision_asset_url")
            name = str(key_vision.get("name") or state.metadata.get("key_vision_name") or "主视觉原图")
        else:
            asset_id = str(state.metadata.get("key_vision_asset_id") or "key_vision_original")
            asset_path = state.metadata.get("key_vision_asset_path")
            asset_url = state.metadata.get("key_vision_asset_url")
            name = str(state.metadata.get("key_vision_name") or "主视觉原图")
        existing = self.layout.existing_project_file(project_dir, str(asset_path)) if asset_path else None
        if not existing and not asset_url:
            return None
        return AssetRef(
            id=asset_id,
            type="image",
            path=str(project_dir / existing) if existing else None,
            url=str(asset_url) if asset_url else None,
            metadata={
                "asset_type": "key_vision",
                "reference_source": "key_vision_image_generation",
                "reference_role": "style_world_reference",
                "reference_for": reference_for,
                "name": name,
            },
        )

    def _frontal_image_ref(
        self,
        project_dir: Path,
        role: Role,
        appearance: RoleAppearance,
    ) -> AssetRef | None:
        existing = self.layout.existing_project_file(project_dir, appearance.subject_frontal_image_asset_path)
        asset_url = appearance.subject_frontal_image_asset_url
        if not existing and not asset_url:
            return None
        return AssetRef(
            id=appearance.subject_frontal_image_asset_id or normalize_id("role_subject_frontal", appearance.id),
            type="image",
            path=str(project_dir / existing) if existing else None,
            url=asset_url,
            metadata={
                "asset_type": "role_subject_frontal",
                "reference_source": "role_subject_frontal_image_generation",
                "reference_role": "frontal_image",
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": appearance.id,
                "appearance_name": appearance.name,
            },
        )

    def _kling_roleboard_ref(
        self,
        project_dir: Path,
        role: Role,
        appearance: RoleAppearance,
    ) -> AssetRef | None:
        ref = self._roleboard_ref(project_dir, role, appearance)
        if ref is None or not ref.path:
            return ref
        source = Path(ref.path)
        if not source.exists() or not source.is_file():
            return ref

        max_bytes = 9_500_000
        if source.stat().st_size <= max_bytes:
            return ref

        output_dir = source.parent / "subject_refs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{source.stem}_kling_subject_ref.jpg"
        if (
            output.exists()
            and output.stat().st_size <= max_bytes
            and output.stat().st_mtime_ns >= source.stat().st_mtime_ns
        ):
            compressed_path = output
        else:
            temporary = output.with_suffix(".tmp.jpg")
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                if image.width < 300 or image.height < 300:
                    raise ValueError(f"Kling roleboard reference is smaller than 300px: {source}")
                ratio = image.width / image.height
                if ratio < 0.4 or ratio > 2.5:
                    raise ValueError(f"Kling roleboard reference aspect ratio is outside 1:2.5-2.5:1: {source}")
                if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")

                for quality in (92, 88, 82, 76, 68):
                    image.save(temporary, format="JPEG", quality=quality, optimize=True, progressive=True)
                    if temporary.stat().st_size <= max_bytes:
                        break
                while temporary.stat().st_size > max_bytes and min(image.size) > 600:
                    image = image.resize(
                        (max(300, int(image.width * 0.9)), max(300, int(image.height * 0.9))),
                        Image.Resampling.LANCZOS,
                    )
                    image.save(temporary, format="JPEG", quality=76, optimize=True, progressive=True)
            if temporary.stat().st_size > max_bytes:
                temporary.unlink(missing_ok=True)
                raise ValueError(f"Could not compress Kling roleboard reference below 10MB: {source}")
            temporary.replace(output)
            compressed_path = output

        return AssetRef(
            id=f"{ref.id or appearance.id}_kling_subject_ref",
            type="image",
            path=str(compressed_path),
            metadata={
                **ref.metadata,
                "asset_type": "roleboard",
                "reference_role": "other_reference_image",
                "derived_from": str(source),
                "max_bytes": max_bytes,
            },
        )

    def _subject_reference_type(self, provider: object) -> str:
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        return str(params.get("subject_reference_type") or options.get("subject_reference_type") or "video_refer").strip()

    def _subject_video_duration(self, provider: object) -> float:
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value = options.get("subject_video_duration_seconds") or options.get("subject_duration_seconds") or 5
        return float(value)

    @staticmethod
    def _bounded_concurrency(provider: object, *option_names: str, default: int = 1, cap: int = 5) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in option_names:
                if name in source:
                    value = source[name]
                    break
            if value is not None:
                break
        if value is None:
            for name in option_names:
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = default
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = default
        return max(1, min(cap, resolved))


class RoleSubjectFrontalImageGenerationNode(RoleSubjectNodeBase):
    """Generate one clean frontal reference image from each reusable roleboard."""

    name = "role_subject_frontal_image_generation"

    @staticmethod
    def _asset_id(appearance: RoleAppearance) -> str:
        return normalize_id("role_subject_frontal", appearance.id)

    @staticmethod
    def _prompt(role: Role, appearance: RoleAppearance) -> str:
        appearance_text = str(appearance.desc or appearance.visual_features or "").strip()
        return "\n".join(
            [
                "严格参考输入的角色身份板，为同一个主体生成一张独立的正面标准参考图。",
                "只保留一个主体，不要角色设定板、三视图、分格、拼图、局部特写、动作分解、文字、标签、边框、logo或水印。",
                "主体正对镜头，头部与身体朝向均为正面，五官或主体核心结构无遮挡，姿态自然稳定，身份辨识度高。",
                "完整保留参考图中的脸型、发型、服装、配色、材质、配饰和关键结构；不得重新设计，不得增加或删除身份特征。",
                "使用简洁中性的浅色背景，主体居中，画面清晰，适合作为可灵主体库的 frontal_image。",
                "人物或类人主体使用从头到脚的全身正面站姿；非人物主体完整展示其正面整体结构。",
                f"主体名称：{role.name}",
                f"外观名称：{appearance.name}",
                f"外观约束：{appearance_text}",
            ]
        )

    @staticmethod
    def _item_from_appearance(
        role: Role,
        appearance: RoleAppearance,
        *,
        prompt: str,
        raw_response: dict[str, Any] | None = None,
    ) -> RoleSubjectFrontalImageGenerationItem:
        return RoleSubjectFrontalImageGenerationItem(
            role_id=role.id,
            role_name=role.name,
            appearance_id=appearance.id,
            appearance_name=appearance.name,
            asset_id=appearance.subject_frontal_image_asset_id or RoleSubjectFrontalImageGenerationNode._asset_id(appearance),
            prompt=prompt,
            asset_path=appearance.subject_frontal_image_asset_path,
            asset_url=appearance.subject_frontal_image_asset_url,
            provider=appearance.subject_frontal_image_provider or "unknown",
            model=appearance.subject_frontal_image_model or "",
            request_id=appearance.subject_frontal_image_request_id,
            usage=appearance.subject_frontal_image_usage,
            raw_response=raw_response or appearance.subject_frontal_image_raw_response,
        )

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role", node_name=self.name)
        force = bool(getattr(self.workflow, "_force_pregen", False))
        concurrency = self._bounded_concurrency(
            provider,
            "role_subject_frontal_image_generation_concurrency",
            "image_generation_concurrency",
            "concurrency",
            default=3,
            cap=5,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(
            role: Role,
            appearance: RoleAppearance,
        ) -> RoleSubjectFrontalImageGenerationItem:
            prompt = self._prompt(role, appearance)
            existing = self.layout.existing_project_file(project_dir, appearance.subject_frontal_image_asset_path)
            if not force and existing is not None:
                appearance.subject_frontal_image_asset_path = existing
                return self._item_from_appearance(
                    role,
                    appearance,
                    prompt=prompt,
                    raw_response={"resumed_from_existing_file": True},
                )

            roleboard_ref = self._roleboard_ref(project_dir, role, appearance)
            if roleboard_ref is None:
                raise FileNotFoundError(
                    f"{self.name} requires roleboard image for {role.name}/{appearance.name}; "
                    "run roleboard_image_generation first"
                )

            asset_id = self._asset_id(appearance)
            output_path = self.layout.image_asset_path(project_dir, "roles", asset_id)
            async with semaphore:
                result = await provider.generate_image(
                    prompt,
                    refs=[roleboard_ref],
                    metadata={
                        "node_name": self.name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "asset_id": asset_id,
                        "asset_type": "role_subject_frontal",
                        "reference_role": "roleboard_identity_reference",
                    },
                )
                asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)

            appearance.subject_frontal_image_asset_id = asset_id
            appearance.subject_frontal_image_asset_path = asset_path
            appearance.subject_frontal_image_asset_url = result.image_urls[0] if result.image_urls else None
            appearance.subject_frontal_image_provider = result.provider
            appearance.subject_frontal_image_model = result.model
            appearance.subject_frontal_image_request_id = result.request_id
            appearance.subject_frontal_image_usage = result.usage
            appearance.subject_frontal_image_raw_response = result.raw_response
            self.logger.info(
                "%s generated frontal reference for %s/%s at %s",
                self.name,
                role.name,
                appearance.name,
                asset_path,
            )
            return self._item_from_appearance(role, appearance, prompt=prompt)

        tasks = [
            asyncio.create_task(generate_one(role, appearance))
            for role, appearance in self._target_role_appearances(state)
        ]
        try:
            generated = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleSubjectFrontalImageGenerationOutput(generated_frontal_images=generated),
        )
        return state


class RoleKlingVoiceGenerationNode(RoleSubjectNodeBase):
    """Select official Kling voices or create explicitly configured custom voices."""

    name = "role_kling_voice_generation"

    @staticmethod
    def _voice_options(provider: object) -> dict[str, Any]:
        options: dict[str, Any] = {}
        settings = getattr(provider, "settings", None)
        configured = getattr(settings, "options", {}) if settings is not None else {}
        if isinstance(configured, dict):
            options.update(configured)
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        if isinstance(params, dict):
            options.update(params)
        return options

    @classmethod
    def _voice_spec(cls, provider: object, role: Role) -> dict[str, Any] | None:
        options = cls._voice_options(provider)
        mapping = options.get("role_voice_map") or options.get("kling_role_voice_map") or {}
        if not isinstance(mapping, dict):
            raise ValueError("Kling role_voice_map must be an object keyed by role id or role name")
        raw = mapping.get(role.id)
        if raw is None:
            raw = mapping.get(role.name)
        if raw is None:
            return None
        if isinstance(raw, str):
            return {"voice_id": raw, "voice_name": role.name}
        if isinstance(raw, dict):
            return dict(raw)
        raise ValueError(f"Kling voice mapping for {role.name} must be a voice id string or object")

    @staticmethod
    def _bind_result(role: Role, result: VoiceAssetResult, *, source: str) -> None:
        role.kling_voice_id = result.voice_id
        role.kling_voice_name = result.voice_name or role.name
        role.kling_voice_source = "custom" if source == "custom" else "preset"
        role.kling_voice_trial_url = result.trial_url
        role.kling_voice_provider = result.provider
        role.kling_voice_model = result.model
        role.kling_voice_task_id = result.task_id
        role.kling_voice_task_status = result.task_status
        role.kling_voice_request_id = result.request_id
        role.kling_voice_usage = result.usage
        role.kling_voice_raw_response = result.raw_response

    @staticmethod
    def _item(role: Role) -> RoleKlingVoiceGenerationItem:
        if not role.kling_voice_id:
            raise ValueError(f"Kling voice is not bound for {role.name}")
        return RoleKlingVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            voice_id=role.kling_voice_id,
            voice_name=role.kling_voice_name,
            source=role.kling_voice_source or "preset",
            trial_url=role.kling_voice_trial_url,
            provider=role.kling_voice_provider or "kling_omni",
            model=role.kling_voice_model or "custom-voices",
            task_id=role.kling_voice_task_id,
            task_status=role.kling_voice_task_status,
            request_id=role.kling_voice_request_id,
            usage=role.kling_voice_usage,
            raw_response=role.kling_voice_raw_response,
        )

    @staticmethod
    def _cached_selection(role: Role) -> RoleVoiceSelectionItem | None:
        value = role.kling_voice_raw_response.get("voice_selection")
        if not isinstance(value, dict):
            return None
        try:
            return RoleVoiceSelectionItem.model_validate(value)
        except Exception:
            return None

    @staticmethod
    def _bind_catalog_selection(
        role: Role,
        selection: RoleVoiceSelectionItem,
        *,
        trial_url: str | None,
        owned_by: str | None,
    ) -> None:
        result = VoiceAssetResult(
            provider=selection.provider,
            model=selection.model,
            task_status="succeeded",
            voice_id=selection.selected_voice_type,
            voice_name=selection.selected_voice_label,
            trial_url=trial_url,
            owned_by=owned_by or "kling",
            request_id=selection.request_id,
            usage=selection.usage,
            raw_response={
                "official_preset_voice": True,
                "selection_source": selection.selection_source,
                "voice_selection": selection.model_dump(mode="json"),
            },
        )
        RoleKlingVoiceGenerationNode._bind_result(role, result, source="preset")
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

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._video_provider(node_name=self.name)
        if not bool(getattr(provider, "supports_custom_voices", False)):
            self.repo.save_node_output(
                project_dir,
                self.name,
                RoleKlingVoiceGenerationOutput(
                    skipped_roles=[{"reason": "provider_does_not_support_kling_voices"}],
                ),
            )
            return state

        options = self._voice_options(provider)
        require_voice = bool(options.get("require_dialogue_voice", True))
        force = bool(getattr(self.workflow, "_force_pregen", False))
        active_episode_keys = {str(item) for item in getattr(self.workflow, "_active_episode_keys", None) or []}
        active_role_names = {str(item).casefold() for item in getattr(self.workflow, "_active_role_names", None) or []}
        generated: list[RoleKlingVoiceGenerationItem] = []
        skipped: list[dict[str, Any]] = []
        target_roles: list[Role] = []
        for role in state.roles.values():
            if not role.has_dialogue:
                continue
            if active_role_names and role.name.casefold() not in active_role_names and role.id.casefold() not in active_role_names:
                continue
            if active_episode_keys and role.episode_keys and not active_episode_keys.intersection(role.episode_keys):
                continue
            target_roles.append(role)

        needs_auto_selection = [
            role
            for role in target_roles
            if self._voice_spec(provider, role) is None and (force or not role.kling_voice_id)
        ]
        auto_select = bool(options.get("auto_select_official_voice", True))
        catalog_repo: VoiceCatalogRepository | None = None
        catalog_service: VoiceCatalogService | None = None
        manifest = None
        selector = None
        catalog_hash = ""
        if needs_auto_selection and auto_select:
            catalog_repo = VoiceCatalogRepository.from_settings(self.repo.settings)
            catalog_service = VoiceCatalogService(catalog_repo)
            manifest = await catalog_service.sync_official_preset_catalog(
                provider,
                refresh_manifest=bool(options.get("refresh_official_voice_catalog", False)),
                force_samples=bool(options.get("force_official_voice_samples", False)),
                download_trials=bool(options.get("download_official_voice_trials", True)),
                download_concurrency=int(options.get("official_voice_download_concurrency", 8)),
            )
            if not manifest.voices:
                raise ValueError("Kling official voice catalog is empty")
            catalog_hash = catalog_repo.manifest_hash(manifest)
            selector = build_voice_node_runners(self.workflow)["role_voice_select"]

        for role in target_roles:
            spec = self._voice_spec(provider, role)
            if spec is None and role.kling_voice_id and not force:
                generated.append(self._item(role))
                continue
            if spec is None and not auto_select:
                if require_voice:
                    raise ValueError(
                        f"No Kling voice configured for dialogue role {role.name} ({role.id}); "
                        "enable auto_select_official_voice or set role_voice_map with voice_id/voice_url"
                    )
                skipped.append({"role_id": role.id, "role_name": role.name, "reason": "voice_not_configured"})
                continue

            if spec is None:
                if selector is None or catalog_service is None or catalog_repo is None or manifest is None:
                    raise ValueError("Kling official voice selector was not initialized")
                selection = await selector.select_role_voice(
                    provider=provider,
                    project_dir=project_dir,
                    service=catalog_service,
                    manifest=manifest,
                    catalog_hash=catalog_hash,
                    role=role,
                    existing=self._cached_selection(role),
                    force=force,
                )
                catalog_voice = catalog_repo.voice_by_type(manifest, selection.selected_voice_type)
                if catalog_voice is None:
                    raise ValueError(
                        f"Kling official voice selection returned unknown voice_id {selection.selected_voice_type}"
                    )
                self._bind_catalog_selection(
                    role,
                    selection,
                    trial_url=str(catalog_voice.official.get("trial_url") or "") or None,
                    owned_by=str(catalog_voice.official.get("owned_by") or "kling"),
                )
                generated.append(self._item(role))
                continue

            voice_id = str(spec.get("voice_id") or "").strip()
            if voice_id:
                result = VoiceAssetResult(
                    provider=getattr(provider, "name", "kling_omni"),
                    model=getattr(provider, "voice_model", "custom-voices"),
                    task_status="succeeded",
                    voice_id=voice_id,
                    voice_name=str(spec.get("voice_name") or role.name),
                    trial_url=str(spec.get("trial_url") or "") or None,
                    owned_by=str(spec.get("owned_by") or "kling"),
                    raw_response={"configured_preset_voice": True},
                )
                source = "preset"
            else:
                voice_url = str(spec.get("voice_url") or "").strip()
                video_id = str(spec.get("video_id") or "").strip()
                if voice_url and not voice_url.startswith(("http://", "https://")):
                    raise ValueError(f"Kling custom voice_url for {role.name} must be a public HTTP(S) URL")
                if not voice_url and not video_id:
                    raise ValueError(f"Kling voice mapping for {role.name} requires voice_id, voice_url, or video_id")
                result = await provider.generate_custom_voice(
                    voice_name=str(spec.get("voice_name") or role.name)[:20],
                    voice_url=voice_url or None,
                    video_id=video_id or None,
                    wait=True,
                    metadata={
                        "node_name": self.name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "role_name": role.name,
                    },
                )
                source = "custom"
            if not result.voice_id:
                raise ValueError(f"Kling voice preparation returned no voice_id for {role.name}")
            self._bind_result(role, result, source=source)
            generated.append(self._item(role))

        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleKlingVoiceGenerationOutput(generated_voices=generated, skipped_roles=skipped),
        )
        return state


class RoleSubjectVideoGenerationNode(RoleSubjectNodeBase):
    name = "role_subject_video_generation"

    @staticmethod
    def _external_task_already_exists(exc: ProviderBadResponseError) -> bool:
        text = str(exc)
        lowered = text.casefold()
        return "external_task_id" in lowered and ("already exists" in lowered or "已存在" in text)

    @staticmethod
    def _video_success_statuses(provider: object) -> set[str]:
        statuses = getattr(provider, "_TERMINAL_SUCCESS", {"succeeded", "success", "completed", "done"})
        return {str(status).strip().lower() for status in statuses}

    @staticmethod
    def _video_failure_statuses(provider: object) -> set[str]:
        statuses = getattr(
            provider,
            "_TERMINAL_FAILURE",
            {"failed", "fail", "error", "expired", "cancelled", "canceled"},
        )
        return {str(status).strip().lower() for status in statuses}

    def _subject_video_download_output_path(
        self,
        project_dir: Path,
        appearance: RoleAppearance,
        asset_id: str,
    ) -> Path:
        if appearance.subject_video_asset_path:
            path = Path(appearance.subject_video_asset_path)
            if not path.is_absolute():
                return project_dir / path
            try:
                path.relative_to(project_dir)
            except ValueError:
                pass
            else:
                return path
        return self.layout.video_asset_path(project_dir, "roles", asset_id)

    @staticmethod
    def _clean_intro_text(value: object, *, role: Role) -> str:
        text = " ".join(str(value or "").replace("\n", " ").split())
        text = text.strip().strip("“”\"'` ")
        text = text.replace("：", ":")
        if ":" in text:
            prefix, body = text.split(":", 1)
            if role.name in prefix or len(prefix) <= 8:
                text = body.strip()
        text = text.strip().strip("“”\"'` ")
        return text or f"我是{role.name}，请记住我的样子。"

    def _intro_text_prompt(self, role: Role, appearance: RoleAppearance) -> str:
        return "\n".join(
            [
                "为角色主体视频生成一句约5秒的中文自我介绍口播台词。",
                "要求：",
                "1. 第一人称，只能是一句自然台词，符合角色身份、气质和当前外观设定。",
                "2. 约5秒内能说完，控制在12到24个汉字左右。",
                "3. 不要写角色名标签、动作说明、括号、旁白、字幕、引号、分镜编号或舞台提示。",
                "4. 不要剧透剧情，只表达角色本人可被主体库识别的稳定气质。",
                "",
                f"角色名：{role.name}",
                f"角色简介：{role.intro}",
                f"外观设定：{appearance.desc or appearance.prompt or appearance.roleboard_prompt or ''}",
            ]
        )

    async def _generate_intro_text(
        self,
        role: Role,
        appearance: RoleAppearance,
        *,
        duration_seconds: float,
    ) -> str:
        provider = self.router.text("role", node_name="role_subject_video_intro_text")
        output = await provider.generate_json(
            self._intro_text_prompt(role, appearance),
            RoleSubjectVideoIntroTextOutput,
            temperature=0.5,
            metadata={
                "node_name": "role_subject_video_intro_text",
                "source_node": self.name,
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": appearance.id,
                "appearance_name": appearance.name,
                "target_duration_seconds": duration_seconds,
            },
        )
        return self._clean_intro_text(output.intro_text, role=role)

    async def _query_existing_subject_video_task(
        self,
        provider: object,
        *,
        external_task_id: str,
        role: Role,
        appearance: RoleAppearance,
    ) -> VideoGenerationResult:
        query_video_task = getattr(provider, "query_video_task", None)
        if query_video_task is None:
            raise ProviderError(
                f"{self.name} cannot recover existing subject video for {role.name}/{appearance.name}: "
                "provider does not support query_video_task"
            )
        success_statuses = self._video_success_statuses(provider)
        failure_statuses = self._video_failure_statuses(provider)
        max_polls = int(getattr(provider, "max_polls", 120))
        poll_interval_seconds = float(getattr(provider, "poll_interval_seconds", 5))
        last_result: VideoGenerationResult | None = None
        for poll_index in range(1, max_polls + 1):
            if poll_index > 1 and poll_interval_seconds > 0:
                await asyncio.sleep(poll_interval_seconds)
            result = await query_video_task(external_task_id)
            last_result = result
            status = str(result.task_status or "").strip().lower()
            if status in success_statuses:
                return result
            if status in failure_statuses:
                raise ProviderError(
                    f"{self.name} recovered existing task {external_task_id} for "
                    f"{role.name}/{appearance.name}, but it ended with status {result.task_status}"
                )
            if result.video_url and not status:
                return result
        last_status = last_result.task_status if last_result is not None else "-"
        raise ProviderError(
            f"{self.name} recovered existing task {external_task_id} for {role.name}/{appearance.name}, "
            f"but it did not finish after {max_polls} polls; last status={last_status}"
        )

    def _prompt(
        self,
        role: Role,
        appearance: RoleAppearance,
        *,
        has_key_vision: bool,
        intro_text: str,
    ) -> str:
        parts = [
            "生成一段用于可灵视频角色主体定制的写实人形角色展示视频，视频必须带角色本人自然口播声音。",
            "视频必须只展示同一个角色，干净背景或低干扰环境，不能出现其他人物、字幕、水印、logo、可读文字或镜头编号。",
            "<<<image_1>>> 是该角色身份板，必须严格保持脸型、五官、发型、体型比例、服装、配饰、材质和年龄感一致。",
        ]
        if has_key_vision:
            parts.append("<<<image_2>>> 是本剧主视觉，只作为整体写实质感、光影、色调和摄影审美参考。")
        parts.extend(
            [
                f"角色名：{role.name}",
                f"角色简介：{role.intro}",
                f"外观描述：{appearance.desc or appearance.prompt or appearance.roleboard_prompt or ''}",
                f"自我介绍口播台词：{intro_text}",
                "声音要求：角色必须用自然中文口播完整说出上面的自我介绍台词；口型、表情和节奏必须与台词同步，声音清晰靠前，不要背景音乐、旁白、混响夸张音效或字幕。",
                "动作设计：角色先以三分之二侧身静立，然后缓慢转向镜头旁侧，微微抬眼，进行一次自然呼吸和轻微手部动作；口播时表情克制自然，不要夸张表演。",
                "镜头要求：中近景到半身，稳定镜头，人物全程清晰，面部无遮挡，服装关键细节可见，便于后续主体库识别。",
            ]
        )
        return "\n".join(part for part in parts if part)

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._video_provider(node_name=self.name)
        if not self._provider_supports_subject_elements(provider):
            self.repo.save_node_output(
                project_dir,
                self.name,
                RoleSubjectVideoGenerationOutput(
                    generated_subject_videos=[],
                    skipped_subject_videos=[
                        {
                            "reason": "provider_does_not_support_subject_elements",
                            "provider": getattr(provider, "name", "unknown"),
                        }
                    ],
                ),
            )
            return state

        force = bool(getattr(self.workflow, "_force_pregen", False))
        generated: list[RoleSubjectVideoGenerationItem] = []
        skipped: list[dict[str, Any]] = []
        duration = self._subject_video_duration(provider)
        targets = self._target_role_appearances(state)
        concurrency = self._bounded_concurrency(
            provider,
            "role_subject_video_generation_concurrency",
            "subject_video_generation_concurrency",
            "video_generation_concurrency",
            "max_concurrent_videos",
            default=1,
            cap=5,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def process_target(
            role: Role,
            appearance: RoleAppearance,
        ) -> tuple[RoleSubjectVideoGenerationItem | None, dict[str, Any] | None]:
            asset_id = normalize_id(f"{appearance.id}", "subject_video")
            output_path = self.layout.video_asset_path(project_dir, "roles", asset_id)
            existing_video_path = self.layout.existing_project_file(project_dir, appearance.subject_video_asset_path)
            existing_has_intro_audio = bool(str(appearance.subject_video_intro_text or "").strip())
            if not force and existing_video_path and existing_has_intro_audio:
                return (
                    RoleSubjectVideoGenerationItem(
                        role_id=role.id,
                        role_name=role.name,
                        appearance_id=appearance.id,
                        appearance_name=appearance.name,
                        asset_id=appearance.subject_video_asset_id or asset_id,
                        prompt="",
                        intro_text=appearance.subject_video_intro_text,
                        duration_seconds=duration,
                        asset_path=existing_video_path or appearance.subject_video_asset_path,
                        asset_url=appearance.subject_video_asset_url,
                        provider=appearance.subject_video_provider or getattr(provider, "name", "unknown"),
                        model=appearance.subject_video_model or getattr(provider, "model", ""),
                        task_id=appearance.subject_video_task_id,
                        task_status=appearance.subject_video_task_status,
                        request_id=appearance.subject_video_request_id,
                        usage=appearance.subject_video_usage,
                        raw_response=appearance.subject_video_raw_response or {"resumed_from_existing_subject_video": True},
                    ),
                    None,
                )
            if not force and appearance.subject_video_asset_url and existing_has_intro_audio:
                resumed_asset_id = appearance.subject_video_asset_id or asset_id
                result = VideoGenerationResult(
                    provider=str(
                        appearance.subject_video_provider
                        or getattr(provider, "name", "unknown")
                        or "unknown"
                    ),
                    model=str(appearance.subject_video_model or getattr(provider, "model", "") or ""),
                    task_id=appearance.subject_video_task_id,
                    task_status=appearance.subject_video_task_status,
                    video_url=appearance.subject_video_asset_url,
                    request_id=appearance.subject_video_request_id,
                    usage=dict(appearance.subject_video_usage or {}),
                    raw_response=dict(
                        appearance.subject_video_raw_response or {"resumed_from_existing_subject_video": True}
                    ),
                )
                restored_asset_path = await self.media_store.write_generated_video(
                    project_dir,
                    self._subject_video_download_output_path(project_dir, appearance, resumed_asset_id),
                    result,
                )
                if not restored_asset_path:
                    raise ValueError(
                        f"{self.name} could not restore local subject video for "
                        f"{role.name}/{appearance.name}: missing video URL"
                    )
                appearance.subject_video_asset_id = resumed_asset_id
                appearance.subject_video_asset_path = restored_asset_path
                appearance.subject_video_provider = result.provider
                appearance.subject_video_model = result.model
                appearance.subject_video_task_id = result.task_id
                appearance.subject_video_task_status = result.task_status
                appearance.subject_video_request_id = result.request_id
                appearance.subject_video_usage = result.usage
                appearance.subject_video_raw_response = result.raw_response
                return (
                    RoleSubjectVideoGenerationItem(
                        role_id=role.id,
                        role_name=role.name,
                        appearance_id=appearance.id,
                        appearance_name=appearance.name,
                        asset_id=resumed_asset_id,
                        prompt="",
                        intro_text=appearance.subject_video_intro_text,
                        duration_seconds=duration,
                        asset_path=restored_asset_path,
                        asset_url=appearance.subject_video_asset_url,
                        provider=result.provider,
                        model=result.model,
                        task_id=result.task_id,
                        task_status=result.task_status,
                        request_id=result.request_id,
                        usage=result.usage,
                        raw_response=result.raw_response,
                    ),
                    None,
                )

            roleboard_ref = self._roleboard_ref(project_dir, role, appearance)
            if roleboard_ref is None:
                return None, {"role_id": role.id, "appearance_id": appearance.id, "reason": "missing_roleboard"}
            refs = [roleboard_ref]
            key_vision_ref = self._key_vision_ref(project_dir, state, reference_for=asset_id)
            if key_vision_ref is not None:
                refs.append(key_vision_ref)
            intro_text = await self._generate_intro_text(role, appearance, duration_seconds=duration)
            prompt = self._prompt(
                role,
                appearance,
                has_key_vision=key_vision_ref is not None,
                intro_text=intro_text,
            )
            external_task_id = f"{state.project_id}_{asset_id}_voiced"
            metadata = {
                "node_name": self.name,
                "project_id": state.project_id,
                "asset_id": asset_id,
                "asset_type": "role_subject_video",
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": appearance.id,
                "appearance_name": appearance.name,
                "duration": duration,
                "external_task_id": external_task_id,
                "intro_text": intro_text,
                "sound": "on",
                "parameters": {"sound": "on"},
            }
            try:
                result = await provider.generate_video(
                    prompt,
                    refs=refs,
                    duration=duration,
                    wait=True,
                    metadata=metadata,
                )
            except ProviderBadResponseError as exc:
                if not self._external_task_already_exists(exc):
                    raise
                result = await self._query_existing_subject_video_task(
                    provider,
                    external_task_id=external_task_id,
                    role=role,
                    appearance=appearance,
                )
            asset_path = await self.media_store.write_generated_video(project_dir, output_path, result)
            if not asset_path:
                raise ValueError(
                    f"{self.name} could not save local subject video for "
                    f"{role.name}/{appearance.name}: provider result has no downloadable video URL"
                )
            appearance.subject_video_asset_id = asset_id
            appearance.subject_video_asset_path = asset_path
            appearance.subject_video_asset_url = result.video_url
            appearance.subject_video_intro_text = intro_text
            appearance.subject_video_provider = result.provider or getattr(provider, "name", None)
            appearance.subject_video_model = result.model or getattr(provider, "model", None)
            appearance.subject_video_task_id = result.task_id
            appearance.subject_video_task_status = result.task_status
            appearance.subject_video_request_id = result.request_id
            appearance.subject_video_usage = result.usage
            appearance.subject_video_raw_response = result.raw_response
            return (
                RoleSubjectVideoGenerationItem(
                    role_id=role.id,
                    role_name=role.name,
                    appearance_id=appearance.id,
                    appearance_name=appearance.name,
                    asset_id=asset_id,
                    prompt=prompt,
                    intro_text=intro_text,
                    duration_seconds=duration,
                    asset_path=asset_path,
                    asset_url=result.video_url,
                    provider=result.provider or getattr(provider, "name", "unknown"),
                    model=result.model or getattr(provider, "model", ""),
                    task_id=result.task_id,
                    task_status=result.task_status,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                ),
                None,
            )

        async def run_target(
            role: Role,
            appearance: RoleAppearance,
        ) -> tuple[RoleSubjectVideoGenerationItem | None, dict[str, Any] | None]:
            async with semaphore:
                return await process_target(role, appearance)

        tasks = [asyncio.create_task(run_target(role, appearance)) for role, appearance in targets]
        try:
            results = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for item, skip in results:
            if item is not None:
                generated.append(item)
            if skip is not None:
                skipped.append(skip)

        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleSubjectVideoGenerationOutput(
                generated_subject_videos=generated,
                skipped_subject_videos=skipped,
            ),
        )
        return state


class RoleSubjectElementGenerationNode(RoleSubjectNodeBase):
    name = "role_subject_element_generation"

    @staticmethod
    def _external_task_already_exists(exc: ProviderBadResponseError) -> bool:
        text = str(exc)
        lowered = text.casefold()
        return "external_task_id" in lowered and ("already exists" in lowered or "已存在" in text)

    @staticmethod
    def _element_description(role: Role, appearance: RoleAppearance) -> str:
        text = " ".join(
            str(part or "").strip()
            for part in (
                role.name,
                role.intro,
                appearance.desc,
                appearance.prompt,
            )
            if str(part or "").strip()
        )
        return text[:100] or role.name[:100]

    def _image_refs(self, project_dir: Path, role: Role, appearance: RoleAppearance) -> list[AssetRef]:
        frontal_ref = self._frontal_image_ref(project_dir, role, appearance)
        roleboard_ref = self._kling_roleboard_ref(project_dir, role, appearance)
        return [ref for ref in (frontal_ref, roleboard_ref) if ref is not None]

    @staticmethod
    def _subject_success_statuses(provider: object) -> set[str]:
        statuses = getattr(provider, "_TERMINAL_SUCCESS", {"succeed", "succeeded", "success", "completed", "done"})
        return {str(status).strip().lower() for status in statuses}

    @staticmethod
    def _subject_failure_statuses(provider: object) -> set[str]:
        statuses = getattr(
            provider,
            "_TERMINAL_FAILURE",
            {"failed", "fail", "error", "expired", "cancelled", "canceled"},
        )
        return {str(status).strip().lower() for status in statuses}

    @staticmethod
    def _raw_response_excerpt(result: SubjectElementResult) -> str:
        try:
            text = json.dumps(result.raw_response or {}, ensure_ascii=False, default=str)
        except TypeError:
            text = repr(result.raw_response)
        return text[:1000]

    async def _query_existing_subject_element_task(
        self,
        provider: object,
        *,
        external_task_id: str,
        role: Role,
        appearance: RoleAppearance,
    ) -> SubjectElementResult:
        query_subject_element = getattr(provider, "query_subject_element", None)
        if query_subject_element is not None:
            async def query() -> SubjectElementResult:
                return await query_subject_element(external_task_id=external_task_id)
        else:
            query_subject_element_task = getattr(provider, "query_subject_element_task", None)
            if query_subject_element_task is None:
                raise ProviderError(
                    f"{self.name} cannot recover existing subject element for {role.name}/{appearance.name}: "
                    "provider does not support query_subject_element"
                )

            async def query() -> SubjectElementResult:
                return await query_subject_element_task(external_task_id)

        success_statuses = self._subject_success_statuses(provider)
        failure_statuses = self._subject_failure_statuses(provider)
        max_polls = int(getattr(provider, "max_polls", 120))
        poll_interval_seconds = float(getattr(provider, "poll_interval_seconds", 5))
        last_result: SubjectElementResult | None = None
        for poll_index in range(1, max_polls + 1):
            if poll_index > 1 and poll_interval_seconds > 0:
                await asyncio.sleep(poll_interval_seconds)
            result = await query()
            last_result = result
            status = str(result.task_status or "").strip().lower()
            if status in success_statuses:
                return result
            if status in failure_statuses:
                raise ProviderError(
                    f"{self.name} recovered existing task {external_task_id} for "
                    f"{role.name}/{appearance.name}, but it ended with status {result.task_status}"
                )
            if result.element_id and not status:
                return result
        last_status = last_result.task_status if last_result is not None else "-"
        raise ProviderError(
            f"{self.name} recovered existing task {external_task_id} for {role.name}/{appearance.name}, "
            f"but it did not finish after {max_polls} polls; last status={last_status}"
        )

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._video_provider(node_name=self.name)
        if not self._provider_supports_subject_elements(provider):
            self.repo.save_node_output(
                project_dir,
                self.name,
                RoleSubjectElementGenerationOutput(
                    generated_subject_elements=[],
                    skipped_subject_elements=[
                        {
                            "reason": "provider_does_not_support_subject_elements",
                            "provider": getattr(provider, "name", "unknown"),
                        }
                    ],
                ),
            )
            return state

        force = bool(getattr(self.workflow, "_force_pregen", False))
        reference_type = self._subject_reference_type(provider)
        generated: list[RoleSubjectElementGenerationItem] = []
        skipped: list[dict[str, Any]] = []
        for role, appearance in self._target_role_appearances(state):
            voice_binding_current = appearance.subject_element_voice_id == role.kling_voice_id
            if not force and appearance.subject_element_id and voice_binding_current:
                generated.append(
                    RoleSubjectElementGenerationItem(
                        role_id=role.id,
                        role_name=role.name,
                        appearance_id=appearance.id,
                        appearance_name=appearance.name,
                        reference_type=appearance.subject_element_reference_type or reference_type,
                        element_id=appearance.subject_element_id,
                        voice_id=appearance.subject_element_voice_id,
                        provider=appearance.subject_element_provider or getattr(provider, "name", "unknown"),
                        model=appearance.subject_element_model or getattr(provider, "subject_element_model", ""),
                        task_id=appearance.subject_element_task_id,
                        task_status=appearance.subject_element_task_status,
                        request_id=appearance.subject_element_request_id,
                        usage=appearance.subject_element_usage,
                        raw_response=appearance.subject_element_raw_response or {"resumed_from_existing_subject_element": True},
                    )
                )
                continue

            video_url = appearance.subject_video_asset_url
            image_refs = self._image_refs(project_dir, role, appearance) if reference_type == "image_refer" else []
            if reference_type == "video_refer" and not video_url:
                raise ValueError(
                    f"{self.name} requires subject video URL for {role.name}/{appearance.name}; "
                    "run role_subject_video_generation with Kling first"
                )
            if reference_type == "image_refer" and len(image_refs) != 2:
                raise ValueError(
                    f"{self.name} requires exactly one generated frontal image plus one roleboard image for "
                    f"{role.name}/{appearance.name}; run role_subject_frontal_image_generation first"
                )

            external_task_id = f"{state.project_id}_{appearance.id}_subject_element"
            metadata = {
                "node_name": self.name,
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": appearance.id,
                "appearance_name": appearance.name,
                "external_task_id": external_task_id,
            }
            if role.has_dialogue:
                if not role.kling_voice_id:
                    raise ValueError(
                        f"{self.name} requires a Kling voice for dialogue role {role.name}; "
                        "run role_kling_voice_generation first"
                    )
                metadata["element_voice_id"] = role.kling_voice_id
                external_task_id = f"{external_task_id}_{normalize_id(role.kling_voice_id, 'voice')}"
                metadata["external_task_id"] = external_task_id
            try:
                result = await provider.generate_subject_element(
                    element_name=role.name[:20],
                    element_description=self._element_description(role, appearance),
                    reference_type=reference_type,
                    video_url=video_url,
                    image_refs=image_refs,
                    wait=True,
                    metadata=metadata,
                )
            except ProviderBadResponseError as exc:
                if not self._external_task_already_exists(exc):
                    raise
                result = await self._query_existing_subject_element_task(
                    provider,
                    external_task_id=external_task_id,
                    role=role,
                    appearance=appearance,
                )
            if not result.element_id:
                raise ValueError(
                    f"Kling subject element task for {role.name}/{appearance.name} returned no element_id; "
                    f"task_id={result.task_id or '-'} status={result.task_status or '-'} "
                    f"request_id={result.request_id or '-'} raw_response={self._raw_response_excerpt(result)}"
                )
            appearance.subject_element_provider = result.provider or getattr(provider, "name", None)
            appearance.subject_element_model = result.model or getattr(provider, "subject_element_model", None)
            appearance.subject_element_reference_type = reference_type
            appearance.subject_element_voice_id = role.kling_voice_id
            appearance.subject_element_id = result.element_id
            appearance.subject_element_task_id = result.task_id
            appearance.subject_element_task_status = result.task_status
            appearance.subject_element_request_id = result.request_id
            appearance.subject_element_usage = result.usage
            appearance.subject_element_raw_response = result.raw_response
            generated.append(
                RoleSubjectElementGenerationItem(
                    role_id=role.id,
                    role_name=role.name,
                    appearance_id=appearance.id,
                    appearance_name=appearance.name,
                    reference_type=reference_type,
                    element_id=result.element_id,
                    voice_id=role.kling_voice_id,
                    provider=result.provider or getattr(provider, "name", "unknown"),
                    model=result.model or getattr(provider, "subject_element_model", ""),
                    task_id=result.task_id,
                    task_status=result.task_status,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )

        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleSubjectElementGenerationOutput(
                generated_subject_elements=generated,
                skipped_subject_elements=skipped,
            ),
        )
        return state


def build_role_subject_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = {
        RoleSubjectFrontalImageGenerationNode.name: RoleSubjectFrontalImageGenerationNode(workflow=workflow),
        RoleKlingVoiceGenerationNode.name: RoleKlingVoiceGenerationNode(workflow=workflow),
        RoleSubjectVideoGenerationNode.name: RoleSubjectVideoGenerationNode(workflow=workflow),
        RoleSubjectElementGenerationNode.name: RoleSubjectElementGenerationNode(workflow=workflow),
    }
    return [WorkflowNode(name=node_name, run=runners[node_name].run) for node_name in ROLE_SUBJECT_NODE_NAMES]


__all__ = [
    "ROLE_SUBJECT_NODE_NAMES",
    "RoleKlingVoiceGenerationNode",
    "RoleSubjectElementGenerationNode",
    "RoleSubjectFrontalImageGenerationNode",
    "RoleSubjectVideoGenerationNode",
    "build_role_subject_nodes",
]
