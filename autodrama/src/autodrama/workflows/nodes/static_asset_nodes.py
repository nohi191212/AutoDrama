from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    Layout,
    ProjectState,
    Prop,
    RoleAppearance,
    StaticAssetGenerationItem,
    StaticAssetGenerationOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.prop_design_repo import PropDesignRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.media_store import MediaStore
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

STATIC_ASSET_NODE_NAMES = [
    "role_appearance_generation",
    "prop_design",
    "prop_image_generation",
    "script_compress",
    "layout_design",
    "layout_dedupe_review",
    "layout_image_generation",
]


class StaticAssetNodeBase:
    def __init__(
        self,
        *,
        workflow: Any,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        script_service: ScriptService,
        asset_service: AssetService,
        script_contents: ScriptContentRepository,
        prop_designs: PropDesignRepository,
        media_store: MediaStore,
        logger: Any,
    ) -> None:
        self.workflow = workflow
        self.repo = repo
        self.layout = layout
        self.router = router
        self.script_service = script_service
        self.asset_service = asset_service
        self.script_contents = script_contents
        self.prop_designs = prop_designs
        self.media_store = media_store
        self.logger = logger

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

    def validate_episode_keys(self, label: str, payload: dict[str, object], state: ProjectState) -> None:
        expected_keys = self.expected_episode_keys(state)
        expected = set(expected_keys)
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                f"{label} must contain exactly {', '.join(expected_keys)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    def episode_stories(self, project_dir: Path, state: ProjectState) -> dict[str, str]:
        episode_keys = self.expected_episode_keys(state)
        refs = state.script.novel_extract
        if not any(refs.get(episode_key) for episode_key in episode_keys):
            refs = state.script.novel_full
        return self.script_contents.load_contents(
            project_dir,
            refs,
            episode_keys,
            label="episode_stories",
        )

    def novel_full_contents(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str] | None = None,
        *,
        allow_missing: bool = False,
    ) -> dict[str, str]:
        selected_keys = episode_keys or self.expected_episode_keys(state)
        return self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            selected_keys,
            label="script_novel.novel_full",
            allow_missing=allow_missing,
        )

    @staticmethod
    def dedupe_texts(values: list[object]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            result.append(text)
            seen.add(text)
        return result

    @staticmethod
    def prop_status_key(value: object) -> str:
        status = str(value or "normal").strip().lower()
        return slugify(status, fallback="normal").lower() or "normal"

    @classmethod
    def prop_asset_id(cls, name: str, status: object) -> str:
        prop_id = normalize_id("prop", name)
        status_key = cls.prop_status_key(status)
        if status_key != "normal" and not prop_id.endswith(f"_{status_key}"):
            prop_id = f"{prop_id}_{status_key}"
        return prop_id

    def prop_episode_keys(self, name: str, episode_keys: list[str], state: ProjectState) -> list[str]:
        expected_keys = self.expected_episode_keys(state)
        expected = set(expected_keys)
        cleaned = self.dedupe_texts(episode_keys)
        invalid = [episode_key for episode_key in cleaned if episode_key not in expected]
        if invalid:
            raise ValueError(
                f"prop_design generated invalid episode_keys for {name}: "
                f"{', '.join(invalid)}; expected one of {', '.join(expected_keys)}"
            )
        if not cleaned:
            raise ValueError(f"prop_design must include episode_keys for {name}")
        selected = set(cleaned)
        return [episode_key for episode_key in expected_keys if episode_key in selected]

    def save_prop_design_record(
        self,
        project_dir: Path,
        prop: Prop,
        *,
        prompt: str,
        node_name: str,
        extra_content: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        return self.prop_designs.save_record(
            project_dir,
            prop,
            prompt=prompt,
            node_name=node_name,
            extra_content=extra_content,
            extra_payload=extra_payload,
        )

    def prop_prompt_for_generation(self, project_dir: Path, prop: Prop) -> str:
        prompt = str(prop.prompt or "").strip()
        if prompt:
            return prompt
        content = self.prop_designs.load_content(project_dir, prop)
        prompt = str(content.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Prop {prop.id} is missing prompt; expected it in state or {prop.design_path}")
        prop.prompt = prompt
        return prompt

    def update_prop_design_image_result(self, project_dir: Path, prop: Prop, result, asset_path: str) -> None:
        self.prop_designs.update_image_result(project_dir, prop, result, asset_path)

    @classmethod
    def prop_variant_base_name(cls, prop: Prop) -> str:
        name = str(prop.name).strip()
        status_key = cls.prop_status_key(prop.status)
        suffixes = [status_key]
        if status_key != "normal":
            suffixes.append("normal")
        for suffix in suffixes:
            for separator in ("_", "-"):
                marker = f"{separator}{suffix}"
                if name.lower().endswith(marker) and len(name) > len(marker):
                    return name[: -len(marker)].strip() or name
        return name

    @classmethod
    def prop_variant_base_key(cls, prop: Prop) -> str:
        return normalize_id("prop", cls.prop_variant_base_name(prop))

    @classmethod
    def ordered_props_for_generation(cls, props: list[Prop]) -> list[Prop]:
        normal_base_keys = {
            cls.prop_variant_base_key(prop)
            for prop in props
            if cls.prop_status_key(prop.status) == "normal"
        }
        indexed = list(enumerate(props))
        ordered = sorted(
            indexed,
            key=lambda item: (
                1
                if cls.prop_status_key(item[1].status) != "normal"
                and cls.prop_variant_base_key(item[1]) in normal_base_keys
                else 0,
                item[0],
            ),
        )
        return [prop for _, prop in ordered]

    @classmethod
    def normal_props_by_variant_base(cls, props: list[Prop]) -> dict[str, Prop]:
        normal_props: dict[str, Prop] = {}
        for prop in props:
            if cls.prop_status_key(prop.status) != "normal":
                continue
            normal_props.setdefault(cls.prop_variant_base_key(prop), prop)
        return normal_props

    @classmethod
    def prop_reference_refs(
        cls,
        project_dir: Path,
        prop: Prop,
        normal_props_by_base: dict[str, Prop],
    ) -> list | None:
        if cls.prop_status_key(prop.status) == "normal":
            return None
        normal_prop = normal_props_by_base.get(cls.prop_variant_base_key(prop))
        if normal_prop is None or normal_prop is prop or not normal_prop.asset_path:
            return None
        reference_path = project_dir / normal_prop.asset_path
        if not reference_path.exists() or not reference_path.is_file():
            raise ValueError(
                f"Cannot use normal prop reference for {prop.id}: missing file {normal_prop.asset_path}"
            )

        from autodrama.providers.base import AssetRef

        return [
            AssetRef(
                id=normal_prop.asset_id or normal_prop.id,
                type="image",
                path=str(reference_path),
                metadata={
                    "asset_type": "prop",
                    "name": normal_prop.name,
                    "status": normal_prop.status,
                    "reference_for": prop.id,
                },
            )
        ]

    @staticmethod
    def role_bound_prop_reference_refs(project_dir: Path, state: ProjectState, prop: Prop) -> list | None:
        if not prop.owner_role_id:
            return None
        role = state.roles.get(prop.owner_role_id)
        if role is None:
            return None
        appearance = next(
            (
                item
                for item in role.appearances.values()
                if prop.id in item.role_bound_prop_ids and item.design_image_asset_path
            ),
            None,
        )
        if appearance is None or not appearance.design_image_asset_path:
            return None
        reference_path = project_dir / appearance.design_image_asset_path
        if not reference_path.exists() or not reference_path.is_file():
            raise ValueError(
                f"Cannot use role appearance reference for {prop.id}: missing file {appearance.design_image_asset_path}"
            )

        from autodrama.providers.base import AssetRef

        return [
            AssetRef(
                id=appearance.design_image_asset_id or appearance.id,
                type="image",
                path=str(reference_path),
                metadata={
                    "asset_type": "role_appearance",
                    "role_id": role.id,
                    "role_name": role.name,
                    "reference_for": prop.id,
                },
            )
        ]


class RoleAppearanceDesignNode(StaticAssetNodeBase):
    name = "role_appearance_design"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        self.logger.info(
            "node=role_appearance_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.role_appearance_design(
            state,
            provider,
            episode_stories=self.episode_stories(project_dir, state),
        )
        roles_by_key = self.workflow._role_lookup(state)
        unmatched_role_names: list[str] = []
        for item in output.appearances:
            role = self.workflow._resolve_role(roles_by_key, item.role_name)
            if role is None:
                unmatched_role_names.append(item.role_name)
                continue
            appearance_id = normalize_id(f"{role.id}_appearance", item.name)
            role_bound_prop_ids: list[str] = []
            for prop_item in item.role_bound_props:
                prop_id = normalize_id(f"{role.id}_prop", prop_item.name)
                status_key = self.prop_status_key(prop_item.status)
                if status_key != "normal" and not prop_id.endswith(f"_{status_key}"):
                    prop_id = f"{prop_id}_{status_key}"
                desc_parts = [prop_item.desc]
                if prop_item.scale_relation:
                    desc_parts.append(f"比例关系：{prop_item.scale_relation}")
                if prop_item.usage:
                    desc_parts.append(f"使用方式：{prop_item.usage}")
                prop = Prop(
                    id=prop_id,
                    name=prop_item.name,
                    desc="；".join(part.strip("；") for part in desc_parts if part),
                    prompt=prop_item.prompt,
                    status=prop_item.status,
                    episode_keys=role.episode_keys,
                    owner_role_id=role.id,
                    owner_role_name=role.name,
                    source="role_appearance_design",
                )
                prop.design_path = self.save_prop_design_record(
                    project_dir,
                    prop,
                    prompt=prop_item.prompt,
                    node_name=self.name,
                    extra_content={
                        "scale_relation": prop_item.scale_relation,
                        "usage": prop_item.usage,
                    },
                )
                state.props[prop_id] = prop
                role_bound_prop_ids.append(prop_id)
            role.appearances[item.name] = RoleAppearance(
                id=appearance_id,
                role_id=role.id,
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                role_bound_prop_ids=role_bound_prop_ids,
                intro_video_prompt=item.intro_video_prompt,
            )
        if unmatched_role_names:
            self.logger.warning(
                "node=role_appearance_design ignored unmatched role_name values: %s",
                ", ".join(unmatched_role_names),
            )
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class RoleAppearanceGenerationNode(StaticAssetNodeBase):
    name = "role_appearance_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role")
        try:
            video_provider = self.router.video("role")
        except Exception:
            video_provider = self.router.video("shot")
        self.logger.info(
            "node=role_appearance_generation image_provider=%s image_model=%s video_provider=%s video_model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            getattr(video_provider, "name", "unknown"),
            getattr(video_provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        generated: list[StaticAssetGenerationItem] = []
        appearances = [
            (role, appearance)
            for role in state.roles.values()
            for appearance in role.appearances.values()
        ]
        self.logger.info("node=role_appearance_generation total_images=%d", len(appearances))
        reuse_existing_assets = not bool(getattr(self.workflow, "_force_pregen", False))
        for role, appearance in appearances:
            if not appearance.prompt:
                raise ValueError(
                    f"Cannot generate role appearance for {role.name}/{appearance.name}: "
                    "missing prompt in role design JSON"
                )
            image_output_path = self.layout.image_asset_path(project_dir, "roles", appearance.id)
            existing_image_path = self.layout.existing_project_file(project_dir, appearance.design_image_asset_path)
            if existing_image_path is None:
                existing_image_path = self.layout.existing_project_file(project_dir, image_output_path)
            if reuse_existing_assets and existing_image_path is not None:
                asset_path = existing_image_path
                image_provider = str(appearance.provider or getattr(provider, "name", "unknown"))
                image_model = str(appearance.model or getattr(provider, "model", ""))
                image_request_id = appearance.request_id
                image_usage = appearance.usage
                image_raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", appearance.id, asset_path)
            else:
                self.logger.info("%s generating role appearance image for %s/%s", appearance.id, role.name, appearance.name)
                result = await provider.generate_image(
                    appearance.prompt,
                    metadata={
                        "node_name": self.name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": appearance.id,
                        "asset_type": "role_appearance",
                    },
                )
                asset_path = await self.media_store.write_first_generated_image(project_dir, image_output_path, result)
                image_provider = result.provider
                image_model = result.model
                image_request_id = result.request_id
                image_usage = result.usage
                image_raw_response = result.raw_response
                self.logger.info("%s generated successfully, saved in %s", appearance.id, asset_path)
            appearance.asset_id = appearance.id
            appearance.asset_path = asset_path
            appearance.design_image_asset_id = appearance.id
            appearance.design_image_asset_path = asset_path
            appearance.design_image_generation_status = "generated"
            appearance.provider = image_provider
            appearance.model = image_model
            appearance.request_id = image_request_id
            appearance.usage = image_usage
            for prop_id in appearance.role_bound_prop_ids:
                prop = state.props.get(prop_id)
                if prop is not None:
                    prop.asset_id = appearance.id
                    prop.asset_path = asset_path
                    prop.provider = image_provider
                    prop.model = image_model
                    prop.request_id = image_request_id
                    prop.usage = image_usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=appearance.id,
                    asset_type="role_appearance",
                    owner_id=role.id,
                    name=f"{role.name}/{appearance.name}",
                    prompt=appearance.prompt,
                    asset_path=asset_path,
                    provider=image_provider,
                    model=image_model,
                    request_id=image_request_id,
                    usage=image_usage,
                    raw_response=image_raw_response,
                )
            )

            intro_video_asset_id = f"{appearance.id}_intro_video"
            intro_prompt = appearance.intro_video_prompt or (
                f"参考图片1中的人物外观和绑定物品设计，{role.name}站在洁净、亮度适中的虚空圆台上；"
                f"0-2 秒：圆台缓慢转动，人物保持{appearance.desc}的稳定外观，镜头以中景平稳观察；"
                "2-5 秒：人物做几个符合身份和性格的常见动作，如有随身物品，展示佩戴、握持或使用方式；"
                "5-8 秒：镜头轻微推近并停在人物稳定识别角度，背景保持干净抽象，无其他人物、无字幕、水印或文字标识。"
            )
            video_output_path = self.layout.video_asset_path(project_dir, "roles", intro_video_asset_id)
            existing_video_path = self.layout.existing_project_file(project_dir, appearance.intro_video_asset_path)
            if existing_video_path is None:
                existing_video_path = self.layout.existing_project_file(project_dir, video_output_path)
            if reuse_existing_assets and existing_video_path is not None:
                video_asset_path = existing_video_path
                video_provider_name = str(getattr(video_provider, "name", "unknown"))
                video_model = str(getattr(video_provider, "model", ""))
                video_request_id = None
                video_usage: dict[str, Any] = {}
                video_raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", intro_video_asset_id, video_asset_path)
            else:
                from autodrama.providers.base import AssetRef

                refs = [
                    AssetRef(
                        id=appearance.id,
                        type="image",
                        path=str(project_dir / asset_path),
                        metadata={
                            "asset_type": "role_appearance",
                            "role_id": role.id,
                            "role_name": role.name,
                            "reference_for": intro_video_asset_id,
                        },
                    )
                ]
                video_result = await video_provider.generate_video(
                    intro_prompt,
                    refs=refs,
                    duration=8,
                    wait=True,
                    metadata={
                        "node_name": self.name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": intro_video_asset_id,
                        "asset_type": "role_appearance_video",
                    },
                )
                video_asset_path = await self.media_store.write_generated_video(project_dir, video_output_path, video_result)
                video_provider_name = video_result.provider
                video_model = video_result.model
                video_request_id = video_result.request_id
                video_usage = video_result.usage
                video_raw_response = video_result.raw_response
                self.logger.info("%s generated successfully, saved in %s", intro_video_asset_id, video_asset_path)
            appearance.intro_video_asset_id = intro_video_asset_id
            appearance.intro_video_asset_path = video_asset_path
            appearance.intro_video_generation_status = "generated"
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=intro_video_asset_id,
                    asset_type="role_appearance_video",
                    owner_id=role.id,
                    name=f"{role.name}/{appearance.name}/intro_video",
                    prompt=intro_prompt,
                    asset_path=video_asset_path,
                    provider=video_provider_name,
                    model=video_model,
                    request_id=video_request_id,
                    usage=video_usage,
                    raw_response=video_raw_response,
                )
            )
        self.repo.save_node_output(project_dir, self.name, StaticAssetGenerationOutput(generated_assets=generated))
        return state


