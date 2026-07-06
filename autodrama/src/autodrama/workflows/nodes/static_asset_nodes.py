from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from autodrama.core.errors import ProviderBadResponseError
from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    Layout,
    LayoutDedupeReviewOutput,
    LayoutExtractItem,
    LayoutExtractOutput,
    LayoutPromptOutput,
    ProjectState,
    Prop,
    PropDedupeOutput,
    PropDesignItem,
    PropDesignOutput,
    PropExtractItem,
    PropExtractOutput,
    PropPromptOutput,
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
    "prop_dedupe",
    "layout_extract",
    "layout_dedupe_review",
    "prop_prompt",
    "prop_image_generation",
    "layout_prompt",
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
        return self.script_service.state_episode_keys(state)

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
                "content policy",
                "safety policy",
                "policy violation",
                "内容政策",
                "安全策略",
                "违反了我们的内容政策",
                "可能违反",
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
    def layout_extract_key(cls, item: LayoutExtractItem) -> str:
        return normalize_id("layout", item.name)

    def load_layout_extract_output(self, project_dir: Path) -> LayoutExtractOutput:
        path = self.layout.node_output_path(project_dir, "layout_extract")
        if not path.exists():
            raise FileNotFoundError(
                "layout_extract output is missing; run pregen --only layout_extract before layout_dedupe_review"
            )
        return LayoutExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_layout_dedupe_output(self, project_dir: Path) -> LayoutDedupeReviewOutput:
        path = self.layout.node_output_path(project_dir, "layout_dedupe_review")
        if not path.exists():
            raise FileNotFoundError(
                "layout_dedupe_review output is missing; run pregen --only layout_dedupe_review before layout_prompt"
            )
        return LayoutDedupeReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def normalize_generated_layout_intro(value: dict[str, object] | None) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_name, raw_intro in (value or {}).items():
            name = str(raw_name or "").strip()
            intro = str(raw_intro or "").strip()
            if not name or not intro:
                continue
            result[name] = intro
        return result

    @classmethod
    def canonical_layout_intro(cls, value: dict[str, object] | None) -> str:
        import json

        normalized = cls.normalize_generated_layout_intro(value)
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def current_generated_layout_intro(self, state: ProjectState) -> dict[str, str]:
        return {
            layout.name: layout.desc
            for layout in state.layouts.values()
            if str(layout.name or "").strip() and str(layout.desc or "").strip()
        }

    def existing_generated_layout_intro(self, project_dir: Path, state: ProjectState) -> dict[str, str]:
        for node_name in ("layout_dedupe_review", "layout_extract"):
            path = self.layout.node_output_path(project_dir, node_name)
            if not path.exists():
                continue
            try:
                if node_name == "layout_dedupe_review":
                    output = LayoutDedupeReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))
                else:
                    output = LayoutExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.warning("%s ignored invalid existing %s output %s: %s", self.__class__.__name__, node_name, path, exc)
                continue
            intro = self.normalize_generated_layout_intro(output.generated_layout_intro)
            if intro:
                return intro
        return self.current_generated_layout_intro(state)

    def layouts_from_intro_and_prompts(
        self,
        generated_layout_intro: dict[str, str],
        layout_prompts: dict[str, str] | None,
        state: ProjectState,
        *,
        existing_layouts: dict[str, Layout] | None = None,
    ) -> dict[str, Layout]:
        expected_episode_keys = self.expected_episode_keys(state)
        existing_layouts = existing_layouts or {}
        layouts: dict[str, Layout] = {}
        prompts = self.normalize_generated_layout_intro(layout_prompts or {})
        for name, intro in self.normalize_generated_layout_intro(generated_layout_intro).items():
            layout_id = normalize_id("layout", name)
            if layout_id in layouts:
                raise ValueError(f"generated_layout_intro returned duplicated layout id for {name}")
            existing = existing_layouts.get(layout_id)
            prompt = prompts.get(name) or (existing.prompt if existing else "")
            layouts[layout_id] = Layout(
                id=layout_id,
                name=name,
                desc=intro,
                prompt=prompt,
                episode_keys=list(expected_episode_keys),
                asset_id=existing.asset_id if existing else None,
                asset_path=existing.asset_path if existing else None,
                asset_url=existing.asset_url if existing else None,
                provider=existing.provider if existing else None,
                model=existing.model if existing else None,
                request_id=existing.request_id if existing else None,
                usage=dict(existing.usage) if existing else {},
            )
        return layouts

    def layout_prompt_output_to_state(
        self,
        generated_layout_intro: dict[str, str],
        output: LayoutPromptOutput,
        state: ProjectState,
    ) -> dict[str, Layout]:
        missing_prompts = [
            name
            for name in self.normalize_generated_layout_intro(generated_layout_intro)
            if not str(output.layout_prompts.get(name) or "").strip()
        ]
        if missing_prompts:
            raise ValueError(f"layout_prompt missing prompt(s): {', '.join(missing_prompts)}")
        return self.layouts_from_intro_and_prompts(
            generated_layout_intro,
            output.layout_prompts,
            state,
            existing_layouts=state.layouts,
        )

    @staticmethod
    def normalize_generated_prop_intro(value: dict[str, object] | None) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_name, raw_intro in (value or {}).items():
            name = str(raw_name or "").strip()
            intro = str(raw_intro or "").strip()
            if not name or not intro:
                continue
            result[name] = intro
        return result

    @classmethod
    def canonical_prop_intro(cls, value: dict[str, object] | None) -> str:
        import json

        normalized = cls.normalize_generated_prop_intro(value)
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def current_generated_prop_intro(self, state: ProjectState) -> dict[str, str]:
        return {
            prop.name: prop.desc
            for prop in state.props.values()
            if not prop.owner_role_id and str(prop.name or "").strip() and str(prop.desc or "").strip()
        }

    def existing_generated_prop_intro(self, project_dir: Path, state: ProjectState) -> dict[str, str]:
        for node_name in ("prop_dedupe", "prop_extract"):
            path = self.layout.node_output_path(project_dir, node_name)
            if not path.exists():
                continue
            try:
                if node_name == "prop_dedupe":
                    output = PropDedupeOutput.model_validate_json(path.read_text(encoding="utf-8"))
                else:
                    output = PropExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.warning("%s ignored invalid existing %s output %s: %s", self.__class__.__name__, node_name, path, exc)
                continue
            intro = self.normalize_generated_prop_intro(output.generated_prop_intro)
            if intro:
                return intro
        return self.current_generated_prop_intro(state)

    def load_prop_extract_output(self, project_dir: Path) -> PropExtractOutput:
        path = self.layout.node_output_path(project_dir, "prop_extract")
        if not path.exists():
            raise FileNotFoundError(
                "prop_extract output is missing; run pregen --only prop_extract before prop_dedupe"
            )
        return PropExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_prop_dedupe_output(self, project_dir: Path) -> PropDedupeOutput:
        path = self.layout.node_output_path(project_dir, "prop_dedupe")
        if not path.exists():
            raise FileNotFoundError(
                "prop_dedupe output is missing; run pregen --only prop_dedupe before prop_prompt"
            )
        return PropDedupeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _state_base_name(name: str) -> str | None:
        value = str(name or "").strip()
        if "_" not in value:
            return None
        base, _state_name = value.rsplit("_", 1)
        base = base.strip()
        return base or None

    @classmethod
    def prop_intro_status(cls, name: str, all_names: set[str]) -> str:
        base_name = cls._state_base_name(name)
        if not base_name or base_name not in all_names:
            return "normal"
        _base, state_name = str(name).rsplit("_", 1)
        return cls.prop_status_key(state_name)

    def props_from_intro_and_prompts(
        self,
        project_dir: Path,
        generated_prop_intro: dict[str, str],
        prop_prompts: dict[str, str] | None,
        state: ProjectState,
        *,
        existing_props: dict[str, Prop] | None = None,
        save_records: bool = False,
    ) -> dict[str, Prop]:
        expected_episode_keys = self.expected_episode_keys(state)
        existing_props = existing_props or {}
        prompts = self.normalize_generated_prop_intro(prop_prompts or {})
        intro = self.normalize_generated_prop_intro(generated_prop_intro)
        all_names = set(intro)
        props: dict[str, Prop] = {}
        for name, desc in intro.items():
            status = self.prop_intro_status(name, all_names)
            prop_id = self.prop_asset_id(name, status)
            if prop_id in props:
                raise ValueError(f"generated_prop_intro returned duplicated prop id for {name}")
            existing = existing_props.get(prop_id)
            prompt = prompts.get(name) or (existing.prompt if existing else None)
            prop = Prop(
                id=prop_id,
                name=name,
                desc=desc,
                prompt=prompt,
                status=status,
                episode_keys=list(expected_episode_keys),
                source="prop_prompt" if prompt else "prop_dedupe",
                design_path=existing.design_path if existing else None,
                asset_id=existing.asset_id if existing else None,
                asset_path=existing.asset_path if existing else None,
                asset_url=existing.asset_url if existing else None,
                provider=existing.provider if existing else None,
                model=existing.model if existing else None,
                request_id=existing.request_id if existing else None,
                usage=dict(existing.usage) if existing else {},
            )
            if save_records and prompt:
                prop.source = "prop_prompt"
                prop.design_path = self.save_prop_design_record(
                    project_dir,
                    prop,
                    prompt=prompt,
                    node_name="prop_prompt",
                    extra_payload={
                        "source_prop_dedupe_path": "assets/json/nodes/prop_dedupe.json",
                    },
                )
            props[prop_id] = prop
        return props

    def prop_prompt_output_to_state(
        self,
        project_dir: Path,
        generated_prop_intro: dict[str, str],
        output: PropPromptOutput,
        state: ProjectState,
    ) -> dict[str, Prop]:
        missing_prompts = [
            name
            for name in self.normalize_generated_prop_intro(generated_prop_intro)
            if not str(output.prop_prompts.get(name) or "").strip()
        ]
        if missing_prompts:
            raise ValueError(f"prop_prompt missing prompt(s): {', '.join(missing_prompts)}")
        return self.props_from_intro_and_prompts(
            project_dir,
            generated_prop_intro,
            output.prop_prompts,
            state,
            existing_props=state.props,
            save_records=True,
        )

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
    DEFAULT_ROLEBOARD_GENERATION_CONCURRENCY = 1
    MAX_ROLEBOARD_GENERATION_CONCURRENCY = 5

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
            f"画面应是清晰可复用的设计板，必须在左上角或底部边缘以小号清晰文字标注"
            f"“角色：{role.name} | {appearance.name}”。"
            "各视图区边缘可添加小号功能性标签：正面、侧面、背面、头部、表情、动作、"
            "服装细节、配饰细节。所有文字必须远离人物脸部、身体轮廓、服装和道具，"
            "不得遮挡任何可复用视觉细节。除指定角色名和视图标签外，不得出现字幕、水印、"
            "logo、片段编号、项目编号、文件名、ID、剧情台词、乱码文字或错误角色名。"
        )
        negative_prompt = str(appearance.roleboard_negative_prompt or "").strip()
        label_negative_prompt = (
            "除指定角色名和视图标签外的可读文字，字幕，水印，logo，片段编号，项目编号，"
            "文件名，ID，剧情台词，乱码文字，错误角色名，文字遮挡人物脸部或服装细节"
        )
        combined_negative_prompt = "；".join(
            part for part in (negative_prompt, label_negative_prompt) if part
        )
        return "\n\n".join(
            part
            for part in (
                style_prefix,
                key_vision_note,
                f"角色：{role.name}",
                base_prompt,
                roleboard_requirement,
                f"负向约束：{combined_negative_prompt}",
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

    @classmethod
    def roleboard_generation_concurrency(cls, provider: object) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in (
                "roleboard_generation_concurrency",
                "roleboard_image_generation_concurrency",
                "image_generation_concurrency",
                "max_concurrent_images",
                "concurrency",
            ):
                if name in source:
                    value = source[name]
                    break
            if value is not None:
                break
        if value is None:
            for name in (
                "roleboard_generation_concurrency",
                "roleboard_image_generation_concurrency",
                "image_generation_concurrency",
                "max_concurrent_images",
                "concurrency",
            ):
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = cls.DEFAULT_ROLEBOARD_GENERATION_CONCURRENCY
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = cls.DEFAULT_ROLEBOARD_GENERATION_CONCURRENCY
        return max(1, min(cls.MAX_ROLEBOARD_GENERATION_CONCURRENCY, resolved))

    async def generate_roleboard_asset(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        appearance: RoleAppearance,
        node_name: str,
        style_refs: list[AssetRef],
        reuse_existing_assets: bool,
    ) -> StaticAssetGenerationItem:
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
        return StaticAssetGenerationItem(
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

        concurrency = self.roleboard_generation_concurrency(provider)
        self.logger.info(
            "%s total_images=%d concurrency=%d",
            node_name,
            len(appearances),
            concurrency,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(role: Role, appearance: RoleAppearance) -> StaticAssetGenerationItem:
            async with semaphore:
                return await self.generate_roleboard_asset(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    role=role,
                    appearance=appearance,
                    node_name=node_name,
                    style_refs=style_refs,
                    reuse_existing_assets=reuse_existing_assets,
                )

        tasks = [asyncio.create_task(generate_one(role, appearance)) for role, appearance in appearances]
        try:
            generated = list(await asyncio.gather(*tasks)) if tasks else []
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for item in generated:
            generated_by_asset_id[item.asset_id] = item
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
        generated_prop_intro = self.existing_generated_prop_intro(project_dir, state)
        output = await self.asset_service.prop_extract(
            state,
            provider,
            novel_full_all_episodes=self.novel_full_contents(project_dir, state, episode_keys),
            generated_prop_intro=generated_prop_intro,
        )
        output.generated_prop_intro = self.normalize_generated_prop_intro(output.generated_prop_intro)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class PropDedupeNode(StaticAssetNodeBase):
    name = "prop_dedupe"
    MAX_CONVERGENCE_ITERATIONS = 8

    @staticmethod
    def _max_iterations(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        try:
            return max(2, int(params.get("max_iterations") or PropDedupeNode.MAX_CONVERGENCE_ITERATIONS))
        except (TypeError, ValueError):
            return PropDedupeNode.MAX_CONVERGENCE_ITERATIONS

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop", node_name=self.name)
        extract_provider = self.router.text("prop", node_name=PropExtractNode.name)
        self.logger.info(
            "node=prop_dedupe provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        extract_output = self.load_prop_extract_output(project_dir)
        current_intro = self.normalize_generated_prop_intro(extract_output.generated_prop_intro)
        previous_review_intro: dict[str, str] | None = None
        final_output: PropDedupeOutput | None = None
        novel_full_all_episodes = self.novel_full_contents(project_dir, state, self.expected_episode_keys(state))
        max_iterations = self._max_iterations(provider)

        for iteration in range(1, max_iterations + 1):
            output = await self.asset_service.prop_dedupe(
                state,
                provider,
                generated_prop_intro=current_intro,
            )
            output.generated_prop_intro = self.normalize_generated_prop_intro(output.generated_prop_intro)
            state.budget.used_text_calls += 1
            final_output = output
            self.logger.info(
                "node=prop_dedupe iteration=%d props=%d",
                iteration,
                len(output.generated_prop_intro),
            )

            if (
                previous_review_intro is not None
                and self.canonical_prop_intro(previous_review_intro)
                == self.canonical_prop_intro(output.generated_prop_intro)
            ):
                output.merge_notes.append(f"prop_extract/prop_dedupe converged after {iteration} dedupe pass(es).")
                break

            previous_review_intro = dict(output.generated_prop_intro)
            if iteration >= max_iterations:
                output.merge_notes.append(
                    f"prop_extract/prop_dedupe reached max_iterations={max_iterations}; using latest dedupe output."
                )
                self.logger.warning(
                    "node=prop_dedupe reached max_iterations=%d without exact convergence",
                    max_iterations,
                )
                break

            extract_output = await self.asset_service.prop_extract(
                state,
                extract_provider,
                novel_full_all_episodes=novel_full_all_episodes,
                generated_prop_intro=output.generated_prop_intro,
            )
            extract_output.generated_prop_intro = self.normalize_generated_prop_intro(extract_output.generated_prop_intro)
            state.budget.used_text_calls += 1
            self.repo.save_node_output(project_dir, PropExtractNode.name, extract_output)
            current_intro = extract_output.generated_prop_intro

        if final_output is None:
            raise ValueError("prop_dedupe produced no output")
        owner_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        state.props = {
            **owner_role_props,
            **self.props_from_intro_and_prompts(
                project_dir,
                final_output.generated_prop_intro,
                {},
                state,
                existing_props=state.props,
            ),
        }
        self.repo.save_node_output(project_dir, self.name, final_output)
        return state


class PropPromptNode(StaticAssetNodeBase):
    name = "prop_prompt"

    def _text_provider(self):
        node_name = self.name
        if node_name not in self.repo.settings.nodes and "prop_design" in self.repo.settings.nodes:
            node_name = "prop_design"
        return self.router.text("prop", node_name=node_name)

    def _prop_prompt_template_name(self) -> str:
        node_settings = self.repo.settings.nodes.get(self.name) or self.repo.settings.nodes.get("prop_design")
        params = getattr(node_settings, "params", {}) if node_settings is not None else {}
        explicit = str(params.get("prompt_template") or "").strip()
        if explicit:
            return explicit

        try:
            image_provider = self.router.image("prop", node_name=PropImageGenerationNode.name)
            provider_key = slugify(str(getattr(image_provider, "name", "") or "")).lower()
            model_key = slugify(str(getattr(image_provider, "model", "") or "")).lower()
        except Exception:
            provider_key = ""
            model_key = ""

        candidates = [
            f"prop_prompt_{provider_key}_{model_key}",
            f"prop_prompt_{model_key}",
            f"prop_prompt_{provider_key}",
            "prop_prompt",
        ]
        prompt_dir = self.asset_service.prompts.prompt_dir
        for candidate in candidates:
            if candidate and (prompt_dir / f"{candidate}.md").exists():
                return candidate
        return "prop_prompt"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._text_provider()
        prompt_template = self._prop_prompt_template_name()
        self.logger.info(
            "node=prop_prompt provider=%s model=%s prompt_template=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            prompt_template,
        )
        dedupe_output = self.load_prop_dedupe_output(project_dir)
        generated_prop_intro = self.normalize_generated_prop_intro(dedupe_output.generated_prop_intro)
        if not generated_prop_intro:
            raise ValueError("prop_prompt requires non-empty generated_prop_intro from prop_dedupe")
        output = await self.asset_service.prop_prompt(
            state,
            provider,
            generated_prop_intro=generated_prop_intro,
            prompt_template=prompt_template,
        )
        output.prop_prompts = self.normalize_generated_prop_intro(output.prop_prompts)
        owner_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        state.props = {
            **owner_role_props,
            **self.prop_prompt_output_to_state(project_dir, generated_prop_intro, output, state),
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class PropDesignNode(PropPromptNode):
    name = "prop_design"


class PropImageGenerationNode(StaticAssetNodeBase):
    name = "prop_image_generation"

    @classmethod
    def _is_state_prop(cls, prop: Prop, props_by_name: dict[str, Prop]) -> bool:
        base_name = cls._state_base_name(prop.name)
        return bool(base_name and base_name in props_by_name)

    @classmethod
    def _ordered_prop_batches(cls, props: list[Prop], props_by_name: dict[str, Prop]) -> list[list[Prop]]:
        base_props = [prop for prop in props if not cls._is_state_prop(prop, props_by_name)]
        state_props = [prop for prop in props if cls._is_state_prop(prop, props_by_name)]
        return [batch for batch in (base_props, state_props) if batch]

    @staticmethod
    def _generation_concurrency(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        for source in (params, options):
            for key in ("concurrency", "prop_image_generation_concurrency", "image_generation_concurrency"):
                try:
                    value = int(source.get(key) or 0)
                except (AttributeError, TypeError, ValueError):
                    continue
                if value > 0:
                    return max(1, value)
        return 1

    def _prop_reference_refs(
        self,
        project_dir: Path,
        prop: Prop,
        props_by_name: dict[str, Prop],
    ) -> list[AssetRef]:
        base_name = self._state_base_name(prop.name)
        if not base_name:
            return []
        base_prop = props_by_name.get(base_name)
        if base_prop is None:
            return []
        path = base_prop.asset_path
        if path:
            path = self.layout.absolute_project_path(project_dir, path)
        if not (path or base_prop.asset_url):
            return []
        return [
            AssetRef(
                id=base_prop.asset_id or base_prop.id,
                type="image",
                path=path,
                url=base_prop.asset_url,
                metadata={
                    "asset_type": "prop",
                    "reference_for": prop.id,
                    "reference_role": "base_prop_for_state",
                    "prop_id": base_prop.id,
                    "prop_name": base_prop.name,
                },
            )
        ]

    async def _generate_one_prop(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        prop: Prop,
        props_by_name: dict[str, Prop],
        semaphore: asyncio.Semaphore,
    ) -> StaticAssetGenerationItem:
        prompt = self.prop_prompt_for_generation(project_dir, prop)
        refs = []
        if getattr(provider, "supports_reference_images", False):
            refs = self._prop_reference_refs(project_dir, prop, props_by_name)
        async with semaphore:
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
                    "reference_prop_ids": [ref.id for ref in refs if ref.id],
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
        item = StaticAssetGenerationItem(
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
        self.logger.info("%s generated successfully, saved in %s", prop.id, asset_path)
        return item

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("prop", node_name=self.name)
        self.logger.info(
            "node=prop_image_generation provider=%s model=%s",
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
        if active_episode_keys:
            self.logger.info(
                "node=prop_image_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(props),
            )
        self.logger.info("node=prop_image_generation total_images=%d", len(props))
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
                    self.logger.warning("prop_image_generation ignored invalid existing node output %s: %s", path, exc)
        concurrency = self._generation_concurrency(provider)
        self.logger.info("node=prop_image_generation concurrency=%d", concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        props_by_name = {prop.name: prop for prop in all_props}
        for batch in self._ordered_prop_batches(props, props_by_name):
            batch_items = await asyncio.gather(
                *[
                    self._generate_one_prop(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        prop=prop,
                        props_by_name=props_by_name,
                        semaphore=semaphore,
                    )
                    for prop in batch
                ]
            )
            for item in batch_items:
                generated.append(item)
                generated_by_asset_id[item.asset_id] = item
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


class PropGenerationNode(PropImageGenerationNode):
    name = "prop_generation"


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
        generated_layout_intro = self.existing_generated_layout_intro(project_dir, state)
        output = await self.asset_service.layout_extract(
            state,
            provider,
            novel_full_all_episodes=self.novel_full_contents(project_dir, state, episode_keys),
            generated_layout_intro=generated_layout_intro,
        )
        output.generated_layout_intro = self.normalize_generated_layout_intro(output.generated_layout_intro)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutDedupeReviewNode(StaticAssetNodeBase):
    name = "layout_dedupe_review"
    MAX_CONVERGENCE_ITERATIONS = 8

    @staticmethod
    def _max_iterations(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        try:
            return max(2, int(params.get("max_iterations") or LayoutDedupeReviewNode.MAX_CONVERGENCE_ITERATIONS))
        except (TypeError, ValueError):
            return LayoutDedupeReviewNode.MAX_CONVERGENCE_ITERATIONS

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout", node_name=self.name)
        extract_provider = self.router.text("layout", node_name=LayoutExtractNode.name)
        self.logger.info(
            "node=layout_dedupe_review provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        extract_output = self.load_layout_extract_output(project_dir)
        current_intro = self.normalize_generated_layout_intro(extract_output.generated_layout_intro)
        previous_review_intro: dict[str, str] | None = None
        final_output: LayoutDedupeReviewOutput | None = None
        novel_full_all_episodes = self.novel_full_contents(project_dir, state, self.expected_episode_keys(state))
        max_iterations = self._max_iterations(provider)

        for iteration in range(1, max_iterations + 1):
            output = await self.asset_service.layout_dedupe_review(
                state,
                provider,
                generated_layout_intro=current_intro,
            )
            output.generated_layout_intro = self.normalize_generated_layout_intro(output.generated_layout_intro)
            state.budget.used_text_calls += 1
            final_output = output
            self.logger.info(
                "node=layout_dedupe_review iteration=%d layouts=%d",
                iteration,
                len(output.generated_layout_intro),
            )

            if (
                previous_review_intro is not None
                and self.canonical_layout_intro(previous_review_intro)
                == self.canonical_layout_intro(output.generated_layout_intro)
            ):
                output.merge_notes.append(f"layout_extract/layout_dedupe_review converged after {iteration} dedupe pass(es).")
                break

            previous_review_intro = dict(output.generated_layout_intro)
            if iteration >= max_iterations:
                output.merge_notes.append(
                    f"layout_extract/layout_dedupe_review reached max_iterations={max_iterations}; using latest dedupe output."
                )
                self.logger.warning(
                    "node=layout_dedupe_review reached max_iterations=%d without exact convergence",
                    max_iterations,
                )
                break

            extract_output = await self.asset_service.layout_extract(
                state,
                extract_provider,
                novel_full_all_episodes=novel_full_all_episodes,
                generated_layout_intro=output.generated_layout_intro,
            )
            extract_output.generated_layout_intro = self.normalize_generated_layout_intro(extract_output.generated_layout_intro)
            state.budget.used_text_calls += 1
            self.repo.save_node_output(project_dir, LayoutExtractNode.name, extract_output)
            current_intro = extract_output.generated_layout_intro

        if final_output is None:
            raise ValueError("layout_dedupe_review produced no output")
        state.layouts = self.layouts_from_intro_and_prompts(
            final_output.generated_layout_intro,
            {},
            state,
            existing_layouts=state.layouts,
        )
        self.repo.save_node_output(project_dir, self.name, final_output)
        return state


class LayoutPromptNode(StaticAssetNodeBase):
    name = "layout_prompt"

    def _text_provider(self):
        return self.router.text("layout", node_name=self.name)

    def _layout_prompt_template_name(self) -> str:
        node_settings = self.repo.settings.nodes.get(self.name)
        params = getattr(node_settings, "params", {}) if node_settings is not None else {}
        explicit = str(params.get("prompt_template") or "").strip()
        if explicit:
            return explicit

        try:
            image_provider = self.router.image("layout", node_name=LayoutImageGenerationNode.name)
            provider_key = slugify(str(getattr(image_provider, "name", "") or "")).lower()
            model_key = slugify(str(getattr(image_provider, "model", "") or "")).lower()
        except Exception:
            provider_key = ""
            model_key = ""

        candidates = [
            f"layout_prompt_{provider_key}_{model_key}",
            f"layout_prompt_{provider_key}_seedream" if "seedream" in model_key else "",
            f"layout_prompt_{provider_key}_gpt_image_2" if "gpt_image" in model_key else "",
            f"layout_prompt_{model_key}",
            "layout_prompt_seedream" if "seedream" in model_key else "",
            "layout_prompt_gpt_image_2" if "gpt_image" in model_key else "",
            f"layout_prompt_{provider_key}",
            "layout_prompt",
        ]
        prompt_dir = self.asset_service.prompts.prompt_dir
        for candidate in candidates:
            if candidate and (prompt_dir / f"{candidate}.md").exists():
                return candidate
        return "layout_prompt"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._text_provider()
        prompt_template = self._layout_prompt_template_name()
        self.logger.info(
            "node=layout_prompt provider=%s model=%s prompt_template=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            prompt_template,
        )
        dedupe_output = self.load_layout_dedupe_output(project_dir)
        generated_layout_intro = self.normalize_generated_layout_intro(dedupe_output.generated_layout_intro)
        if not generated_layout_intro:
            raise ValueError("layout_prompt requires non-empty generated_layout_intro from layout_dedupe_review")
        output = await self.asset_service.layout_prompt(
            state,
            provider,
            generated_layout_intro=generated_layout_intro,
            prompt_template=prompt_template,
        )
        output.layout_prompts = self.normalize_generated_layout_intro(output.layout_prompts)
        state.layouts = self.layout_prompt_output_to_state(generated_layout_intro, output, state)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutImageGenerationNode(StaticAssetNodeBase):
    name = "layout_image_generation"

    @staticmethod
    def _state_base_name(layout_name: str) -> str | None:
        name = str(layout_name or "").strip()
        if "_" not in name:
            return None
        base, _status = name.rsplit("_", 1)
        base = base.strip()
        return base or None

    @classmethod
    def _is_state_layout(cls, layout: Layout, layouts_by_name: dict[str, Layout]) -> bool:
        base_name = cls._state_base_name(layout.name)
        return bool(base_name and base_name in layouts_by_name)

    @classmethod
    def _layout_generation_stages(
        cls,
        layouts: list[Layout],
        layouts_by_name: dict[str, Layout],
    ) -> list[tuple[str, list[Layout]]]:
        base_layouts = [layout for layout in layouts if not cls._is_state_layout(layout, layouts_by_name)]
        variant_layouts = [layout for layout in layouts if cls._is_state_layout(layout, layouts_by_name)]
        stages = [
            ("base", base_layouts),
            ("variant", variant_layouts),
        ]
        return [(stage_name, stage_layouts) for stage_name, stage_layouts in stages if stage_layouts]

    @classmethod
    def _ordered_layout_batches(cls, layouts: list[Layout], layouts_by_name: dict[str, Layout]) -> list[list[Layout]]:
        return [stage_layouts for _stage_name, stage_layouts in cls._layout_generation_stages(layouts, layouts_by_name)]

    @staticmethod
    def _generation_concurrency(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        for key in ("concurrency", "layout_image_generation_concurrency", "image_generation_concurrency"):
            try:
                value = int(params.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return max(1, value)
        return 1

    def _layout_reference_refs(
        self,
        project_dir: Path,
        layout: Layout,
        layouts_by_name: dict[str, Layout],
    ) -> list[AssetRef]:
        base_name = self._state_base_name(layout.name)
        if not base_name:
            return []
        base_layout = layouts_by_name.get(base_name)
        if base_layout is None:
            return []
        path = base_layout.asset_path
        if path:
            path = self.layout.absolute_project_path(project_dir, path)
        if not (path or base_layout.asset_url):
            return []
        return [
            AssetRef(
                id=base_layout.asset_id or base_layout.id,
                type="image",
                path=path,
                url=base_layout.asset_url,
                metadata={
                    "asset_type": "layout",
                    "reference_for": layout.id,
                    "reference_role": "base_layout_for_state",
                    "layout_id": base_layout.id,
                    "layout_name": base_layout.name,
                },
            )
        ]

    async def _generate_one_layout(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        layout: Layout,
        layouts_by_name: dict[str, Layout],
        semaphore: asyncio.Semaphore,
    ) -> StaticAssetGenerationItem:
        prompt = str(layout.prompt or "").strip()
        if not prompt:
            raise ValueError(f"layout_prompt is empty for {layout.id}; run pregen --only layout_prompt first")
        refs = self._layout_reference_refs(project_dir, layout, layouts_by_name)
        async with semaphore:
            result, prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                provider=provider,
                state=state,
                node_name=self.name,
                asset_id=layout.id,
                prompt=prompt,
                refs=refs,
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
                    "reference_layout_ids": [ref.id for ref in refs if ref.id],
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
        item = StaticAssetGenerationItem(
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
        self.logger.info("%s generated successfully, saved in %s", layout.id, asset_path)
        return item

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
        concurrency = self._generation_concurrency(provider)
        self.logger.info("node=layout_image_generation concurrency=%d", concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        layouts_by_name = {layout.name: layout for layout in all_layouts}
        for stage_name, batch in self._layout_generation_stages(layouts, layouts_by_name):
            self.logger.info(
                "node=layout_image_generation stage=%s target_images=%d",
                stage_name,
                len(batch),
            )
            batch_items = await asyncio.gather(
                *[
                    self._generate_one_layout(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        layout=layout,
                        layouts_by_name=layouts_by_name,
                        semaphore=semaphore,
                    )
                    for layout in batch
                ]
            )
            for item in batch_items:
                generated.append(item)
                generated_by_asset_id[item.asset_id] = item
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
        PropDedupeNode.name: PropDedupeNode(**deps),
        PropPromptNode.name: PropPromptNode(**deps),
        PropImageGenerationNode.name: PropImageGenerationNode(**deps),
        PropDesignNode.name: PropDesignNode(**deps),
        PropGenerationNode.name: PropGenerationNode(**deps),
        LayoutExtractNode.name: LayoutExtractNode(**deps),
        LayoutDedupeReviewNode.name: LayoutDedupeReviewNode(**deps),
        LayoutPromptNode.name: LayoutPromptNode(**deps),
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
    "LayoutExtractNode",
    "LayoutImageGenerationNode",
    "LayoutPromptNode",
    "PropDedupeNode",
    "PropDesignNode",
    "PropExtractNode",
    "PropImageGenerationNode",
    "PropPromptNode",
    "PropGenerationNode",
    "RoleboardGenerationNode",
    "StaticAssetNodeBase",
    "build_static_asset_node_runners",
    "build_static_asset_nodes",
]
