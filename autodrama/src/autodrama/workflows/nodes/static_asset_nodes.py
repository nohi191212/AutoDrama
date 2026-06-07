from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.errors import ProviderBadResponseError
from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    Layout,
    LayoutDesignItem,
    LayoutExtractItem,
    LayoutExtractOutput,
    ProjectState,
    Prop,
    PropDesignItem,
    PropDesignOutput,
    PropExtractItem,
    Role,
    RoleAppearance,
    RoleIntroVideoPromptItem,
    RoleIntroVideoPromptOutput,
    SafeImagePromptRewriteOutput,
    StaticAssetGenerationItem,
    StaticAssetGenerationOutput,
)
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef, ImageGenerationResult
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.prop_design_repo import PropDesignRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.media_store import MediaStore
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

STATIC_ASSET_NODE_NAMES = [
    "role_full_body_generation",
    "role_multiview_generation",
    "role_intro_video_prompt",
    "role_intro_video_generation",
    "prop_extract",
    "prop_design",
    "prop_generation",
    "layout_extract",
    "layout_design",
    "layout_dedupe_review",
    "layout_image_generation",
]


class StaticAssetNodeBase:
    IMAGE_SAFETY_PROMPT_REWRITE_MAX_ATTEMPTS = 3

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

    @staticmethod
    def _is_image_safety_failure(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "image_unsafe",
                "appear to be unsafe",
                "generated images appear to be unsafe",
            )
        )

    @staticmethod
    def _image_prompt_safety_rewrite_purposes(node_name: str) -> list[str]:
        if node_name.startswith("role_"):
            preferred = ["role"]
        elif node_name.startswith("prop_"):
            preferred = ["prop"]
        elif node_name.startswith("layout_"):
            preferred = ["layout"]
        else:
            preferred = []
        fallback = ["storyboard", "role", "prop", "layout"]
        ordered: list[str] = []
        for purpose in [*preferred, *fallback]:
            if purpose not in ordered:
                ordered.append(purpose)
        return ordered

    def _safe_image_prompt_rewrite_provider(self, node_name: str):
        last_error: Exception | None = None
        for purpose in self._image_prompt_safety_rewrite_purposes(node_name):
            try:
                return self.router.text(purpose)
            except Exception as exc:
                last_error = exc
        raise ProviderBadResponseError(f"No text provider is available for image prompt safety rewrite: {last_error}")

    async def _rewrite_static_image_prompt_for_safety(
        self,
        *,
        state: ProjectState,
        node_name: str,
        asset_id: str,
        current_prompt: str,
        safety_error: Exception,
        rewrite_attempt: int,
        context: dict[str, Any] | None = None,
    ) -> SafeImagePromptRewriteOutput:
        provider = self._safe_image_prompt_rewrite_provider(node_name)
        rewrite_prompt = (
            "你是图像生成 prompt 安全改写器。请把下方图像生成 prompt 改写得更容易通过图像安全策略，"
            "但不要改变资产职责、角色身份、道具身份、场景设定、构图关系、参考图片编号或核心视觉连续性。\n"
            "只移除或弱化可能触发安全策略的视觉表达：血液、血迹、开放性伤口、尸体、内脏、断肢、"
            "写实暴力、恐怖 gore、裸露、性暗示、仇恨标识、危险违法细节、可读文字、logo、水印等。\n"
            "优先改写为低风险替代表达：尘土、泥污、旧污渍、暗色纹理、衣物破损、疲惫或紧张神情、"
            "非写实符号化痕迹、CG 动画电影质感、克制的冲突氛围。保持画面仍然可作为当前资产使用。\n"
            "输出 JSON，只包含改写后的 prompt 和简短 notes。\n\n"
            f"## 节点\n{node_name}\n\n"
            f"## 资产 ID\n{asset_id}\n\n"
            f"## 资产上下文\n{context or {}}\n\n"
            f"## 原始图像生成 prompt\n{current_prompt}\n\n"
            f"## 安全失败信息\n{str(safety_error)[:2000]}\n\n"
            f"## 改写轮次\n{rewrite_attempt}/{self.IMAGE_SAFETY_PROMPT_REWRITE_MAX_ATTEMPTS}"
        )
        output = await provider.generate_json(
            rewrite_prompt,
            SafeImagePromptRewriteOutput,
            temperature=0.2,
            metadata={
                "node_name": "image_prompt_safety_rewrite",
                "project_id": state.project_id,
                "asset_id": asset_id,
                "source_node_name": node_name,
                "rewrite_attempt": rewrite_attempt,
            },
        )
        state.budget.used_text_calls += 1
        output.prompt = str(output.prompt or "").strip()
        if not output.prompt:
            raise ProviderBadResponseError("Image prompt safety rewrite returned an empty prompt")
        return output

    async def _generate_image_with_safety_prompt_rewrites(
        self,
        *,
        provider,
        state: ProjectState,
        node_name: str,
        asset_id: str,
        prompt: str,
        refs: list[AssetRef] | None = None,
        metadata: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[ImageGenerationResult, str, list[dict[str, Any]]]:
        current_prompt = prompt
        rewrite_records: list[dict[str, Any]] = []
        rewrite_attempt = 0
        metadata = metadata or {}
        while True:
            try:
                result = await provider.generate_image(
                    current_prompt,
                    refs=refs,
                    metadata={
                        **metadata,
                        "safety_prompt_rewrite_attempt": rewrite_attempt,
                        "safety_prompt_rewritten": rewrite_attempt > 0,
                    },
                )
                if rewrite_records:
                    raw_response = dict(result.raw_response or {})
                    raw_response["safety_prompt_rewrite"] = {
                        "original_prompt": prompt,
                        "final_prompt": current_prompt,
                        "rewrite_attempts": rewrite_records,
                    }
                    result.raw_response = raw_response
                return result, current_prompt, rewrite_records
            except ProviderBadResponseError as exc:
                if not self._is_image_safety_failure(exc):
                    raise
                if rewrite_attempt >= self.IMAGE_SAFETY_PROMPT_REWRITE_MAX_ATTEMPTS:
                    raise ProviderBadResponseError(
                        "Image generation still failed safety checks after "
                        f"{rewrite_attempt} prompt rewrite attempt(s): {exc}"
                    ) from exc

                rewrite_attempt += 1
                self.logger.warning(
                    "%s safety failure for asset=%s; rewriting prompt attempt %d/%d",
                    node_name,
                    asset_id,
                    rewrite_attempt,
                    self.IMAGE_SAFETY_PROMPT_REWRITE_MAX_ATTEMPTS,
                    extra={"asset_id": asset_id, "node_name": node_name},
                )
                rewritten = await self._rewrite_static_image_prompt_for_safety(
                    state=state,
                    node_name=node_name,
                    asset_id=asset_id,
                    current_prompt=current_prompt,
                    safety_error=exc,
                    rewrite_attempt=rewrite_attempt,
                    context=context,
                )
                rewrite_records.append(
                    {
                        "attempt": rewrite_attempt,
                        "error": str(exc),
                        "prompt": rewritten.prompt,
                        "notes": rewritten.notes,
                    }
                )
                current_prompt = rewritten.prompt

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
    def first_image_url(result: Any) -> str | None:
        image_urls = getattr(result, "image_urls", None)
        return image_urls[0] if image_urls else None

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

    def layout_matches_active_episode_keys(self, layout: Layout, active_episode_keys: list[str]) -> bool:
        if not active_episode_keys:
            return True
        return self.intersects_active_episode_keys(self.dedupe_texts(layout.episode_keys), active_episode_keys)

    def role_matches_active_episode_keys(
        self,
        role: Role,
        active_episode_keys: list[str],
        *,
        label: str,
    ) -> bool:
        if not active_episode_keys:
            return True
        role_episode_keys = self.dedupe_texts(role.episode_keys)
        if not role_episode_keys:
            raise ValueError(f"{label} cannot scope role {role.name}: missing episode_keys")
        return self.intersects_active_episode_keys(role_episode_keys, active_episode_keys)

    def target_role_appearances(
        self,
        state: ProjectState,
        active_episode_keys: list[str],
        *,
        label: str,
    ) -> list[tuple[Role, RoleAppearance]]:
        return [
            (role, appearance)
            for role in state.roles.values()
            if self.role_matches_active_episode_keys(role, active_episode_keys, label=label)
            for appearance in role.appearances.values()
        ]

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

    @classmethod
    def layout_extract_key(cls, item: LayoutExtractItem | LayoutDesignItem) -> str:
        return normalize_id("layout", item.name)

    def load_layout_extract_output(self, project_dir: Path) -> LayoutExtractOutput:
        path = self.layout.node_output_path(project_dir, "layout_extract")
        if not path.exists():
            raise FileNotFoundError(
                "layout_extract output is missing; run pregen --only layout_extract before layout_design"
            )
        return LayoutExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def layout_episode_keys(
        self,
        name: str,
        episode_keys: list[str],
        state: ProjectState,
        *,
        label: str,
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

    def layouts_from_design_items(
        self,
        items: list[LayoutDesignItem],
        state: ProjectState,
        *,
        label: str,
    ) -> dict[str, Layout]:
        layouts: dict[str, Layout] = {}
        for item in items:
            item.name = str(item.name or "").strip()
            if not item.name:
                raise ValueError(f"{label} returned a layout with empty name")
            item.episode_keys = self.layout_episode_keys(item.name, item.episode_keys, state, label=label)
            layout_id = normalize_id("layout", item.name)
            if layout_id in layouts:
                raise ValueError(f"{label} returned duplicated layout: {item.name}")
            layouts[layout_id] = Layout(
                id=layout_id,
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
        return layouts

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
            prop.asset_url = existing_prop.asset_url
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
                url=normal_prop.asset_url,
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
                url=appearance.design_image_asset_url or appearance.asset_url,
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
                full_body_prompt=item.full_body_prompt,
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


class RoleAppearanceGenerationBase(StaticAssetNodeBase):
    ROLE_DESIGN_STYLE_PROMPT_HEADER = "统一人物设计风格要求（优先级高于角色设计 JSON 中的旧画面风格模板）"
    ROLE_INTRO_VIDEO_DURATION_SECONDS = 4
    STYLE_REFERENCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

    def role_design_style_reference_refs(self) -> list[AssetRef]:
        ref_dir = self.repo.settings.generation.role_design_style_reference_dir
        if ref_dir is None:
            return []
        if not ref_dir.exists() or not ref_dir.is_dir():
            raise FileNotFoundError(f"role_design_style_reference_dir does not exist or is not a directory: {ref_dir}")

        paths = [
            path
            for path in sorted(ref_dir.iterdir(), key=lambda item: item.name.lower())
            if path.is_file() and path.suffix.lower() in self.STYLE_REFERENCE_EXTENSIONS
        ]
        if not paths:
            raise ValueError(f"role_design_style_reference_dir contains no supported image files: {ref_dir}")

        return [
            AssetRef(
                id=f"role_design_style_ref_{index}",
                type="image",
                path=str(path),
                metadata={
                    "asset_type": "role_design_style_reference",
                    "role": "style_reference",
                    "source_dir": str(ref_dir),
                },
            )
            for index, path in enumerate(paths, start=1)
        ]

    def role_design_style_prompt(self) -> str:
        return str(self.repo.settings.generation.role_design_style_prompt or "").strip()

    def role_design_style_prefix(
        self,
        *,
        style_reference_count: int,
        identity_reference_index: int | None = None,
        output_kind: str,
    ) -> str:
        parts: list[str] = []
        style_prompt = self.role_design_style_prompt()
        if style_prompt:
            parts.append(f"{self.ROLE_DESIGN_STYLE_PROMPT_HEADER}：{style_prompt}")
        if style_reference_count > 0:
            if style_reference_count == 1:
                style_ref_text = "参考图片1"
            else:
                style_ref_text = f"参考图片1-{style_reference_count}"
            parts.append(
                f"{style_ref_text}只作为{output_kind}的整体美术风格、材质质感、光影、色彩和画面气质参考；"
                "不要照搬参考图中的人物身份、脸、服装或构图。"
            )
        if identity_reference_index is not None:
            parts.append(
                f"参考图片{identity_reference_index}是同一角色的全身身份参考，必须保持人物脸型、发型、服装、"
                "随身道具、体型比例和身份特征一致。"
            )
        if parts:
            parts.append("下方角色提示词中的人物身份、服装、道具和结构要求仍需保留；若画面风格冲突，以上方统一风格要求和参考图片风格为准。")
        return "\n".join(parts)

    def apply_role_design_style_context(
        self,
        prompt: str,
        *,
        style_reference_count: int,
        identity_reference_index: int | None = None,
        output_kind: str,
    ) -> str:
        prefix = self.role_design_style_prefix(
            style_reference_count=style_reference_count,
            identity_reference_index=identity_reference_index,
            output_kind=output_kind,
        )
        if not prefix:
            return prompt
        if prompt.lstrip().startswith(self.ROLE_DESIGN_STYLE_PROMPT_HEADER):
            return prompt
        return "\n\n".join([prefix, prompt])

    @staticmethod
    def role_full_body_asset_id(appearance: RoleAppearance) -> str:
        return f"{appearance.id}_full_body"

    @staticmethod
    def role_full_body_prompt(role: Role, appearance: RoleAppearance) -> str:
        prompt = str(appearance.full_body_prompt or "").strip()
        if prompt:
            hard_requirement = (
                "硬性要求：正面全身照，角色从头到脚完整入画；自然展示身份和气质，不要立正站立、"
                "不要证件照姿势、不要三视图、不要右侧道具设计图、不要其他人物、不要字幕水印或文字标识。"
            )
            if hard_requirement in prompt:
                return prompt
            return "\n".join([prompt, hard_requirement])

        desc = str(appearance.desc or "").strip()
        multiview_prompt = str(appearance.prompt or "").strip()
        return (
            "生成单人正面全身照，角色从头到脚完整入画，用自然站姿或轻微动作展示身份、年龄感、性别呈现、"
            "身高体型、头身比例、脸型、五官、眼神、发型、肤色、稳定服装、鞋履和可复用配饰。"
            "人物不要立正站立，不要证件照姿势，身体重心自然，手臂自然放松或做符合身份的小动作。"
            "干净背景，无其他人物，无三视图拼版，无右侧道具设计图，无字幕、水印或文字标识，不要夸张畸变。"
            f"\n角色：{role.name}。"
            f"\n人物稳定外观：{desc or '-'}"
            f"\n后续三视图与道具设计提示词依据：{multiview_prompt or '-'}"
        )

    @staticmethod
    def role_multiview_asset_id(appearance: RoleAppearance) -> str:
        return appearance.id

    @staticmethod
    def role_intro_video_asset_id(appearance: RoleAppearance) -> str:
        return f"{appearance.id}_intro_video"

    def load_existing_intro_video_prompt_items(
        self,
        project_dir: Path,
        active_episode_keys: list[str],
    ) -> dict[str, RoleIntroVideoPromptItem]:
        if not active_episode_keys:
            return {}
        path = self.layout.node_output_path(project_dir, RoleIntroVideoPromptNode.name)
        if not path.exists():
            return {}
        try:
            output = RoleIntroVideoPromptOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("%s ignored invalid existing node output %s: %s", RoleIntroVideoPromptNode.name, path, exc)
            return {}
        return {item.asset_id: item for item in output.prompts}

    @staticmethod
    def ordered_intro_video_prompt_items(
        prompt_by_asset_id: dict[str, RoleIntroVideoPromptItem],
        ordered_asset_ids: list[str],
    ) -> list[RoleIntroVideoPromptItem]:
        return [
            prompt_by_asset_id[asset_id]
            for asset_id in ordered_asset_ids
            if asset_id in prompt_by_asset_id
        ]

    def render_intro_video_prompt_item(
        self,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        appearance: RoleAppearance,
    ) -> RoleIntroVideoPromptItem | None:
        if not self.workflow._role_needs_intro_video(role):
            appearance.intro_video_generation_status = "skipped"
            appearance.intro_video_asset_id = None
            appearance.intro_video_asset_path = None
            self.logger.info(
                "%s skipped role intro video prompt for functional role %s/%s",
                appearance.id,
                role.name,
                appearance.name,
            )
            return None

        multiview_asset_path = self.layout.existing_project_file(project_dir, appearance.design_image_asset_path)
        if multiview_asset_path is None:
            raise ValueError(
                f"Cannot render role intro video prompt for {role.name}/{appearance.name}: "
                "missing multiview reference; run role_multiview_generation first"
            )

        intro_video_asset_id = self.role_intro_video_asset_id(appearance)
        base_intro_prompt = appearance.intro_video_prompt or (
            f"{role.name}站在洁净、亮度适中的虚空圆台上；"
            f"0-1.5 秒：圆台缓慢转动，人物保持{appearance.desc}的稳定外观，镜头以中景平稳观察；"
            "1.5-3 秒：人物做一个符合身份和性格的常见动作，如有随身物品，展示佩戴、握持或使用方式；"
            "3-4 秒：镜头轻微推近并停在人物稳定识别角度，背景保持干净抽象，无其他人物、无字幕、水印或文字标识。"
        )
        role_style_prompt = self.role_design_style_prompt()
        intro_prompt = "\n".join(
            part
            for part in (
                "参考图片1中的人物三视图、全身比例和绑定物品设计，保持人物形象一致性但不要求和图片像素级一致以防动作僵硬，人物动作和画面表现需要符合基本逻辑",
                f"人物设计风格要求：{role_style_prompt}" if role_style_prompt else "",
                base_intro_prompt,
                f"人物介绍视频时长硬性约束：全片总时长 {self.ROLE_INTRO_VIDEO_DURATION_SECONDS} 秒，时间段必须只覆盖 0-{self.ROLE_INTRO_VIDEO_DURATION_SECONDS} 秒；如果上方旧提示词出现超过 {self.ROLE_INTRO_VIDEO_DURATION_SECONDS} 秒的时间段，请压缩动作并在 4 秒内完成，结尾停在稳定识别角度。",
                "人物朝向约束：角色介绍视频中人物不要呈现证件照式、完全正对镜头的僵硬构图；脸部和身体保持轻微侧转，可使用约15-45度三分之二侧脸、侧身、低头抬眼或视线看向镜头旁侧。即使需要表现人物注意到观众方向，也避免双肩水平、脸部完全平贴镜头和长时间直盯镜头。",
                "全片不要出现任何字幕、标志、logo、水印、文字标识、片段编号、可读文字或无关商标。",
            )
            if part
        )
        return RoleIntroVideoPromptItem(
            asset_id=intro_video_asset_id,
            role_id=role.id,
            role_name=role.name,
            appearance_id=appearance.id,
            appearance_name=appearance.name,
            prompt=intro_prompt,
            reference_asset_id=appearance.design_image_asset_id or appearance.id,
            reference_asset_path=str(multiview_asset_path),
            reference_asset_url=appearance.design_image_asset_url or appearance.asset_url,
            episode_keys=self.dedupe_texts(role.episode_keys),
        )

    def load_existing_generation_items(
        self,
        project_dir: Path,
        node_name: str,
        active_episode_keys: list[str],
    ) -> dict[str, StaticAssetGenerationItem]:
        if not active_episode_keys:
            return {}
        path = self.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            return {}
        try:
            output = StaticAssetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("%s ignored invalid existing node output %s: %s", node_name, path, exc)
            return {}
        return {item.asset_id: item for item in output.generated_assets}

    @staticmethod
    def ordered_generation_items(
        generated: list[StaticAssetGenerationItem],
        generated_by_asset_id: dict[str, StaticAssetGenerationItem],
        ordered_asset_ids: list[str],
        active_episode_keys: list[str],
    ) -> list[StaticAssetGenerationItem]:
        if active_episode_keys and generated_by_asset_id:
            return [
                generated_by_asset_id[asset_id]
                for asset_id in ordered_asset_ids
                if asset_id in generated_by_asset_id
            ]
        return generated

    def existing_full_body_path(self, project_dir: Path, appearance: RoleAppearance) -> str | None:
        full_body_asset_id = self.role_full_body_asset_id(appearance)
        if appearance.full_body_image_asset_id == full_body_asset_id:
            existing = self.layout.existing_project_file(project_dir, appearance.full_body_image_asset_path)
            if existing is not None:
                return existing
        return self.layout.existing_project_file(
            project_dir,
            self.layout.image_asset_path(project_dir, "roles", full_body_asset_id),
        )

    async def generate_full_body_assets(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        appearances: list[tuple[Role, RoleAppearance]],
        node_name: str,
        generated_by_asset_id: dict[str, StaticAssetGenerationItem],
    ) -> list[StaticAssetGenerationItem]:
        generated: list[StaticAssetGenerationItem] = []
        reuse_existing_assets = not bool(getattr(self.workflow, "_force_pregen", False))
        style_refs = self.role_design_style_reference_refs()
        if style_refs:
            if not getattr(provider, "supports_reference_images", False):
                raise ValueError(f"{node_name} requires an image provider that supports reference images")
            self.logger.info(
                "%s using %d role design style reference image(s)",
                node_name,
                len(style_refs),
            )
        for role, appearance in appearances:
            full_body_asset_id = self.role_full_body_asset_id(appearance)
            full_body_prompt = self.apply_role_design_style_context(
                self.role_full_body_prompt(role, appearance),
                style_reference_count=len(style_refs),
                output_kind="角色全身图",
            )
            output_path = self.layout.image_asset_path(project_dir, "roles", full_body_asset_id)
            existing_path = self.existing_full_body_path(project_dir, appearance)
            if reuse_existing_assets and existing_path is not None:
                asset_path = existing_path
                asset_url = appearance.full_body_image_asset_url
                provider_name = str(appearance.full_body_provider or getattr(provider, "name", "unknown"))
                model = str(appearance.full_body_model or getattr(provider, "model", ""))
                request_id = appearance.full_body_request_id
                usage = appearance.full_body_usage
                raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", full_body_asset_id, asset_path)
            else:
                self.logger.info(
                    "%s generating role full body image for %s/%s",
                    full_body_asset_id,
                    role.name,
                    appearance.name,
                )
                result, full_body_prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                    provider=provider,
                    state=state,
                    node_name=node_name,
                    asset_id=full_body_asset_id,
                    prompt=full_body_prompt,
                    refs=style_refs,
                    metadata={
                        "node_name": node_name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": full_body_asset_id,
                        "asset_type": "role_full_body",
                        "style_reference_count": len(style_refs),
                    },
                    context={
                        "asset_type": "role_full_body",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                    },
                )
                asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)
                asset_url = self.first_image_url(result)
                provider_name = result.provider
                model = result.model
                request_id = result.request_id
                usage = result.usage
                raw_response = result.raw_response
                self.logger.info("%s generated successfully, saved in %s", full_body_asset_id, asset_path)

            appearance.full_body_prompt = full_body_prompt
            appearance.full_body_image_asset_id = full_body_asset_id
            appearance.full_body_image_asset_path = asset_path
            appearance.full_body_image_asset_url = asset_url
            appearance.full_body_image_generation_status = "generated"
            appearance.full_body_provider = provider_name
            appearance.full_body_model = model
            appearance.full_body_request_id = request_id
            appearance.full_body_usage = usage
            item = StaticAssetGenerationItem(
                asset_id=full_body_asset_id,
                asset_type="role_full_body",
                owner_id=role.id,
                name=f"{role.name}/{appearance.name}/full_body",
                prompt=full_body_prompt,
                asset_path=asset_path,
                asset_url=asset_url,
                provider=provider_name,
                model=model,
                request_id=request_id,
                usage=usage,
                raw_response=raw_response,
            )
            generated.append(item)
            generated_by_asset_id[full_body_asset_id] = item
        return generated

    async def generate_multiview_assets(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        appearances: list[tuple[Role, RoleAppearance]],
        node_name: str,
        generated_by_asset_id: dict[str, StaticAssetGenerationItem],
    ) -> list[StaticAssetGenerationItem]:
        generated: list[StaticAssetGenerationItem] = []
        reuse_existing_assets = not bool(getattr(self.workflow, "_force_pregen", False))
        style_refs = self.role_design_style_reference_refs()
        if style_refs:
            self.logger.info(
                "%s using %d role design style reference image(s)",
                node_name,
                len(style_refs),
            )
        for role, appearance in appearances:
            if not appearance.prompt:
                raise ValueError(
                    f"Cannot generate role multiview for {role.name}/{appearance.name}: "
                    "missing prompt in role design JSON"
                )
            multiview_prompt = self.apply_role_design_style_context(
                appearance.prompt,
                style_reference_count=len(style_refs),
                identity_reference_index=len(style_refs) + 1,
                output_kind="角色三视图设计图",
            )
            full_body_asset_id = self.role_full_body_asset_id(appearance)
            full_body_asset_path = self.layout.existing_project_file(project_dir, appearance.full_body_image_asset_path)
            if full_body_asset_path is None:
                full_body_asset_path = self.existing_full_body_path(project_dir, appearance)
            if full_body_asset_path is None:
                raise ValueError(
                    f"Cannot generate role multiview for {role.name}/{appearance.name}: "
                    "missing full body reference; run role_full_body_generation first"
                )

            multiview_asset_id = self.role_multiview_asset_id(appearance)
            output_path = self.layout.image_asset_path(project_dir, "roles", multiview_asset_id)
            existing_path = self.layout.existing_project_file(project_dir, appearance.design_image_asset_path)
            if existing_path is None:
                existing_path = self.layout.existing_project_file(project_dir, output_path)
            if reuse_existing_assets and existing_path is not None:
                asset_path = existing_path
                asset_url = appearance.design_image_asset_url or appearance.asset_url
                provider_name = str(appearance.provider or getattr(provider, "name", "unknown"))
                model = str(appearance.model or getattr(provider, "model", ""))
                request_id = appearance.request_id
                usage = appearance.usage
                raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", multiview_asset_id, asset_path)
            else:
                if not getattr(provider, "supports_reference_images", False):
                    raise ValueError(f"{node_name} requires an image provider that supports reference images")
                refs = [
                    *style_refs,
                    AssetRef(
                        id=full_body_asset_id,
                        type="image",
                        path=str(project_dir / full_body_asset_path),
                        url=appearance.full_body_image_asset_url,
                        metadata={
                            "asset_type": "role_full_body",
                            "role_id": role.id,
                            "role_name": role.name,
                            "reference_for": multiview_asset_id,
                        },
                    )
                ]
                self.logger.info(
                    "%s generating role multiview image for %s/%s",
                    multiview_asset_id,
                    role.name,
                    appearance.name,
                )
                result, multiview_prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                    provider=provider,
                    state=state,
                    node_name=node_name,
                    asset_id=multiview_asset_id,
                    prompt=multiview_prompt,
                    refs=refs,
                    metadata={
                        "node_name": node_name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": multiview_asset_id,
                        "asset_type": "role_multiview",
                        "style_reference_count": len(style_refs),
                        "identity_reference_index": len(style_refs) + 1,
                    },
                    context={
                        "asset_type": "role_multiview",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                    },
                )
                asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)
                asset_url = self.first_image_url(result)
                provider_name = result.provider
                model = result.model
                request_id = result.request_id
                usage = result.usage
                raw_response = result.raw_response
                self.logger.info("%s generated successfully, saved in %s", multiview_asset_id, asset_path)

            appearance.asset_id = multiview_asset_id
            appearance.asset_path = asset_path
            appearance.asset_url = asset_url
            appearance.design_image_asset_id = multiview_asset_id
            appearance.design_image_asset_path = asset_path
            appearance.design_image_asset_url = asset_url
            appearance.design_image_generation_status = "generated"
            appearance.provider = provider_name
            appearance.model = model
            appearance.request_id = request_id
            appearance.usage = usage
            for prop_id in appearance.role_bound_prop_ids:
                prop = state.props.get(prop_id)
                if prop is not None:
                    prop.asset_id = multiview_asset_id
                    prop.asset_path = asset_path
                    prop.asset_url = asset_url
                    prop.provider = provider_name
                    prop.model = model
                    prop.request_id = request_id
                    prop.usage = usage
            item = StaticAssetGenerationItem(
                asset_id=multiview_asset_id,
                asset_type="role_multiview",
                owner_id=role.id,
                name=f"{role.name}/{appearance.name}/multiview",
                prompt=multiview_prompt,
                asset_path=asset_path,
                asset_url=asset_url,
                provider=provider_name,
                model=model,
                request_id=request_id,
                usage=usage,
                raw_response=raw_response,
            )
            generated.append(item)
            generated_by_asset_id[multiview_asset_id] = item
        return generated

    async def generate_intro_video_assets(
        self,
        *,
        video_provider,
        project_dir: Path,
        state: ProjectState,
        appearances: list[tuple[Role, RoleAppearance]],
        node_name: str,
        generated_by_asset_id: dict[str, StaticAssetGenerationItem],
        prompt_by_asset_id: dict[str, RoleIntroVideoPromptItem],
    ) -> list[StaticAssetGenerationItem]:
        from autodrama.providers.base import AssetRef

        generated: list[StaticAssetGenerationItem] = []
        reuse_existing_assets = not bool(getattr(self.workflow, "_force_pregen", False))
        for role, appearance in appearances:
            if not self.workflow._role_needs_intro_video(role):
                appearance.intro_video_generation_status = "skipped"
                appearance.intro_video_asset_id = None
                appearance.intro_video_asset_path = None
                appearance.intro_video_asset_url = None
                self.logger.info(
                    "%s skipped role intro video for functional role %s/%s",
                    appearance.id,
                    role.name,
                    appearance.name,
                )
                continue

            intro_video_asset_id = self.role_intro_video_asset_id(appearance)
            prompt_item = prompt_by_asset_id.get(intro_video_asset_id)
            if prompt_item is None:
                raise ValueError(
                    f"Cannot generate role intro video for {role.name}/{appearance.name}: "
                    f"missing prompt; run {RoleIntroVideoPromptNode.name} first"
                )
            reference_asset_path = self.layout.existing_project_file(project_dir, prompt_item.reference_asset_path)
            if reference_asset_path is None:
                raise ValueError(
                    f"Cannot generate role intro video for {role.name}/{appearance.name}: "
                    f"missing prompt reference asset {prompt_item.reference_asset_path}; "
                    "run role_multiview_generation and role_intro_video_prompt first"
                )

            output_path = self.layout.video_asset_path(project_dir, "roles", intro_video_asset_id)
            existing_path = self.layout.existing_project_file(project_dir, appearance.intro_video_asset_path)
            if existing_path is None:
                existing_path = self.layout.existing_project_file(project_dir, output_path)
            if reuse_existing_assets and existing_path is not None:
                asset_path = existing_path
                asset_url = appearance.intro_video_asset_url
                provider_name = str(getattr(video_provider, "name", "unknown"))
                model = str(getattr(video_provider, "model", ""))
                request_id = None
                usage: dict[str, Any] = {}
                raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", intro_video_asset_id, asset_path)
            else:
                refs = [
                    AssetRef(
                        id=prompt_item.reference_asset_id,
                        type="image",
                        path=str(project_dir / reference_asset_path),
                        url=prompt_item.reference_asset_url,
                        metadata={
                            "asset_type": "role_multiview",
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
                result = await video_provider.generate_video(
                    prompt_item.prompt,
                    refs=refs,
                    duration=self.ROLE_INTRO_VIDEO_DURATION_SECONDS,
                    wait=True,
                    metadata={
                        "node_name": node_name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": intro_video_asset_id,
                        "asset_type": "role_intro_video",
                        "duration": self.ROLE_INTRO_VIDEO_DURATION_SECONDS,
                    },
                )
                asset_path = await self.media_store.write_generated_video(project_dir, output_path, result)
                asset_url = result.video_url
                provider_name = result.provider
                model = result.model
                request_id = result.request_id
                usage = result.usage
                raw_response = result.raw_response
                self.logger.info("%s generated successfully, saved in %s", intro_video_asset_id, asset_path)
            appearance.intro_video_asset_id = intro_video_asset_id
            appearance.intro_video_asset_path = asset_path
            appearance.intro_video_asset_url = asset_url
            appearance.intro_video_generation_status = "generated"
            item = StaticAssetGenerationItem(
                asset_id=intro_video_asset_id,
                asset_type="role_intro_video",
                owner_id=role.id,
                name=f"{role.name}/{appearance.name}/intro_video",
                prompt=prompt_item.prompt,
                asset_path=asset_path,
                asset_url=asset_url,
                provider=provider_name,
                model=model,
                request_id=request_id,
                usage=usage,
                raw_response=raw_response,
            )
            generated.append(item)
            generated_by_asset_id[intro_video_asset_id] = item
        return generated


class RoleFullBodyGenerationNode(RoleAppearanceGenerationBase):
    name = "role_full_body_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role")
        self.logger.info(
            "node=role_full_body_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        active_episode_keys = self.active_episode_keys(state)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=role_full_body_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(appearances),
            )
        self.logger.info("node=role_full_body_generation total_images=%d", len(appearances))
        generated_by_asset_id = self.load_existing_generation_items(project_dir, self.name, active_episode_keys)
        generated = await self.generate_full_body_assets(
            provider=provider,
            project_dir=project_dir,
            state=state,
            appearances=appearances,
            node_name=self.name,
            generated_by_asset_id=generated_by_asset_id,
        )
        ordered_ids = [
            self.role_full_body_asset_id(appearance)
            for _, appearance in self.target_role_appearances(state, [], label=self.name)
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StaticAssetGenerationOutput(
                generated_assets=self.ordered_generation_items(
                    generated,
                    generated_by_asset_id,
                    ordered_ids,
                    active_episode_keys,
                )
            ),
        )
        return state


class RoleMultiviewGenerationNode(RoleAppearanceGenerationBase):
    name = "role_multiview_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role")
        self.logger.info(
            "node=role_multiview_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        active_episode_keys = self.active_episode_keys(state)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=role_multiview_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(appearances),
            )
        self.logger.info("node=role_multiview_generation total_images=%d", len(appearances))
        generated_by_asset_id = self.load_existing_generation_items(project_dir, self.name, active_episode_keys)
        generated = await self.generate_multiview_assets(
            provider=provider,
            project_dir=project_dir,
            state=state,
            appearances=appearances,
            node_name=self.name,
            generated_by_asset_id=generated_by_asset_id,
        )
        ordered_ids = [
            self.role_multiview_asset_id(appearance)
            for _, appearance in self.target_role_appearances(state, [], label=self.name)
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StaticAssetGenerationOutput(
                generated_assets=self.ordered_generation_items(
                    generated,
                    generated_by_asset_id,
                    ordered_ids,
                    active_episode_keys,
                )
            ),
        )
        return state


class RoleIntroVideoPromptNode(RoleAppearanceGenerationBase):
    name = "role_intro_video_prompt"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        self.logger.info("node=role_intro_video_prompt rendering prompts")
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        active_episode_keys = self.active_episode_keys(state)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=role_intro_video_prompt episode-scoped rerun episodes=%s target_prompts=%d",
                ",".join(active_episode_keys),
                len(appearances),
            )
        self.logger.info("node=role_intro_video_prompt total_prompts=%d", len(appearances))
        prompt_by_asset_id = self.load_existing_intro_video_prompt_items(project_dir, active_episode_keys)
        for role, appearance in appearances:
            item = self.render_intro_video_prompt_item(project_dir, state, role, appearance)
            if item is not None:
                prompt_by_asset_id[item.asset_id] = item
        ordered_ids = [
            self.role_intro_video_asset_id(appearance)
            for role, appearance in self.target_role_appearances(state, [], label=self.name)
            if self.workflow._role_needs_intro_video(role)
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleIntroVideoPromptOutput(
                prompts=self.ordered_intro_video_prompt_items(prompt_by_asset_id, ordered_ids)
            ),
        )
        return state


