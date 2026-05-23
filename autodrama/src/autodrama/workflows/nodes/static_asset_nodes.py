from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    Layout,
    ProjectState,
    Prop,
    PropDesignItem,
    PropDesignOutput,
    PropExtractItem,
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
    "prop_extract",
    "prop_design",
    "prop_generation",
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
    def prop_name_key(name: object) -> str:
        return str(name or "").strip().casefold()

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

    def prop_episode_keys(
        self,
        name: str,
        episode_keys: list[str],
        state: ProjectState,
        *,
        label: str = "prop_design",
    ) -> list[str]:
        expected_keys = self.expected_episode_keys(state)
        expected = set(expected_keys)
        cleaned = self.dedupe_texts(episode_keys)
        invalid = [episode_key for episode_key in cleaned if episode_key not in expected]
        if invalid:
            raise ValueError(
                f"{label} generated invalid episode_keys for {name}: "
                f"{', '.join(invalid)}; expected one of {', '.join(expected_keys)}"
            )
        if not cleaned:
            raise ValueError(f"{label} must include episode_keys for {name}")
        selected = set(cleaned)
        return [episode_key for episode_key in expected_keys if episode_key in selected]

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

    @staticmethod
    def intersects_active_episode_keys(episode_keys: list[str], active_episode_keys: list[str]) -> bool:
        if not active_episode_keys:
            return True
        return bool(set(episode_keys).intersection(active_episode_keys))

    def prop_matches_active_episode_keys(self, prop: Prop, active_episode_keys: list[str]) -> bool:
        if not active_episode_keys:
            return True
        return self.intersects_active_episode_keys(self.dedupe_texts(prop.episode_keys), active_episode_keys)

    def target_prop_extract_items(
        self,
        extract_items: list[PropExtractItem],
        state: ProjectState,
        active_episode_keys: list[str],
    ) -> list[PropExtractItem]:
        if not active_episode_keys:
            return extract_items
        target_items: list[PropExtractItem] = []
        for item in extract_items:
            try:
                episode_keys = self.prop_episode_keys(item.name, item.episode_keys, state, label="prop_extract")
            except ValueError:
                continue
            if self.intersects_active_episode_keys(episode_keys, active_episode_keys):
                target_items.append(item)
        return target_items

    @classmethod
    def prop_extract_key(cls, item: PropExtractItem | PropDesignItem) -> str:
        return cls.prop_asset_id(item.name, item.status)

    def select_prop_design_item(self, output: PropDesignOutput, extract_item: PropExtractItem) -> PropDesignItem:
        if not output.props:
            raise ValueError(f"prop_design returned no props for {extract_item.name}")

        expected_id = self.prop_extract_key(extract_item)
        expected_name = self.prop_name_key(extract_item.name)
        for item in output.props:
            if self.prop_asset_id(item.name, item.status) == expected_id:
                return item
            if self.prop_name_key(item.name) == expected_name:
                return item

        if len(output.props) == 1:
            return output.props[0]
        raise ValueError(
            f"prop_design must return only {extract_item.name}; got "
            f"{', '.join(item.name for item in output.props)}"
        )

    def merge_prop_extract_into_design(self, item: PropDesignItem, extract_item: PropExtractItem) -> PropDesignItem:
        item.name = extract_item.name
        item.status = extract_item.status or item.status or "normal"
        item.episode_keys = self.dedupe_texts([*extract_item.episode_keys, *item.episode_keys])
        return item

    def ordered_prop_design_items(
        self,
        extract_items: list[PropExtractItem],
        designed_by_key: dict[str, PropDesignItem],
    ) -> list[PropDesignItem]:
        ordered: list[PropDesignItem] = []
        emitted: set[str] = set()
        for extract_item in extract_items:
            key = self.prop_extract_key(extract_item)
            item = designed_by_key.get(key)
            if item is None:
                continue
            ordered.append(item)
            emitted.add(key)
        for key, item in designed_by_key.items():
            if key not in emitted:
                ordered.append(item)
        return ordered

    @staticmethod
    def role_bound_props(state: ProjectState) -> dict[str, Prop]:
        return {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.source in {"role_design", "role_appearance_design"} or prop.owner_role_id
        }

    def apply_prop_design_item(
        self,
        project_dir: Path,
        state: ProjectState,
        item: PropDesignItem,
        *,
        existing_props: dict[str, Prop],
    ) -> Prop:
        item.episode_keys = self.prop_episode_keys(item.name, item.episode_keys, state, label="prop_design")
        prop_id = self.prop_asset_id(item.name, item.status)
        prop = Prop(
            id=prop_id,
            name=item.name,
            desc=item.desc,
            prompt=item.prompt,
            status=item.status,
            episode_keys=item.episode_keys,
            source="prop_design",
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
            node_name="prop_design",
            extra_payload={
                "source_prop_extract_path": "assets/json/nodes/prop_extract.json",
                "source_novel_full_paths": {
                    episode_key: state.script.novel_full.get(episode_key)
                    for episode_key in item.episode_keys
                },
            },
        )
        state.props[prop_id] = prop
        return prop

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
                portrait_prompt=item.portrait_prompt,
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

    @staticmethod
    def role_portrait_asset_id(appearance: RoleAppearance) -> str:
        return f"{appearance.id}_portrait"

    @staticmethod
    def role_portrait_prompt(role, appearance: RoleAppearance) -> str:
        prompt = str(appearance.portrait_prompt or "").strip()
        if prompt:
            return prompt

        desc = str(appearance.desc or "").strip()
        design_prompt = str(appearance.prompt or "").strip()
        return (
            "生成单人竖幅人物特写，1:2 构图，只展示头部、肩颈到上胸的近景或半身近景，"
            "保持后续角色设计图需要复用的稳定身份、年龄感、性别呈现、脸型、五官、眼神、发型、肤色、"
            "基础服装领口和材质气质。干净背景，无其他人物，无右侧道具设计图，无三视图拼版，"
            "无字幕、水印或文字标识，不要夸张畸变。"
            f"\n角色：{role.name}。"
            f"\n人物稳定外观：{desc or '-'}"
            f"\n原角色设计图提示词依据：{design_prompt}"
        )

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
        self.logger.info("node=role_appearance_generation total_images=%d", len(appearances) * 2)
        reuse_existing_assets = not bool(getattr(self.workflow, "_force_pregen", False))
        for role, appearance in appearances:
            if not appearance.prompt:
                raise ValueError(
                    f"Cannot generate role appearance for {role.name}/{appearance.name}: "
                    "missing prompt in role design JSON"
                )
            portrait_asset_id = self.role_portrait_asset_id(appearance)
            portrait_prompt = self.role_portrait_prompt(role, appearance)
            portrait_output_path = self.layout.image_asset_path(project_dir, "roles", portrait_asset_id)
            existing_portrait_path = self.layout.existing_project_file(project_dir, appearance.portrait_image_asset_path)
            if existing_portrait_path is None:
                existing_portrait_path = self.layout.existing_project_file(project_dir, portrait_output_path)
            if reuse_existing_assets and existing_portrait_path is not None:
                portrait_asset_path = existing_portrait_path
                portrait_provider = str(appearance.portrait_provider or getattr(provider, "name", "unknown"))
                portrait_model = str(appearance.portrait_model or getattr(provider, "model", ""))
                portrait_request_id = appearance.portrait_request_id
                portrait_usage = appearance.portrait_usage
                portrait_raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", portrait_asset_id, portrait_asset_path)
            else:
                self.logger.info(
                    "%s generating role portrait image for %s/%s",
                    portrait_asset_id,
                    role.name,
                    appearance.name,
                )
                portrait_result = await provider.generate_image(
                    portrait_prompt,
                    metadata={
                        "node_name": "role_portrait_generation",
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": portrait_asset_id,
                        "asset_type": "role_portrait",
                    },
                )
                portrait_asset_path = await self.media_store.write_first_generated_image(
                    project_dir,
                    portrait_output_path,
                    portrait_result,
                )
                portrait_provider = portrait_result.provider
                portrait_model = portrait_result.model
                portrait_request_id = portrait_result.request_id
                portrait_usage = portrait_result.usage
                portrait_raw_response = portrait_result.raw_response
                self.logger.info("%s generated successfully, saved in %s", portrait_asset_id, portrait_asset_path)

            appearance.portrait_prompt = portrait_prompt
            appearance.portrait_image_asset_id = portrait_asset_id
            appearance.portrait_image_asset_path = portrait_asset_path
            appearance.portrait_image_generation_status = "generated"
            appearance.portrait_provider = portrait_provider
            appearance.portrait_model = portrait_model
            appearance.portrait_request_id = portrait_request_id
            appearance.portrait_usage = portrait_usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=portrait_asset_id,
                    asset_type="role_portrait",
                    owner_id=role.id,
                    name=f"{role.name}/{appearance.name}/portrait",
                    prompt=portrait_prompt,
                    asset_path=portrait_asset_path,
                    provider=portrait_provider,
                    model=portrait_model,
                    request_id=portrait_request_id,
                    usage=portrait_usage,
                    raw_response=portrait_raw_response,
                )
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
                from autodrama.providers.base import AssetRef

                refs = None
                if getattr(provider, "supports_reference_images", False) and portrait_asset_path:
                    refs = [
                        AssetRef(
                            id=portrait_asset_id,
                            type="image",
                            path=str(project_dir / portrait_asset_path),
                            metadata={
                                "asset_type": "role_portrait",
                                "role_id": role.id,
                                "role_name": role.name,
                                "reference_for": appearance.id,
                            },
                        )
                    ]
                result = await provider.generate_image(
                    appearance.prompt,
                    refs=refs,
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

            if not self.workflow._role_needs_intro_video(role):
                appearance.intro_video_generation_status = "skipped"
                appearance.intro_video_asset_id = None
                appearance.intro_video_asset_path = None
                self.logger.info(
                    "%s skipped role intro video for functional role %s/%s",
                    appearance.id,
                    role.name,
                    appearance.name,
                )
                continue

            intro_video_asset_id = f"{appearance.id}_intro_video"
            base_intro_prompt = appearance.intro_video_prompt or (
                f"{role.name}站在洁净、亮度适中的虚空圆台上；"
                f"0-2 秒：圆台缓慢转动，人物保持{appearance.desc}的稳定外观，镜头以中景平稳观察；"
                "2-5 秒：人物做几个符合身份和性格的常见动作，如有随身物品，展示佩戴、握持或使用方式；"
                "5-8 秒：镜头轻微推近并停在人物稳定识别角度，背景保持干净抽象，无其他人物、无字幕、水印或文字标识。"
            )
            visual_style_prompt = str(state.metadata.get("visual_style_prompt") or "").strip()
            intro_prompt = "\n".join(
                part
                for part in (
                    "参考图片1中的人物外观和绑定物品设计，保持形象一致性。",
                    f"画面风格要求：{visual_style_prompt}" if visual_style_prompt else "",
                    base_intro_prompt,
                    "全片不要出现任何字幕、标志、logo、水印、文字标识、片段编号、可读文字或无关商标。",
                )
                if part
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
                self.logger.info(
                    "%s generating role intro video for %s/%s",
                    intro_video_asset_id,
                    role.name,
                    appearance.name,
                )
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


class PropExtractNode(StaticAssetNodeBase):
    name = "prop_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop")
        self.logger.info(
            "node=prop_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        output = await self.asset_service.prop_extract(
            state,
            provider,
            novel_full=self.novel_full_contents(project_dir, state, episode_keys),
        )

        expected = set(episode_keys)
        seen_keys: set[str] = set()
        for item in output.props:
            item.name = str(item.name or "").strip()
            if not item.name:
                raise ValueError("prop_extract returned a prop with empty name")
            item.status = self.prop_status_key(item.status)
            item.episode_keys = self.dedupe_texts(item.episode_keys)
            item.source_chapters = self.dedupe_texts(item.source_chapters)
            item.appearance_notes = self.dedupe_texts(item.appearance_notes)
            invalid_episode_keys = sorted(set(item.episode_keys).difference(expected))
            if invalid_episode_keys:
                raise ValueError(
                    f"prop_extract episode_keys for {item.name} must use existing keys; "
                    f"got {', '.join(invalid_episode_keys)}"
                )
            if not item.episode_keys:
                raise ValueError(f"prop_extract must include episode_keys for {item.name}")
            key = self.prop_extract_key(item)
            if key in seen_keys:
                raise ValueError(f"prop_extract returned duplicated prop/status: {item.name} ({item.status})")
            seen_keys.add(key)
            self.prop_designs.save_extract_item(project_dir, item)

        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
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
        extract_output = self.prop_designs.load_extract_output(project_dir)

        active_episode_keys = self.active_episode_keys(state)
        target_extract_items = self.target_prop_extract_items(extract_output.props, state, active_episode_keys)
        if active_episode_keys:
            self.logger.info(
                "prop_design episode-scoped rerun episodes=%s target_props=%s",
                ",".join(active_episode_keys),
                ",".join(item.name for item in target_extract_items) or "-",
            )
            if not target_extract_items:
                self.logger.warning(
                    "prop_design found no props appearing in selected episodes: %s",
                    ",".join(active_episode_keys),
                )

        existing_props = dict(state.props)
        role_bound_props = self.role_bound_props(state)
        for prop in role_bound_props.values():
            if not prop.design_path and prop.prompt:
                prop.design_path = self.save_prop_design_record(
                    project_dir,
                    prop,
                    prompt=prop.prompt,
                    node_name=prop.source or self.name,
                )
        state.props = dict(role_bound_props)

        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        designed_by_key: dict[str, PropDesignItem] = {}
        existing_output = self.prop_designs.load_existing_output(project_dir)
        should_keep_existing = existing_output is not None and (not force_pregen or bool(active_episode_keys))
        if existing_output is not None and should_keep_existing:
            for existing_item in existing_output.props:
                try:
                    existing_item.episode_keys = self.prop_episode_keys(
                        existing_item.name,
                        existing_item.episode_keys,
                        state,
                        label="prop_design",
                    )
                except ValueError:
                    continue
                key = self.prop_extract_key(existing_item)
                designed_by_key[key] = existing_item
                self.apply_prop_design_item(project_dir, state, existing_item, existing_props=existing_props)

        all_prop_extracts = [item.model_dump(mode="json") for item in extract_output.props]
        for extract_item in target_extract_items:
            key = self.prop_extract_key(extract_item)
            if key in designed_by_key and not force_pregen:
                self.logger.info("prop_design %s already exists, skipped", extract_item.name)
                continue

            episode_keys = self.prop_episode_keys(
                extract_item.name,
                extract_item.episode_keys,
                state,
                label="prop_extract",
            )
            prop_novel_full = self.novel_full_contents(project_dir, state, episode_keys)
            self.logger.info(
                "prop_design generating %s status=%s episodes=%s chapters=%s",
                extract_item.name,
                extract_item.status,
                ",".join(episode_keys),
                ",".join(extract_item.source_chapters) or "-",
            )
            output = await self.asset_service.prop_design(
                state,
                provider,
                prop_item=extract_item,
                prop_novel_full=prop_novel_full,
                all_prop_extracts=all_prop_extracts,
                existing_prop_designs=[
                    item.model_dump(mode="json")
                    for item in self.ordered_prop_design_items(extract_output.props, designed_by_key)
                ],
            )
            item = self.merge_prop_extract_into_design(
                self.select_prop_design_item(output, extract_item),
                extract_item,
            )
            item.episode_keys = self.prop_episode_keys(item.name, item.episode_keys, state, label="prop_design")
            self.apply_prop_design_item(project_dir, state, item, existing_props=existing_props)
            designed_by_key[key] = item
            state.budget.used_text_calls += 1
            self.repo.save_node_output(
                project_dir,
                self.name,
                PropDesignOutput(props=self.ordered_prop_design_items(extract_output.props, designed_by_key)),
            )
            self.repo.save_state(project_dir, state)

        final_items = self.ordered_prop_design_items(extract_output.props, designed_by_key)
        state.metadata["prop_design_generation_mode"] = "per_prop_recursive"
        state.metadata["prop_design_active_episode_keys"] = active_episode_keys
        state.metadata["prop_design_target_prop_names"] = [item.name for item in target_extract_items]
        state.metadata["prop_design_designed_prop_names"] = [item.name for item in final_items]
        self.repo.save_node_output(project_dir, self.name, PropDesignOutput(props=final_items))
        return state


class PropGenerationNode(StaticAssetNodeBase):
    name = "prop_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("prop")
        self.logger.info(
            "node=prop_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        active_episode_keys = self.active_episode_keys(state)
        all_props = self.ordered_props_for_generation(list(state.props.values()))
        props = [
            prop
            for prop in all_props
            if self.prop_matches_active_episode_keys(prop, active_episode_keys)
        ]
        normal_props_by_base = self.normal_props_by_variant_base(all_props)
        if active_episode_keys:
            self.logger.info(
                "node=prop_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(props),
            )
        self.logger.info("node=prop_generation total_images=%d", len(props))
        generated_by_asset_id: dict[str, StaticAssetGenerationItem] = {}
        if active_episode_keys:
            path = self.layout.node_output_path(project_dir, self.name)
            if path.exists():
                try:
                    existing_output = StaticAssetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
                    generated_by_asset_id = {
                        item.asset_id: item
                        for item in existing_output.generated_assets
                    }
                except Exception as exc:
                    self.logger.warning("prop_generation ignored invalid existing node output %s: %s", path, exc)
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
            generated_by_asset_id[prop.id] = generated[-1]
            self.logger.info("%s generated successfully, saved in %s", prop.id, asset_path)
        if active_episode_keys and generated_by_asset_id:
            ordered_generated = [
                generated_by_asset_id[prop.id]
                for prop in all_props
                if prop.id in generated_by_asset_id
            ]
        else:
            ordered_generated = generated
        self.repo.save_node_output(
            project_dir,
            self.name,
            StaticAssetGenerationOutput(generated_assets=ordered_generated),
        )
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
        PropExtractNode.name: PropExtractNode(**deps),
        PropDesignNode.name: PropDesignNode(**deps),
        PropGenerationNode.name: PropGenerationNode(**deps),
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
    "PropExtractNode",
    "PropGenerationNode",
    "RoleAppearanceDesignNode",
    "RoleAppearanceGenerationNode",
    "StaticAssetNodeBase",
    "build_static_asset_node_runners",
    "build_static_asset_nodes",
]
