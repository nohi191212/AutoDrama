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
    "roleboard_generation",
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
                return self.router.text(purpose, node_name="image_prompt_safety_rewrite")
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

class RoleAppearanceGenerationBase(StaticAssetNodeBase):
    ROLEBOARD_STYLE_PROMPT_HEADER = "统一角色身份板风格要求（优先级高于角色身份板 prompt 中的画面风格）"
    STYLE_REFERENCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

    def roleboard_style_reference_refs(self) -> list[AssetRef]:
        ref_dir = self.repo.settings.generation.roleboard_style_reference_dir
        if ref_dir is None:
            return []
        if not ref_dir.exists() or not ref_dir.is_dir():
            raise FileNotFoundError(f"roleboard_style_reference_dir does not exist or is not a directory: {ref_dir}")

        paths = [
            path
            for path in sorted(ref_dir.iterdir(), key=lambda item: item.name.lower())
            if path.is_file() and path.suffix.lower() in self.STYLE_REFERENCE_EXTENSIONS
        ]
        if not paths:
            raise ValueError(f"roleboard_style_reference_dir contains no supported image files: {ref_dir}")

        return [
            AssetRef(
                id=f"roleboard_style_ref_{index}",
                type="image",
                path=str(path),
                metadata={
                    "asset_type": "roleboard_style_reference",
                    "role": "style_reference",
                    "source_dir": str(ref_dir),
                },
            )
            for index, path in enumerate(paths, start=1)
        ]

    def roleboard_style_prompt(self) -> str:
        return str(self.repo.settings.generation.roleboard_style_prompt or "").strip()

    def roleboard_style_prefix(
        self,
        *,
        style_reference_count: int,
        identity_reference_index: int | None = None,
        output_kind: str,
    ) -> str:
        parts: list[str] = []
        style_prompt = self.roleboard_style_prompt()
        if style_prompt:
            parts.append(f"{self.ROLEBOARD_STYLE_PROMPT_HEADER}：{style_prompt}")
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

    def apply_roleboard_style_context(
        self,
        prompt: str,
        *,
        style_reference_count: int,
        identity_reference_index: int | None = None,
        output_kind: str,
    ) -> str:
        prefix = self.roleboard_style_prefix(
            style_reference_count=style_reference_count,
            identity_reference_index=identity_reference_index,
            output_kind=output_kind,
        )
        if not prefix:
            return prompt
        if prompt.lstrip().startswith(self.ROLEBOARD_STYLE_PROMPT_HEADER):
            return prompt
        return "\n\n".join([prefix, prompt])

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

    @staticmethod
    def roleboard_asset_id(appearance: RoleAppearance) -> str:
        return f"{appearance.id}_roleboard"

    def key_vision_reference_ref(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        reference_for: str,
    ) -> AssetRef:
        asset = state.metadata.get("key_vision_asset")
        if isinstance(asset, dict):
            asset_id = asset.get("asset_id") or state.metadata.get("key_vision_asset_id")
            asset_path = asset.get("asset_path") or state.metadata.get("key_vision_asset_path")
            asset_url = asset.get("asset_url") or state.metadata.get("key_vision_asset_url")
            name = asset.get("name") or state.metadata.get("key_vision_name") or "主视觉原图"
        else:
            asset_id = state.metadata.get("key_vision_asset_id")
            asset_path = state.metadata.get("key_vision_asset_path")
            asset_url = state.metadata.get("key_vision_asset_url")
            name = state.metadata.get("key_vision_name") or "主视觉原图"
        if not asset_path and not asset_url:
            raise FileNotFoundError(
                "roleboard_generation requires the key vision image; run design_key_vision_image first"
            )

        path: str | None = None
        if asset_path:
            existing = self.layout.existing_project_file(project_dir, str(asset_path))
            if existing is None:
                if not asset_url:
                    raise FileNotFoundError(
                        f"roleboard_generation key vision image is missing: {asset_path}"
                    )
            else:
                path = str(project_dir / existing)
        return AssetRef(
            id=str(asset_id or "key_vision_original"),
            type="image",
            path=path,
            url=str(asset_url) if asset_url else None,
            metadata={
                "asset_type": "key_vision",
                "name": str(name),
                "reference_for": reference_for,
            },
        )

    def roleboard_prompt_for_generation(
        self,
        *,
        role: Role,
        appearance: RoleAppearance,
        style_reference_count: int,
        key_vision_reference_index: int,
    ) -> str:
        base_prompt = str(appearance.roleboard_prompt or appearance.prompt or "").strip()
        if not base_prompt:
            raise ValueError(
                f"Cannot generate roleboard for {role.name}/{appearance.name}: missing roleboard_prompt"
            )
        style_prefix = self.roleboard_style_prefix(
            style_reference_count=style_reference_count,
            output_kind="角色身份板",
        )
        key_vision_note = (
            f"参考图片{key_vision_reference_index}是本剧主视觉原图，只作为世界观、真人剧质感、"
            "光影色彩、摄影审美和美术气质参考；不要照搬其中人物、服装、脸或构图。"
        )
        roleboard_requirement = (
            "生成一张角色身份板：同一角色必须包含正面全身、侧面全身、背面全身、头部近景、"
            "表情组、常用动作姿态、服装材质细节和可复用配饰/道具细节。所有视图必须统一年龄感、"
            "脸型、五官、发型、服装、身高比例、体型和材质，不得变脸、换衣服或年龄漂移。"
            "画面应是清晰可复用的设计板，无字幕、水印、logo、片段编号或可读文字。"
        )
        negative_prompt = str(appearance.roleboard_negative_prompt or "").strip()
        return "\n\n".join(
            part
            for part in (
                style_prefix,
                key_vision_note,
                roleboard_requirement,
                f"角色：{role.name}",
                base_prompt,
                f"负向约束：{negative_prompt}" if negative_prompt else "",
            )
            if part
        )

    def existing_roleboard_path(self, project_dir: Path, appearance: RoleAppearance) -> str | None:
        for value in (appearance.asset_path, appearance.design_image_asset_path):
            existing = self.layout.existing_project_file(project_dir, value)
            if existing is not None:
                return existing
        return self.layout.existing_project_file(
            project_dir,
            self.layout.image_asset_path(project_dir, "roles", self.roleboard_asset_id(appearance)),
        )

    async def generate_roleboard_assets(
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
        style_refs = self.roleboard_style_reference_refs()
        if style_refs:
            self.logger.info(
                "%s using %d roleboard style reference image(s)",
                node_name,
                len(style_refs),
            )
        if not getattr(provider, "supports_reference_images", False):
            raise ValueError(f"{node_name} requires an image provider that supports reference images")

        for role, appearance in appearances:
            asset_id = self.roleboard_asset_id(appearance)
            key_vision_ref = self.key_vision_reference_ref(project_dir, state, reference_for=asset_id)
            refs = [*style_refs, key_vision_ref]
            prompt = self.roleboard_prompt_for_generation(
                role=role,
                appearance=appearance,
                style_reference_count=len(style_refs),
                key_vision_reference_index=len(style_refs) + 1,
            )
            output_path = self.layout.image_asset_path(project_dir, "roles", asset_id)
            existing_path = self.existing_roleboard_path(project_dir, appearance)
            if reuse_existing_assets and existing_path is not None:
                asset_path = existing_path
                asset_url = appearance.asset_url or appearance.design_image_asset_url
                provider_name = str(appearance.provider or getattr(provider, "name", "unknown"))
                model = str(appearance.model or getattr(provider, "model", ""))
                request_id = appearance.request_id
                usage = appearance.usage
                raw_response = {"resumed_from_existing_file": True}
                self.logger.info("%s already exists, reused from %s", asset_id, asset_path)
            else:
                self.logger.info(
                    "%s generating roleboard image for %s/%s",
                    asset_id,
                    role.name,
                    appearance.name,
                )
                result, prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                    provider=provider,
                    state=state,
                    node_name=node_name,
                    asset_id=asset_id,
                    prompt=prompt,
                    refs=refs,
                    metadata={
                        "node_name": node_name,
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": asset_id,
                        "asset_type": "roleboard",
                        "style_reference_count": len(style_refs),
                        "key_vision_reference_index": len(style_refs) + 1,
                    },
                    context={
                        "asset_type": "roleboard",
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
                self.logger.info("%s generated successfully, saved in %s", asset_id, asset_path)

            appearance.prompt = appearance.roleboard_prompt or appearance.prompt
            appearance.asset_id = asset_id
            appearance.asset_path = asset_path
            appearance.asset_url = asset_url
            appearance.design_image_asset_id = asset_id
            appearance.design_image_asset_path = asset_path
            appearance.design_image_asset_url = asset_url
            appearance.design_image_generation_status = "generated"
            appearance.provider = provider_name
            appearance.model = model
            appearance.request_id = request_id
            appearance.usage = usage
            item = StaticAssetGenerationItem(
                asset_id=asset_id,
                asset_type="roleboard",
                owner_id=role.id,
                name=f"{role.name}/{appearance.name}/roleboard",
                prompt=prompt,
                asset_path=asset_path,
                asset_url=asset_url,
                provider=provider_name,
                model=model,
                request_id=request_id,
                usage=usage,
                raw_response=raw_response,
            )
            generated.append(item)
            generated_by_asset_id[asset_id] = item
        return generated


class RoleboardGenerationNode(RoleAppearanceGenerationBase):
    name = "roleboard_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role", node_name=self.name)
        self.logger.info(
            "node=roleboard_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        active_episode_keys = self.active_episode_keys(state)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=roleboard_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(appearances),
            )
        self.logger.info("node=roleboard_generation total_images=%d", len(appearances))
        generated_by_asset_id = self.load_existing_generation_items(project_dir, self.name, active_episode_keys)
        generated = await self.generate_roleboard_assets(
            provider=provider,
            project_dir=project_dir,
            state=state,
            appearances=appearances,
            node_name=self.name,
            generated_by_asset_id=generated_by_asset_id,
        )
        ordered_ids = [
            self.roleboard_asset_id(appearance)
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


class PropExtractNode(StaticAssetNodeBase):
    name = "prop_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop", node_name=self.name)
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
        provider = self.router.text("prop", node_name=self.name)
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
        owner_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        for prop in owner_role_props.values():
            if not prop.design_path and prop.prompt:
                prop.design_path = self.save_prop_design_record(
                    project_dir,
                    prop,
                    prompt=prop.prompt,
                    node_name=prop.source or self.name,
                )
        state.props = dict(owner_role_props)

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
        provider = self.router.image("prop", node_name=self.name)
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
        provider = self.router.text("layout", node_name=self.name)
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
        provider = self.router.text("layout", node_name=self.name)
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
        provider = self.router.text("layout", node_name=self.name)
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
        provider = self.router.image("layout", node_name=self.name)
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
        RoleboardGenerationNode.name: RoleboardGenerationNode(**deps),
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
    "RoleboardGenerationNode",
    "StaticAssetNodeBase",
    "build_static_asset_node_runners",
    "build_static_asset_nodes",
]