class RoleIntroVideoGenerationNode(RoleAppearanceGenerationBase):
    name = "role_intro_video_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        try:
            video_provider = self.router.video("role")
        except Exception:
            video_provider = self.router.video("shot")
        self.logger.info(
            "node=role_intro_video_generation provider=%s model=%s",
            getattr(video_provider, "name", "unknown"),
            getattr(video_provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        active_episode_keys = self.active_episode_keys(state)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=role_intro_video_generation episode-scoped rerun episodes=%s target_videos=%d",
                ",".join(active_episode_keys),
                len(appearances),
            )
        self.logger.info("node=role_intro_video_generation total_videos=%d", len(appearances))
        prompt_path = self.layout.node_output_path(project_dir, RoleIntroVideoPromptNode.name)
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"role intro video prompts are missing: {prompt_path}; "
                f"run pregen --only {RoleIntroVideoPromptNode.name} first"
            )
        prompt_output = RoleIntroVideoPromptOutput.model_validate_json(prompt_path.read_text(encoding="utf-8"))
        prompt_by_asset_id = {item.asset_id: item for item in prompt_output.prompts}
        generated_by_asset_id = self.load_existing_generation_items(project_dir, self.name, active_episode_keys)
        generated = await self.generate_intro_video_assets(
            video_provider=video_provider,
            project_dir=project_dir,
            state=state,
            appearances=appearances,
            node_name=self.name,
            generated_by_asset_id=generated_by_asset_id,
            prompt_by_asset_id=prompt_by_asset_id,
        )
        ordered_ids = [
            self.role_intro_video_asset_id(appearance)
            for role, appearance in self.target_role_appearances(state, [], label=self.name)
            if self.workflow._role_needs_intro_video(role)
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StaticAssetGenerationOutput(
                generated_assets=self.ordered_generation_items(
                    generated,
                    generated_by_asset_id,
                    ordered_ids,
                    active_episode_keys,
                )
            ),
        )
        return state