class PropDesignNode(StaticAssetNodeBase):
    name = "prop_design"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop")
        self.logger.info(
            "node=prop_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.prop_design(
            state,
            provider,
            novel_full=self.novel_full_contents(project_dir, state),
        )
        existing_props = dict(state.props)
        role_bound_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.source in {"role_design", "role_appearance_design"} or prop.owner_role_id
        }
        for prop in role_bound_props.values():
            if not prop.design_path and prop.prompt:
                prop.design_path = self.save_prop_design_record(
                    project_dir,
                    prop,
                    prompt=prop.prompt,
                    node_name=prop.source or self.name,
                )
        global_props: dict[str, Prop] = {}
        for item in output.props:
            item.episode_keys = self.prop_episode_keys(item.name, item.episode_keys, state)
            prop_id = self.prop_asset_id(item.name, item.status)
            prop = Prop(
                id=prop_id,
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                status=item.status,
                episode_keys=item.episode_keys,
                source=self.name,
            )
            existing_prop = existing_props.get(prop_id)
            if existing_prop is not None:
                prop.asset_id = existing_prop.asset_id
                prop.asset_path = existing_prop.asset_path
                prop.provider = existing_prop.provider
                prop.model = existing_prop.model
                prop.request_id = existing_prop.request_id
                prop.usage = existing_prop.usage
            prop.design_path = self.save_prop_design_record(
                project_dir,
                prop,
                prompt=item.prompt,
                node_name=self.name,
                extra_payload={
                    "source_novel_full_paths": {
                        episode_key: state.script.novel_full.get(episode_key)
                        for episode_key in item.episode_keys
                    }
                },
            )
            global_props[prop_id] = prop
        state.props = {**role_bound_props, **global_props}
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class PropImageGenerationNode(StaticAssetNodeBase):
    name = "prop_image_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("prop")
        self.logger.info(
            "node=prop_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        props = self.ordered_props_for_generation(list(state.props.values()))
        normal_props_by_base = self.normal_props_by_variant_base(props)
        self.logger.info("node=prop_image_generation total_images=%d", len(props))
        for prop in props:
            prompt = self.prop_prompt_for_generation(project_dir, prop)
            refs = None
            if getattr(provider, "supports_reference_images", False):
                refs = self.role_bound_prop_reference_refs(project_dir, state, prop)
                if refs is None:
                    refs = self.prop_reference_refs(project_dir, prop, normal_props_by_base)
            result = await provider.generate_image(
                prompt,
                refs=refs,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "prop_id": prop.id,
                    "asset_id": prop.id,
                },
            )
            asset_path = await self.media_store.write_first_generated_image(
                project_dir,
                self.layout.image_asset_path(project_dir, "props", prop.id),
                result,
            )
            prop.asset_id = prop.id
            prop.asset_path = asset_path
            prop.provider = result.provider
            prop.model = result.model
            prop.request_id = result.request_id
            prop.usage = result.usage
            self.update_prop_design_image_result(project_dir, prop, result, asset_path)
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=prop.id,
                    asset_type="prop",
                    owner_id=prop.id,
                    name=prop.name,
                    prompt=prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
            self.logger.info("%s generated successfully, saved in %s", prop.id, asset_path)
        self.repo.save_node_output(project_dir, self.name, StaticAssetGenerationOutput(generated_assets=generated))
        return state


