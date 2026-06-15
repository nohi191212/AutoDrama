from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from autodrama.core.errors import ProviderBadResponseError, ProviderError
from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    ProjectState,
    Role,
    RoleAppearance,
    RoleSubjectElementGenerationItem,
    RoleSubjectElementGenerationOutput,
    RoleSubjectVideoGenerationItem,
    RoleSubjectVideoGenerationOutput,
    RoleSubjectVideoIntroTextOutput,
)
from autodrama.providers.base import AssetRef, VideoGenerationResult
from autodrama.workflows.runner import WorkflowNode


ROLE_SUBJECT_NODE_NAMES = [
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
                "reference_source": "roleboard_generation",
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
                "reference_source": "design_key_vision_image",
                "reference_role": "style_world_reference",
                "reference_for": reference_for,
                "name": name,
            },
        )

    def _subject_reference_type(self, provider: object) -> str:
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        return str(options.get("subject_reference_type") or "video_refer").strip()

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
        ref = self._roleboard_ref(project_dir, role, appearance)
        return [ref] if ref is not None else []

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
            if not force and appearance.subject_element_id:
                generated.append(
                    RoleSubjectElementGenerationItem(
                        role_id=role.id,
                        role_name=role.name,
                        appearance_id=appearance.id,
                        appearance_name=appearance.name,
                        reference_type=appearance.subject_element_reference_type or reference_type,
                        element_id=appearance.subject_element_id,
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
            if reference_type == "image_refer" and not image_refs:
                raise ValueError(f"{self.name} requires roleboard image for {role.name}/{appearance.name}")

            result = await provider.generate_subject_element(
                element_name=role.name[:20],
                element_description=self._element_description(role, appearance),
                reference_type=reference_type,
                video_url=video_url,
                image_refs=image_refs,
                wait=True,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "role_id": role.id,
                    "role_name": role.name,
                    "appearance_id": appearance.id,
                    "appearance_name": appearance.name,
                    "external_task_id": f"{state.project_id}_{appearance.id}_subject_element",
                },
            )
            if not result.element_id:
                raise ValueError(f"Kling subject element task for {role.name}/{appearance.name} returned no element_id")
            appearance.subject_element_provider = result.provider or getattr(provider, "name", None)
            appearance.subject_element_model = result.model or getattr(provider, "subject_element_model", None)
            appearance.subject_element_reference_type = reference_type
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
        RoleSubjectVideoGenerationNode.name: RoleSubjectVideoGenerationNode(workflow=workflow),
        RoleSubjectElementGenerationNode.name: RoleSubjectElementGenerationNode(workflow=workflow),
    }
    return [WorkflowNode(name=node_name, run=runners[node_name].run) for node_name in ROLE_SUBJECT_NODE_NAMES]


__all__ = [
    "ROLE_SUBJECT_NODE_NAMES",
    "RoleSubjectElementGenerationNode",
    "RoleSubjectVideoGenerationNode",
    "build_role_subject_nodes",
]