class RoleAppearanceGenerationNode(RoleAppearanceGenerationBase):
    """Backward-compatible combined role appearance generation entry point."""

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
        active_episode_keys = self.active_episode_keys(state)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        generated_by_asset_id: dict[str, StaticAssetGenerationItem] = {}
        generated: list[StaticAssetGenerationItem] = []
        generated.extend(
            await self.generate_full_body_assets(
                provider=provider,
                project_dir=project_dir,
                state=state,
                appearances=appearances,
                node_name=RoleFullBodyGenerationNode.name,
                generated_by_asset_id=generated_by_asset_id,
            )
        )
        generated.extend(
            await self.generate_multiview_assets(
                provider=provider,
                project_dir=project_dir,
                state=state,
                appearances=appearances,
                node_name=RoleMultiviewGenerationNode.name,
                generated_by_asset_id=generated_by_asset_id,
            )
        )
        prompt_by_asset_id: dict[str, RoleIntroVideoPromptItem] = {}
        for role, appearance in appearances:
            item = self.render_intro_video_prompt_item(project_dir, state, role, appearance)
            if item is not None:
                prompt_by_asset_id[item.asset_id] = item
        self.repo.save_node_output(
            project_dir,
            RoleIntroVideoPromptNode.name,
            RoleIntroVideoPromptOutput(prompts=list(prompt_by_asset_id.values())),
        )
        generated.extend(
            await self.generate_intro_video_assets(
                video_provider=video_provider,
                project_dir=project_dir,
                state=state,
                appearances=appearances,
                node_name=RoleIntroVideoGenerationNode.name,
                generated_by_asset_id=generated_by_asset_id,
                prompt_by_asset_id=prompt_by_asset_id,
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
            result, prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                provider=provider,
                state=state,
                node_name=self.name,
                asset_id=prop.id,
                prompt=prompt,
                refs=refs,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "prop_id": prop.id,
                    "asset_id": prop.id,
                },
                context={
                    "asset_type": "prop",
                    "prop_id": prop.id,
                    "prop_name": prop.name,
                    "prop_status": prop.status,
                    "owner_role_id": prop.owner_role_id,
                    "owner_role_name": prop.owner_role_name,
                },
            )
            asset_path = await self.media_store.write_first_generated_image(
                project_dir,
                self.layout.image_asset_path(project_dir, "props", prop.id),
                result,
            )
            asset_url = self.first_image_url(result)
            prop.prompt = prompt
            prop.asset_id = prop.id
            prop.asset_path = asset_path
            prop.asset_url = asset_url
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
                    asset_url=asset_url,
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