class ScriptCompressNode(StaticAssetNodeBase):
    name = "script_compress"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        self.logger.info(
            "node=script_compress provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.script_compress(
            state,
            provider,
            episode_stories=self.episode_stories(project_dir, state),
        )
        self.validate_episode_keys("script_compress.simple_script", output.simple_script, state)
        state.metadata["simple_script"] = output.simple_script
        state.metadata["global_script"] = output.global_script
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutDesignNode(StaticAssetNodeBase):
    name = "layout_design"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        self.logger.info(
            "node=layout_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.layout_design(
            state,
            provider,
            episode_stories=self.episode_stories(project_dir, state),
        )
        state.layouts = {
            normalize_id("layout", item.name): Layout(
                id=normalize_id("layout", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
            for item in output.layouts
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutDedupeReviewNode(StaticAssetNodeBase):
    name = "layout_dedupe_review"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        self.logger.info(
            "node=layout_dedupe_review provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.layout_dedupe_review(state, provider)
        state.layouts = {
            normalize_id("layout", item.name): Layout(
                id=normalize_id("layout", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
            for item in output.layouts
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutImageGenerationNode(StaticAssetNodeBase):
    name = "layout_image_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("layout")
        self.logger.info(
            "node=layout_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        layouts = list(state.layouts.values())
        self.logger.info("node=layout_image_generation total_images=%d", len(layouts))
        for layout in layouts:
            result = await provider.generate_image(
                layout.prompt,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "layout_id": layout.id,
                    "asset_id": layout.id,
                },
            )
            asset_path = await self.media_store.write_first_generated_image(
                project_dir,
                self.layout.image_asset_path(project_dir, "layouts", layout.id),
                result,
            )
            layout.asset_id = layout.id
            layout.asset_path = asset_path
            layout.provider = result.provider
            layout.model = result.model
            layout.request_id = result.request_id
            layout.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=layout.id,
                    asset_type="layout",
                    owner_id=layout.id,
                    name=layout.name,
                    prompt=layout.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
            self.logger.info("%s generated successfully, saved in %s", layout.id, asset_path)
        self.repo.save_node_output(project_dir, self.name, StaticAssetGenerationOutput(generated_assets=generated))
        return state


def build_static_asset_node_runners(workflow: Any) -> dict[str, StaticAssetNodeBase]:
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)
    prop_designs = getattr(workflow, "prop_designs", None)
    if prop_designs is None:
        prop_designs = PropDesignRepository(workflow.repo, workflow.layout)
    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "asset_service": workflow.asset_service,
        "script_contents": script_contents,
        "prop_designs": prop_designs,
        "media_store": workflow.media_store,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        RoleAppearanceDesignNode.name: RoleAppearanceDesignNode(**deps),
        RoleAppearanceGenerationNode.name: RoleAppearanceGenerationNode(**deps),
        PropDesignNode.name: PropDesignNode(**deps),
        PropImageGenerationNode.name: PropImageGenerationNode(**deps),
        ScriptCompressNode.name: ScriptCompressNode(**deps),
        LayoutDesignNode.name: LayoutDesignNode(**deps),
        LayoutDedupeReviewNode.name: LayoutDedupeReviewNode(**deps),
        LayoutImageGenerationNode.name: LayoutImageGenerationNode(**deps),
    }


def build_static_asset_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_static_asset_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in STATIC_ASSET_NODE_NAMES
    ]


__all__ = [
    "STATIC_ASSET_NODE_NAMES",
    "LayoutDedupeReviewNode",
    "LayoutDesignNode",
    "LayoutImageGenerationNode",
    "PropDesignNode",
    "PropImageGenerationNode",
    "RoleAppearanceDesignNode",
    "RoleAppearanceGenerationNode",
    "ScriptCompressNode",
    "StaticAssetNodeBase",
    "build_static_asset_node_runners",
    "build_static_asset_nodes",
]