class LayoutExtractNode(StaticAssetNodeBase):
    name = "layout_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        self.logger.info(
            "node=layout_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        output = await self.asset_service.layout_extract(
            state,
            provider,
            novel_full=self.novel_full_contents(project_dir, state, episode_keys),
        )

        expected = set(episode_keys)
        seen_keys: set[str] = set()
        for item in output.layouts:
            item.name = str(item.name or "").strip()
            if not item.name:
                raise ValueError("layout_extract returned a layout with empty name")
            item.episode_keys = self.dedupe_texts(item.episode_keys)
            item.source_chapters = self.dedupe_texts(item.source_chapters)
            item.appearance_notes = self.dedupe_texts(item.appearance_notes)
            invalid_episode_keys = sorted(set(item.episode_keys).difference(expected))
            if invalid_episode_keys:
                raise ValueError(
                    f"layout_extract episode_keys for {item.name} must use existing keys; "
                    f"got {', '.join(invalid_episode_keys)}"
                )
            if not item.episode_keys:
                raise ValueError(f"layout_extract must include episode_keys for {item.name}")
            key = self.layout_extract_key(item)
            if key in seen_keys:
                raise ValueError(f"layout_extract returned duplicated layout: {item.name}")
            seen_keys.add(key)

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
        extract_output = self.load_layout_extract_output(project_dir)
        output = await self.asset_service.layout_design(
            state,
            provider,
            layout_extracts=[item.model_dump(mode="json") for item in extract_output.layouts],
            episode_stories=self.episode_stories(project_dir, state),
        )
        state.layouts = self.layouts_from_design_items(output.layouts, state, label="layout_design")
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
        state.layouts = self.layouts_from_design_items(output.layouts, state, label="layout_dedupe_review")
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
        active_episode_keys = self.active_episode_keys(state)
        all_layouts = list(state.layouts.values())
        layouts = [
            layout
            for layout in all_layouts
            if self.layout_matches_active_episode_keys(layout, active_episode_keys)
        ]
        if active_episode_keys:
            self.logger.info(
                "node=layout_image_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(layouts),
            )
        self.logger.info("node=layout_image_generation total_images=%d", len(layouts))
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
                    self.logger.warning("layout_image_generation ignored invalid existing node output %s: %s", path, exc)
        for layout in layouts:
            prompt = layout.prompt
            result, prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                provider=provider,
                state=state,
                node_name=self.name,
                asset_id=layout.id,
                prompt=prompt,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "layout_id": layout.id,
                    "asset_id": layout.id,
                },
                context={
                    "asset_type": "layout",
                    "layout_id": layout.id,
                    "layout_name": layout.name,
                },
            )
            asset_path = await self.media_store.write_first_generated_image(
                project_dir,
                self.layout.image_asset_path(project_dir, "layouts", layout.id),
                result,
            )
            asset_url = self.first_image_url(result)
            layout.prompt = prompt
            layout.asset_id = layout.id
            layout.asset_path = asset_path
            layout.asset_url = asset_url
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
                    asset_url=asset_url,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
            self.logger.info("%s generated successfully, saved in %s", layout.id, asset_path)
            generated_by_asset_id[layout.id] = generated[-1]
        if active_episode_keys and generated_by_asset_id:
            ordered_generated = [
                generated_by_asset_id[layout.id]
                for layout in all_layouts
                if layout.id in generated_by_asset_id
            ]
        else:
            ordered_generated = generated
        self.repo.save_node_output(
            project_dir,
            self.name,
            StaticAssetGenerationOutput(generated_assets=ordered_generated),
        )
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
        RoleFullBodyGenerationNode.name: RoleFullBodyGenerationNode(**deps),
        RoleMultiviewGenerationNode.name: RoleMultiviewGenerationNode(**deps),
        RoleIntroVideoPromptNode.name: RoleIntroVideoPromptNode(**deps),
        RoleIntroVideoGenerationNode.name: RoleIntroVideoGenerationNode(**deps),
        RoleAppearanceGenerationNode.name: RoleAppearanceGenerationNode(**deps),
        PropExtractNode.name: PropExtractNode(**deps),
        PropDesignNode.name: PropDesignNode(**deps),
        PropGenerationNode.name: PropGenerationNode(**deps),
        LayoutExtractNode.name: LayoutExtractNode(**deps),
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
    "LayoutExtractNode",
    "LayoutImageGenerationNode",
    "PropDesignNode",
    "PropExtractNode",
    "PropGenerationNode",
    "RoleAppearanceDesignNode",
    "RoleAppearanceGenerationNode",
    "RoleFullBodyGenerationNode",
    "RoleIntroVideoGenerationNode",
    "RoleIntroVideoPromptNode",
    "RoleMultiviewGenerationNode",
    "StaticAssetNodeBase",
    "build_static_asset_node_runners",
    "build_static_asset_nodes",
]
