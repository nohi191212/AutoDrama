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
    LayoutPromptItem,
    LayoutPromptOutput,
    LayoutPropBoundaryReviewOutput,
    ProjectState,
    Prop,
    PropAsset,
    PropAssetExtractItem,
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
    "roleboard_image_generation",
    "prop_extract",
    "prop_finalize",
    "layout_extract",
    "layout_finalize",
    "layout_prop_boundary_review",
    "prop_prompt",
    "layout_prompt",
    "prop_image_generation",
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
                "安全审核",
                "未通过审核",
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
        fallback = ["shot", "role", "prop", "layout"]
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
            "将下方图像提示词改写为低风险版本。保留人物、道具、场景、构图、参考图编号和视觉连续性，"
            "但移除或弱化血液、伤口、尸体、内脏、断肢、写实暴力、恐怖、裸露、性暗示、仇恨标识、"
            "危险违法细节、可读文字、logo 和水印。可用尘土、旧污渍、衣物破损、疲惫神情、克制冲突氛围"
            "等低风险表达替代。\n\n只输出 JSON，包含 `prompt` 和可选的简短 `notes`。\n\n"
            f"原始图像提示词：\n{current_prompt}"
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

    def active_asset_ids(self) -> set[str]:
        """Return a precise image-asset selection for an in-workflow repair run."""
        return {str(item).strip() for item in getattr(self.workflow, "_active_asset_ids", set()) if str(item).strip()}

    def asset_is_selected(self, *identifiers: str | None) -> bool:
        selected = self.active_asset_ids()
        if not selected:
            return True
        return any(str(identifier or "").strip() in selected for identifier in identifiers)

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
        targets: list[tuple[Role, RoleAppearance]] = []
        for role in state.roles.values():
            if not self.role_matches_active_episode_keys(role, active_episode_keys, label=label):
                continue
            for appearance in role.appearances.values():
                if not self.asset_is_selected(appearance.id, self.roleboard_asset_id(appearance)):
                    continue
                if active_episode_keys and appearance.episode_keys:
                    if not self.intersects_active_episode_keys(appearance.episode_keys, active_episode_keys):
                        continue
                targets.append((role, appearance))
        return targets

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
                "layout_extract output is missing; run pregen --only layout_extract before layout_finalize"
            )
        return LayoutExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_layout_dedupe_output(self, project_dir: Path) -> LayoutDedupeReviewOutput:
        path = self.layout.node_output_path(project_dir, "layout_finalize")
        if not path.exists():
            raise FileNotFoundError(
                "layout_finalize output is missing; run pregen --only layout_finalize before layout_prop_boundary_review"
            )
        return LayoutDedupeReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_layout_prop_boundary_review_output(self, project_dir: Path) -> LayoutPropBoundaryReviewOutput:
        path = self.layout.node_output_path(project_dir, "layout_prop_boundary_review")
        if not path.exists():
            raise FileNotFoundError(
                "layout_prop_boundary_review output is missing; run pregen --only layout_prop_boundary_review before prop_prompt/layout_prompt"
            )
        return LayoutPropBoundaryReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _clean_string_list(values: object) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        if not isinstance(values, list):
            return result
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def normalize_layout_items(
        self,
        items: list[LayoutExtractItem] | None,
        state: ProjectState | None = None,
    ) -> list[LayoutExtractItem]:
        expected = set(self.expected_episode_keys(state)) if state is not None else set()
        normalized: list[LayoutExtractItem] = []
        seen_names: set[str] = set()
        seen_ids: set[str] = set()
        for item in items or []:
            name = str(item.name or "").strip()
            group = str(item.group or "").strip() or name
            asset_role = str(item.asset_role or "").strip()
            reference_asset_name = str(item.reference_asset_name or "").strip()
            brief = str(item.brief or "").strip()
            if not name or not brief:
                continue
            if asset_role not in {"base", "variant"}:
                raise ValueError(f"layout {name} has invalid asset_role: {asset_role}")
            if asset_role == "base":
                reference_asset_name = None
            elif not reference_asset_name:
                raise ValueError(f"layout variant {name} requires reference_asset_name")
            episode_keys = self._clean_string_list(item.episode_keys)
            if state is not None:
                if not episode_keys:
                    raise ValueError(f"layout {name} requires non-empty episode_keys")
                invalid_episode_keys = sorted(set(episode_keys) - expected)
                if invalid_episode_keys:
                    raise ValueError(
                        f"layout {name} has invalid episode_keys: {', '.join(invalid_episode_keys)}"
                    )
            layout_id = normalize_id("layout", name)
            if name in seen_names:
                raise ValueError(f"duplicated layout name: {name}")
            if layout_id in seen_ids:
                raise ValueError(f"duplicated layout id {layout_id} generated from {name}")
            seen_names.add(name)
            seen_ids.add(layout_id)
            state_delta = str(item.state_delta or "").strip()
            if asset_role == "base":
                state_delta = ""
            elif not state_delta:
                state_delta = brief
            normalized.append(
                LayoutExtractItem(
                    name=name,
                    group=group,
                    asset_role=asset_role,  # type: ignore[arg-type]
                    reference_asset_name=reference_asset_name,
                    episode_keys=episode_keys,
                    source_chapters=self._clean_string_list(item.source_chapters),
                    brief=brief,
                    space_features=self._clean_string_list(item.space_features),
                    state_delta=state_delta,
                )
            )
        base_names = {item.name for item in normalized if item.asset_role == "base"}
        for item in normalized:
            if item.asset_role == "variant" and item.reference_asset_name not in base_names:
                raise ValueError(
                    f"layout variant {item.name} references missing base layout {item.reference_asset_name}"
                )
        return normalized

    @classmethod
    def canonical_layout_items(cls, items: list[LayoutExtractItem] | None) -> str:
        import json

        payload = sorted(
            [
                {
                    "name": item.name,
                    "group": item.group,
                    "asset_role": item.asset_role,
                    "reference_asset_name": item.reference_asset_name,
                    "episode_keys": sorted(item.episode_keys),
                    "source_chapters": sorted(item.source_chapters),
                    "brief": item.brief,
                    "space_features": sorted(item.space_features),
                    "state_delta": item.state_delta,
                }
                for item in items or []
            ],
            key=lambda item: str(item["name"]),
        )
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def current_layout_items(self, state: ProjectState) -> list[LayoutExtractItem]:
        items: list[LayoutExtractItem] = []
        for layout in state.layouts.values():
            name = str(layout.name or "").strip()
            brief = str(layout.desc or "").strip()
            if not name or not brief:
                continue
            items.append(
                LayoutExtractItem(
                    name=name,
                    group=str(layout.group or "").strip() or name,
                    asset_role=layout.asset_role,
                    reference_asset_name=str(layout.reference_asset_name or "").strip() or None,
                    episode_keys=list(layout.episode_keys),
                    source_chapters=list(layout.source_chapters),
                    brief=brief,
                    space_features=list(layout.space_features),
                    state_delta=str(layout.state_delta or "").strip(),
                )
            )
        return self.normalize_layout_items(items, state)

    def existing_layout_items(self, project_dir: Path, state: ProjectState) -> list[LayoutExtractItem]:
        for node_name in ("layout_prop_boundary_review", "layout_finalize", "layout_extract"):
            path = self.layout.node_output_path(project_dir, node_name)
            if not path.exists():
                continue
            try:
                if node_name == "layout_prop_boundary_review":
                    output = LayoutPropBoundaryReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))
                elif node_name == "layout_finalize":
                    output = LayoutDedupeReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))
                else:
                    output = LayoutExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))
                items = self.normalize_layout_items(output.layouts, state)
            except Exception as exc:
                self.logger.warning("%s ignored invalid existing %s output %s: %s", self.__class__.__name__, node_name, path, exc)
                continue
            if items:
                return items
        return self.current_layout_items(state)

    def normalize_layout_prompt_items(
        self,
        prompt_items: list[LayoutPromptItem] | None,
        layout_items: list[LayoutExtractItem],
    ) -> dict[str, LayoutPromptItem]:
        layout_by_name = {item.name: item for item in layout_items}
        prompts: dict[str, LayoutPromptItem] = {}
        for prompt_item in prompt_items or []:
            name = str(prompt_item.name or "").strip()
            prompt = str(prompt_item.prompt or "").strip()
            if not name or not prompt:
                continue
            layout_item = layout_by_name.get(name)
            if layout_item is None:
                raise ValueError(f"layout_prompt returned unexpected layout: {name}")
            prompt_type = str(prompt_item.prompt_type or "").strip()
            expected_prompt_type = "image_edit" if layout_item.asset_role == "variant" else "text_to_image"
            if prompt_type != expected_prompt_type:
                raise ValueError(
                    f"layout_prompt {name} must use prompt_type={expected_prompt_type}; got {prompt_type}"
                )
            if name in prompts:
                raise ValueError(f"layout_prompt returned duplicated layout prompt: {name}")
            prompts[name] = LayoutPromptItem(
                name=name,
                group=str(prompt_item.group or layout_item.group or "").strip() or layout_item.group,
                asset_role=layout_item.asset_role,
                reference_asset_name=layout_item.reference_asset_name,
                prompt_type=expected_prompt_type,  # type: ignore[arg-type]
                prompt=prompt,
            )
        missing = [item.name for item in layout_items if item.name not in prompts]
        if missing:
            raise ValueError(f"layout_prompt missing prompt(s): {', '.join(missing)}")
        return prompts

    def layouts_from_items_and_prompts(
        self,
        layout_items: list[LayoutExtractItem],
        prompt_items: list[LayoutPromptItem] | None,
        state: ProjectState,
        *,
        existing_layouts: dict[str, Layout] | None = None,
    ) -> dict[str, Layout]:
        existing_layouts = existing_layouts or {}
        normalized_items = self.normalize_layout_items(layout_items, state)
        prompts = self.normalize_layout_prompt_items(prompt_items or [], normalized_items) if prompt_items else {}
        layouts: dict[str, Layout] = {}
        for item in normalized_items:
            layout_id = normalize_id("layout", item.name)
            existing = existing_layouts.get(layout_id)
            prompt_item = prompts.get(item.name)
            prompt = prompt_item.prompt if prompt_item else (existing.prompt if existing else "")
            layouts[layout_id] = Layout(
                id=layout_id,
                name=item.name,
                group=item.group,
                asset_role=item.asset_role,
                reference_asset_name=item.reference_asset_name,
                desc=item.brief,
                prompt=prompt,
                episode_keys=list(item.episode_keys),
                source_chapters=list(item.source_chapters),
                space_features=list(item.space_features),
                state_delta=item.state_delta,
                asset_id=existing.asset_id if existing else None,
                asset_path=existing.asset_path if existing else None,
                asset_url=existing.asset_url if existing else None,
                provider=existing.provider if existing else None,
                model=existing.model if existing else None,
                request_id=existing.request_id if existing else None,
                usage=dict(existing.usage) if existing else {},
                reference_image_kind="spatial_anchor",
                prompt_language="en",
            )
        return layouts

    def layout_prompt_output_to_state(
        self,
        layout_items: list[LayoutExtractItem],
        output: LayoutPromptOutput,
        state: ProjectState,
    ) -> dict[str, Layout]:
        normalized_items = self.normalize_layout_items(layout_items, state)
        prompt_by_name = self.normalize_layout_prompt_items(output.layout_prompts, normalized_items)
        return self.layouts_from_items_and_prompts(
            normalized_items,
            list(prompt_by_name.values()),
            state,
            existing_layouts=state.layouts,
        )

    @staticmethod
    def _asset_name_key(name: object) -> str:
        return str(name or "base").strip() or "base"

    @classmethod
    def prop_group_id(cls, name: str) -> str:
        return normalize_id("prop", name)

    @classmethod
    def prop_asset_key(cls, asset_name: object) -> str:
        return cls._asset_name_key(asset_name)

    @classmethod
    def prop_asset_id(cls, prop_name: str, asset_name: object = "base", status: object = "normal") -> str:
        prop_id = cls.prop_group_id(prop_name)
        asset_slug = slugify(cls._asset_name_key(asset_name), fallback="base")
        if asset_slug == "base":
            return f"{prop_id}__base"
        status_key = cls.prop_status_key(status)
        if status_key != "normal" and status_key != asset_slug:
            asset_slug = f"{asset_slug}_{status_key}"
        return f"{prop_id}__{asset_slug}"

    def prop_asset_episode_keys(
        self,
        prop_name: str,
        asset_name: str,
        episode_keys: list[str],
        fallback_episode_keys: list[str],
        state: ProjectState,
        *,
        label: str,
    ) -> list[str]:
        selected = episode_keys or fallback_episode_keys
        return self.prop_episode_keys(f"{prop_name}/{asset_name}", selected, state, label=label)

    @staticmethod
    def prop_output_payload(output: PropExtractOutput | PropDedupeOutput | LayoutPropBoundaryReviewOutput) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in output.props]

    @classmethod
    def canonical_prop_items(cls, items: list[PropExtractItem] | None) -> str:
        import json

        payload = [
            {
                "name": item.name,
                "aliases": sorted(item.aliases),
                "intro": item.intro,
                "episode_keys": sorted(item.episode_keys),
                "source_chapters": sorted(item.source_chapters),
                "owner_role_name": item.owner_role_name,
                "assets": sorted(
                    [
                        {
                            "name": asset.name,
                            "asset_role": asset.asset_role,
                            "status": asset.status,
                            "reference_asset_name": asset.reference_asset_name,
                            "episode_keys": sorted(asset.episode_keys),
                            "source_chapters": sorted(asset.source_chapters),
                            "desc": asset.desc,
                            "visual_features": asset.visual_features,
                            "state_change": asset.state_change,
                            "prompt_hint": asset.prompt_hint,
                        }
                        for asset in item.assets
                    ],
                    key=lambda asset: (str(asset["asset_role"]), str(asset["name"]), str(asset["status"])),
                ),
            }
            for item in items or []
        ]
        payload = sorted(payload, key=lambda item: str(item["name"]))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def prop_items_from_props(props: dict[str, Prop]) -> list[PropExtractItem]:
        items: list[PropExtractItem] = []
        for prop in props.values():
            items.append(
                PropExtractItem(
                    name=prop.name,
                    aliases=list(prop.aliases),
                    intro=prop.intro,
                    episode_keys=list(prop.episode_keys),
                    source_chapters=list(prop.source_chapters),
                    owner_role_name=prop.owner_role_name,
                    assets=[
                        PropAssetExtractItem(
                            name=asset.name,
                            asset_role=asset.asset_role,
                            status=asset.status,
                            reference_asset_name=asset.reference_asset_name,
                            episode_keys=list(asset.episode_keys),
                            source_chapters=list(asset.source_chapters),
                            desc=str(asset.desc or ""),
                            visual_features=asset.visual_features,
                            state_change=asset.state_change,
                            prompt_hint=asset.prompt_hint,
                        )
                        for asset in prop.assets.values()
                    ],
                )
            )
        return items

    def current_prop_payload(self, state: ProjectState) -> list[dict[str, object]]:
        return [prop.model_dump(mode="json") for prop in state.props.values() if not prop.owner_role_id]

    def existing_prop_payload(self, project_dir: Path, state: ProjectState) -> list[dict[str, object]]:
        for node_name in ("layout_prop_boundary_review", "prop_finalize", "prop_extract"):
            path = self.layout.node_output_path(project_dir, node_name)
            if not path.exists():
                continue
            try:
                if node_name == "layout_prop_boundary_review":
                    output = LayoutPropBoundaryReviewOutput.model_validate_json(path.read_text(encoding="utf-8"))
                elif node_name == "prop_finalize":
                    output = PropDedupeOutput.model_validate_json(path.read_text(encoding="utf-8"))
                else:
                    output = PropExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.warning("%s ignored invalid existing %s output %s: %s", self.__class__.__name__, node_name, path, exc)
                continue
            payload = self.prop_output_payload(output)
            if payload:
                return payload
        return self.current_prop_payload(state)

    def load_prop_extract_output(self, project_dir: Path) -> PropExtractOutput:
        path = self.layout.node_output_path(project_dir, "prop_extract")
        if not path.exists():
            raise FileNotFoundError(
                "prop_extract output is missing; run pregen --only prop_extract before prop_finalize"
            )
        return PropExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_prop_finalize_output(self, project_dir: Path) -> PropDedupeOutput:
        path = self.layout.node_output_path(project_dir, "prop_finalize")
        if not path.exists():
            raise FileNotFoundError(
                "prop_finalize output is missing; run pregen --only prop_finalize before layout_prop_boundary_review"
            )
        return PropDedupeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def props_from_extract_items(
        self,
        project_dir: Path,
        items: list[PropExtractItem],
        state: ProjectState,
        *,
        existing_props: dict[str, Prop] | None = None,
        source: str,
        save_records: bool = False,
    ) -> dict[str, Prop]:
        existing_props = existing_props or {}
        props: dict[str, Prop] = {}
        for item in items:
            prop_id = self.prop_group_id(item.name)
            if prop_id in props:
                raise ValueError(f"{source} returned duplicated prop group id for {item.name}")
            prop_episode_keys = self.prop_episode_keys(item.name, item.episode_keys, state, label=source)
            existing_prop = existing_props.get(prop_id)
            prop = Prop(
                id=prop_id,
                name=item.name,
                intro=item.intro,
                aliases=self.dedupe_texts(item.aliases),
                episode_keys=prop_episode_keys,
                source_chapters=self.dedupe_texts(item.source_chapters),
                owner_role_name=item.owner_role_name,
                source=source,
                design_path=existing_prop.design_path if existing_prop else None,
            )
            for raw_asset in item.assets:
                asset_name = self.prop_asset_key(raw_asset.name)
                asset_role = str(raw_asset.asset_role or "base").strip().lower()
                if asset_role not in {"base", "variant"}:
                    raise ValueError(f"{source} returned prop asset with invalid asset_role: {item.name}/{asset_name}")
                reference_asset_name = str(raw_asset.reference_asset_name or "").strip() or None
                if asset_role == "base":
                    reference_asset_name = None
                elif not reference_asset_name:
                    raise ValueError(f"{source} returned prop variant without reference_asset_name: {item.name}/{asset_name}")
                asset_episode_keys = self.prop_asset_episode_keys(
                    item.name,
                    asset_name,
                    raw_asset.episode_keys,
                    prop_episode_keys,
                    state,
                    label=source,
                )
                asset_id = self.prop_asset_id(item.name, asset_name, raw_asset.status)
                existing_asset = existing_prop.assets.get(asset_name) if existing_prop else None
                asset = PropAsset(
                    id=asset_id,
                    prop_id=prop_id,
                    name=asset_name,
                    asset_role=asset_role,  # type: ignore[arg-type]
                    reference_asset_name=reference_asset_name,
                    status=raw_asset.status,
                    episode_keys=asset_episode_keys,
                    source_chapters=self.dedupe_texts(raw_asset.source_chapters),
                    desc=raw_asset.desc,
                    visual_features=raw_asset.visual_features,
                    state_change=raw_asset.state_change,
                    prompt_hint=raw_asset.prompt_hint,
                    prompt=existing_asset.prompt if existing_asset else None,
                    prompt_type=existing_asset.prompt_type if existing_asset else None,
                    design_path=existing_asset.design_path if existing_asset else None,
                    asset_id=existing_asset.asset_id if existing_asset else None,
                    asset_path=existing_asset.asset_path if existing_asset else None,
                    asset_url=existing_asset.asset_url if existing_asset else None,
                    provider=existing_asset.provider if existing_asset else None,
                    model=existing_asset.model if existing_asset else None,
                    request_id=existing_asset.request_id if existing_asset else None,
                    usage=dict(existing_asset.usage) if existing_asset else {},
                )
                prop.assets[asset_name] = asset
            if not prop.assets:
                raise ValueError(f"{source} returned prop without assets: {item.name}")
            base_asset_names = {asset.name for asset in prop.assets.values() if asset.asset_role == "base"}
            if not base_asset_names:
                raise ValueError(f"{source} returned prop without base asset: {item.name}")
            for asset in prop.assets.values():
                if asset.asset_role == "variant" and asset.reference_asset_name not in base_asset_names:
                    raise ValueError(
                        f"{source} returned prop variant {item.name}/{asset.name} referencing missing base asset "
                        f"{asset.reference_asset_name}"
                    )
            if save_records:
                prop.design_path = self.prop_designs.save_prop_record(project_dir, prop, node_name=source)
                for asset in prop.assets.values():
                    asset.design_path = prop.design_path
            props[prop_id] = prop
        return props

    def prop_prompt_output_to_state(
        self,
        project_dir: Path,
        output: PropPromptOutput,
        state: ProjectState,
        *,
        source_prop_assets_path: str = "assets/json/nodes/prop_finalize.json",
    ) -> dict[str, Prop]:
        props = {prop_id: prop.model_copy(deep=True) for prop_id, prop in state.props.items()}
        prop_by_name = {self.prop_name_key(prop.name): prop for prop in props.values()}
        seen: set[tuple[str, str]] = set()
        for item in output.prop_asset_prompts:
            prop = prop_by_name.get(self.prop_name_key(item.prop_name))
            if prop is None:
                raise ValueError(f"prop_prompt returned unknown prop: {item.prop_name}")
            asset_name = self.prop_asset_key(item.asset_name)
            asset = prop.assets.get(asset_name)
            if asset is None:
                raise ValueError(f"prop_prompt returned unknown asset: {item.prop_name}/{asset_name}")
            prompt = str(item.prompt or "").strip()
            if not prompt:
                raise ValueError(f"prop_prompt returned empty prompt: {item.prop_name}/{asset_name}")
            asset.prompt = prompt
            asset.prompt_type = item.prompt_type
            asset.reference_asset_name = item.reference_asset_name or asset.reference_asset_name
            prop.source = "prop_prompt"
            prop.design_path = self.prop_designs.save_prop_record(
                project_dir,
                prop,
                node_name="prop_prompt",
                extra_payload={"source_prop_assets_path": source_prop_assets_path},
            )
            asset.design_path = prop.design_path
            seen.add((prop.id, asset_name))
        missing = [
            f"{prop.name}/{asset.name}"
            for prop in props.values()
            for asset in prop.assets.values()
            if (prop.id, asset.name) not in seen and not str(asset.prompt or "").strip()
        ]
        if missing:
            raise ValueError(f"prop_prompt missing prompt(s): {', '.join(missing)}")
        return props

    def prop_prompt_for_generation(self, project_dir: Path, prop: Prop, asset: PropAsset) -> str:
        prompt = str(asset.prompt or "").strip()
        if prompt:
            return prompt
        content = self.prop_designs.load_prop_content(project_dir, prop)
        assets = content.get("assets") if isinstance(content, dict) else None
        loaded_asset = assets.get(asset.name) if isinstance(assets, dict) else None
        if isinstance(loaded_asset, dict):
            prompt = str(loaded_asset.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Prop asset {asset.id} is missing prompt; expected it in state or {prop.design_path}")
        asset.prompt = prompt
        return prompt

    def update_prop_design_image_result(self, project_dir: Path, prop: Prop, asset: PropAsset, result, asset_path: str) -> None:
        self.prop_designs.update_asset_image_result(project_dir, prop, asset, result, asset_path)

    @staticmethod
    def prop_asset_matches_active_episode_keys(asset: PropAsset, active_episode_keys: list[str]) -> bool:
        if not active_episode_keys:
            return True
        return bool(set(asset.episode_keys).intersection(active_episode_keys))

    @classmethod
    def iter_prop_assets(cls, props: dict[str, Prop]) -> list[tuple[Prop, PropAsset]]:
        result: list[tuple[Prop, PropAsset]] = []
        for prop in props.values():
            for asset in prop.assets.values():
                result.append((prop, asset))
        return result

    @classmethod
    def ordered_prop_assets_for_generation(cls, props: dict[str, Prop]) -> list[tuple[Prop, PropAsset]]:
        indexed = list(enumerate(cls.iter_prop_assets(props)))
        ordered = sorted(
            indexed,
            key=lambda item: (
                0 if str(item[1][1].asset_role or "base") == "base" else 1,
                item[0],
            ),
        )
        return [pair for _, pair in ordered]

    def prop_reference_refs(self, project_dir: Path, prop: Prop, asset: PropAsset) -> list[AssetRef]:
        if asset.asset_role == "base":
            return []
        reference_name = str(asset.reference_asset_name or "").strip()
        if not reference_name:
            raise ValueError(f"prop variant {prop.name}/{asset.name} requires reference_asset_name")
        reference_asset = prop.assets.get(reference_name)
        if reference_asset is None or reference_asset is asset:
            raise ValueError(f"prop variant {prop.name}/{asset.name} references missing base asset {reference_name}")
        if reference_asset.asset_role != "base":
            raise ValueError(f"prop variant {prop.name}/{asset.name} must reference a base asset")
        path = reference_asset.asset_path
        absolute_path = self.layout.absolute_project_path(project_dir, path) if path else None
        if not (absolute_path or reference_asset.asset_url):
            raise ValueError(
                f"prop variant {prop.name}/{asset.name} requires generated base image for {reference_asset.name}"
            )
        return [
            AssetRef(
                id=reference_asset.asset_id or reference_asset.id,
                type="image",
                path=absolute_path,
                url=reference_asset.asset_url,
                metadata={
                    "asset_type": "prop",
                    "reference_for": asset.id,
                    "reference_role": "base_prop_for_variant",
                    "prop_id": prop.id,
                    "prop_name": prop.name,
                    "prop_asset_id": reference_asset.id,
                    "prop_asset_name": reference_asset.name,
                },
            )
        ]
        return refs

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
                "layout_image_generation requires the key vision image; run key_vision_image_generation first"
            )

        path: str | None = None
        if asset_path:
            existing = self.layout.existing_project_file(project_dir, str(asset_path))
            if existing is None:
                if not asset_url:
                    raise FileNotFoundError(
                        f"layout_image_generation key vision image is missing: {asset_path}"
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
                "reference_role": "key_vision_style",
                "reference_index": 2,
                "name": str(name),
                "reference_for": reference_for,
                "identity_transfer_allowed": False,
            },
        )

class RoleAppearanceGenerationBase(StaticAssetNodeBase):
    DEFAULT_ROLEBOARD_IMAGE_GENERATION_CONCURRENCY = 1
    MAX_ROLEBOARD_IMAGE_GENERATION_CONCURRENCY = 5

    def roleboard_spatial_template_ref(self, *, reference_for: str) -> AssetRef:
        template_path = self.repo.settings.generation.roleboard_spatial_template_path
        if not template_path.is_file():
            raise FileNotFoundError(
                f"roleboard spatial template does not exist: {template_path}"
            )
        return AssetRef(
            id="roleboard_spatial_template",
            type="image",
            path=str(template_path),
            metadata={
                "asset_type": "roleboard_spatial_template",
                "reference_role": "spatial_template",
                "reference_index": 1,
                "reference_for": reference_for,
                "identity_transfer_allowed": False,
            },
        )

    def load_existing_generation_items(
        self,
        project_dir: Path,
        node_name: str,
        active_episode_keys: list[str],
    ) -> dict[str, StaticAssetGenerationItem]:
        if not active_episode_keys and not self.active_asset_ids():
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
        preserve_existing: bool,
    ) -> list[StaticAssetGenerationItem]:
        if preserve_existing and generated_by_asset_id:
            return [
                generated_by_asset_id[asset_id]
                for asset_id in ordered_asset_ids
                if asset_id in generated_by_asset_id
            ]
        return generated

    @staticmethod
    def roleboard_asset_id(appearance: RoleAppearance) -> str:
        return f"{appearance.id}_roleboard"

    def roleboard_prompt_for_generation(
        self,
        *,
        role: Role,
        appearance: RoleAppearance,
        refs: list[AssetRef],
    ) -> str:
        base_prompt = str(appearance.roleboard_prompt or appearance.prompt or "").strip()
        if not base_prompt:
            raise ValueError(
                f"Cannot generate roleboard for {role.name}/{appearance.name}: missing roleboard_prompt"
            )
        reference_instructions: list[str] = []
        for index, ref in enumerate(refs, start=1):
            reference_role = str(
                ref.metadata.get("reference_role")
                or ref.metadata.get("asset_type")
                or ""
            )
            if reference_role == "spatial_template":
                reference_instructions.append(
                    f"参考图片{index}只用于横向16:9身份板的三视图排布、正面/真侧面/背面顺序、"
                    "等尺度、共同脚底线、间距、中性站姿和完整全身可见；不得复制模板人物的身份、"
                    "脸、体型、发型、服饰、配件、道具或白模材质。"
                )
            elif reference_role == "key_vision_style":
                reference_instructions.append(
                    f"参考图片{index}用于统一人物设计语言与视觉呈现：沿用其共性的面部塑造方式、"
                    "身体比例、服装轮廓与结构逻辑、装饰密度、材质层次、色彩关系、光影和完成度，"
                    "并将这些设计原则重新应用于当前角色；不得复制其中任何具体人物的身份、具体五官、"
                    "发型、服装款式、配饰、武器、动作、场景或构图，不得改变当前角色设定。"
                )
            elif reference_role == "same_role_identity":
                reference_instructions.append(
                    f"参考图片{index}是同一角色的基础身份参考；保持稳定脸部、体型与可延续的身份特征，"
                    "只采用当前提示词明确要求的造型变化。"
                )
            else:
                raise ValueError(
                    f"Unsupported roleboard reference role at index {index}: {reference_role or '-'}"
                )
        return "\n".join(
            [
                *reference_instructions,
                base_prompt,
                "严格保留当前提示词已经明确的身份与造型。若仍有稳定可见特征未说明，依据角色年龄、"
                "身份、职业与整体视觉方向，具体设计脸型骨相、眉眼、鼻唇、发型轮廓、身体比例和服装结构；"
                "设计清晰可见、克制统一，避免无依据的标准美型、通用英雄脸和繁复装饰。",
                "只生成一张干净的 16:9 单角色身份板；避免身份漂移、重复主体、畸形肢体、文字、logo 和水印。",
            ]
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

    def roleboard_identity_reference_ref(
        self,
        project_dir: Path,
        role: Role,
        appearance: RoleAppearance,
    ) -> AssetRef | None:
        if appearance.asset_role == "base":
            return None
        reference_name = str(appearance.reference_asset_name or "").strip()
        if not reference_name:
            raise ValueError(f"role variant {role.name}/{appearance.name} requires reference_asset_name")
        reference = role.appearances.get(reference_name)
        if reference is None:
            raise ValueError(f"role variant {role.name}/{appearance.name} references missing base asset {reference_name}")
        if reference.asset_role != "base":
            raise ValueError(f"role variant {role.name}/{appearance.name} must reference a base asset")
        ref_path = reference.asset_path or reference.design_image_asset_path
        ref_url = reference.asset_url or reference.design_image_asset_url
        path: str | None = None
        if ref_path:
            existing = self.layout.existing_project_file(project_dir, ref_path)
            if existing is not None:
                path = str(project_dir / existing)
        if not path and not ref_url:
            raise ValueError(
                f"role variant {role.name}/{appearance.name} requires generated base image for {reference.name}"
            )
        return AssetRef(
            id=reference.asset_id or reference.design_image_asset_id or reference.id,
            type="image",
            path=path,
            url=ref_url,
            metadata={
                "asset_type": "roleboard_identity_reference",
                "reference_role": "same_role_identity",
                "reference_index": 3,
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": reference.id,
                "appearance_name": reference.name,
                "reference_for": appearance.id,
                "identity_transfer_allowed": True,
            },
        )

    def roleboard_reference_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        appearance: RoleAppearance,
    ) -> list[AssetRef]:
        asset_id = self.roleboard_asset_id(appearance)
        refs = [
            self.roleboard_spatial_template_ref(reference_for=asset_id),
            self.key_vision_reference_ref(project_dir, state, reference_for=asset_id),
        ]
        identity_ref = self.roleboard_identity_reference_ref(project_dir, role, appearance)
        if identity_ref is not None:
            refs.append(identity_ref)

        expected_roles = ["spatial_template", "key_vision_style"]
        if appearance.asset_role == "variant":
            expected_roles.append("same_role_identity")
        actual_roles = [str(ref.metadata.get("reference_role") or "") for ref in refs]
        if actual_roles != expected_roles:
            raise ValueError(
                f"roleboard reference order mismatch for {role.name}/{appearance.name}: "
                f"expected {expected_roles}, got {actual_roles}"
            )
        return refs

    @classmethod
    def roleboard_image_generation_concurrency(cls, provider: object) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in (
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
                "roleboard_image_generation_concurrency",
                "image_generation_concurrency",
                "max_concurrent_images",
                "concurrency",
            ):
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = cls.DEFAULT_ROLEBOARD_IMAGE_GENERATION_CONCURRENCY
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = cls.DEFAULT_ROLEBOARD_IMAGE_GENERATION_CONCURRENCY
        return max(1, min(cls.MAX_ROLEBOARD_IMAGE_GENERATION_CONCURRENCY, resolved))

    async def generate_roleboard_asset(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        appearance: RoleAppearance,
        node_name: str,
        reuse_existing_assets: bool,
    ) -> StaticAssetGenerationItem:
        asset_id = self.roleboard_asset_id(appearance)
        refs = self.roleboard_reference_refs(project_dir, state, role, appearance)
        reference_roles = [str(ref.metadata.get("reference_role") or "") for ref in refs]
        identity_reference_index = 3 if appearance.asset_role == "variant" else None
        prompt = self.roleboard_prompt_for_generation(
            role=role,
            appearance=appearance,
            refs=refs,
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
                    "reference_roles": reference_roles,
                    "spatial_template_reference_index": 1,
                    "key_vision_reference_index": 2,
                    "identity_reference_index": identity_reference_index,
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
        state.metadata.pop("roleboard_anchor", None)
        if not getattr(provider, "supports_reference_images", False):
            raise ValueError(f"{node_name} requires an image provider that supports reference images")

        concurrency = self.roleboard_image_generation_concurrency(provider)
        self.logger.info(
            "%s total_images=%d concurrency=%d",
            node_name,
            len(appearances),
            concurrency,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(
            role: Role,
            appearance: RoleAppearance,
        ) -> StaticAssetGenerationItem:
            async with semaphore:
                return await self.generate_roleboard_asset(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    role=role,
                    appearance=appearance,
                    node_name=node_name,
                    reuse_existing_assets=reuse_existing_assets,
                )

        base_appearances = [
            (role, appearance)
            for role, appearance in appearances
            if appearance.asset_role == "base"
        ]
        variant_appearances = [
            (role, appearance)
            for role, appearance in appearances
            if appearance.asset_role == "variant"
        ]

        async def run_batch(batch: list[tuple[Role, RoleAppearance]]) -> list[StaticAssetGenerationItem]:
            tasks = [asyncio.create_task(generate_one(role, appearance)) for role, appearance in batch]
            try:
                return list(await asyncio.gather(*tasks)) if tasks else []
            except Exception:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        for batch in (base_appearances, variant_appearances):
            batch_generated = await run_batch(batch)
            generated.extend(batch_generated)
            for item in batch_generated:
                generated_by_asset_id[item.asset_id] = item
        return generated


class RoleboardGenerationNode(RoleAppearanceGenerationBase):
    name = "roleboard_image_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role", node_name=self.name)
        self.logger.info(
            "node=roleboard_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        active_episode_keys = self.active_episode_keys(state)
        all_appearances = self.target_role_appearances(state, [], label=self.name)
        appearances = self.target_role_appearances(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=roleboard_image_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(appearances),
            )
        self.logger.info("node=roleboard_image_generation total_images=%d", len(appearances))
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
            for _, appearance in all_appearances
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StaticAssetGenerationOutput(
                generated_assets=self.ordered_generation_items(
                    generated,
                    generated_by_asset_id,
                    ordered_ids,
                    bool(active_episode_keys or self.active_asset_ids()),
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
        existing_props = self.existing_prop_payload(project_dir, state)
        output = await self.asset_service.prop_extract(
            state,
            provider,
            novel_full_all_episodes=self.novel_full_contents(project_dir, state, episode_keys),
            existing_props=existing_props,
        )
        normalized_props = self.props_from_extract_items(
            project_dir,
            output.props,
            state,
            existing_props=state.props,
            source="prop_extract",
        )
        output.props = self.prop_items_from_props(normalized_props)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class PropDedupeNode(StaticAssetNodeBase):
    name = "prop_finalize"
    DEFAULT_MAX_ITERATIONS = 1

    @staticmethod
    def _max_iterations(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        try:
            return max(1, int(params.get("max_iterations") or PropDedupeNode.DEFAULT_MAX_ITERATIONS))
        except (TypeError, ValueError):
            return PropDedupeNode.DEFAULT_MAX_ITERATIONS

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop", node_name=self.name)
        extract_provider = self.router.text("prop", node_name=PropExtractNode.name)
        self.logger.info(
            "node=prop_finalize provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        extract_output = self.load_prop_extract_output(project_dir)
        current_props = self.prop_output_payload(extract_output)
        previous_review_props: str | None = None
        final_output: PropDedupeOutput | None = None
        novel_full_all_episodes = self.novel_full_contents(project_dir, state, self.expected_episode_keys(state))
        max_iterations = self._max_iterations(provider)

        for iteration in range(1, max_iterations + 1):
            output = await self.asset_service.prop_finalize(
                state,
                provider,
                props=current_props,
            )
            normalized_props = self.props_from_extract_items(
                project_dir,
                output.props,
                state,
                existing_props=state.props,
                source="prop_finalize",
            )
            output.props = self.prop_items_from_props(normalized_props)
            state.budget.used_text_calls += 1
            final_output = output
            review_key = self.canonical_prop_items(output.props)
            self.logger.info("node=prop_finalize iteration=%d props=%d", iteration, len(output.props))

            if max_iterations <= 1:
                output.merge_notes.append("prop_finalize completed as a single-pass audit.")
                break

            if previous_review_props is not None and previous_review_props == review_key:
                output.merge_notes.append(f"prop_extract/prop_finalize converged after {iteration} dedupe pass(es).")
                break

            previous_review_props = review_key
            if iteration >= max_iterations:
                output.merge_notes.append(
                    f"prop_extract/prop_finalize reached max_iterations={max_iterations}; using latest dedupe output."
                )
                self.logger.warning(
                    "node=prop_finalize reached max_iterations=%d without exact convergence",
                    max_iterations,
                )
                break

            extract_output = await self.asset_service.prop_extract(
                state,
                extract_provider,
                novel_full_all_episodes=novel_full_all_episodes,
                existing_props=self.prop_output_payload(output),
            )
            extract_props = self.props_from_extract_items(
                project_dir,
                extract_output.props,
                state,
                existing_props=state.props,
                source="prop_extract",
            )
            extract_output.props = self.prop_items_from_props(extract_props)
            state.budget.used_text_calls += 1
            self.repo.save_node_output(project_dir, PropExtractNode.name, extract_output)
            current_props = self.prop_output_payload(extract_output)

        if final_output is None:
            raise ValueError("prop_finalize produced no output")
        owner_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        finalized_props = self.props_from_extract_items(
            project_dir,
            final_output.props,
            state,
            existing_props=state.props,
            source="prop_finalize",
            save_records=True,
        )
        state.props = {**owner_role_props, **finalized_props}
        self.repo.save_node_output(project_dir, self.name, final_output)
        return state


class PropPromptNode(StaticAssetNodeBase):
    name = "prop_prompt"

    def _text_provider(self):
        return self.router.text("prop", node_name=self.name)

    @staticmethod
    def _fallback_prompt_output(state: ProjectState) -> PropPromptOutput:
        style = str(state.metadata.get("prop_design_style_prompt") or "高质感东方玄幻二维国漫插画").strip()
        prompts: list[dict[str, object]] = []
        for prop in state.props.values():
            for asset in prop.assets.values():
                visible_details = "；".join(
                    value
                    for value in (prop.intro, asset.desc, asset.visual_features, asset.prompt_hint)
                    if str(value or "").strip()
                )
                if asset.asset_role == "variant":
                    prompt = (
                        f"严格保持“{prop.name}”基础参考图的单件构图、结构、比例、材质和背景；"
                        f"仅呈现此状态变化：{asset.state_change or asset.desc or asset.name}。{style}。"
                    )
                    prompt_type = "image_edit"
                else:
                    prompt = (
                        f"单件道具“{prop.name}”：{visible_details}。{style}。"
                        "干净中性背景，主体完整、比例准确、材质和识别细节清楚；无人、无手持、无文字、无水印、无 logo。"
                    )
                    prompt_type = "text_to_image"
                prompts.append(
                    {
                        "prop_name": prop.name,
                        "asset_name": asset.name,
                        "prompt_type": prompt_type,
                        "reference_asset_name": asset.reference_asset_name,
                        "prompt": prompt,
                    }
                )
        return PropPromptOutput.model_validate({"prop_asset_prompts": prompts})

    def _prop_prompt_variant(self) -> str:
        node_settings = self.repo.settings.nodes.get(self.name)
        params = getattr(node_settings, "params", {}) if node_settings is not None else {}
        explicit = str(params.get("prompt_template") or "").strip()
        if explicit:
            return explicit.removesuffix(".md")

        try:
            image_provider = self.router.image("prop", node_name=PropImageGenerationNode.name)
            provider_key = slugify(str(getattr(image_provider, "name", "") or "")).lower()
            model_key = slugify(str(getattr(image_provider, "model", "") or "")).lower()
        except Exception:
            provider_key = ""
            model_key = ""

        candidates = [
            f"{provider_key}_{model_key}",
            model_key,
            provider_key,
            "default",
        ]
        prompt_dir = self.asset_service.prompts.prompt_dir
        for candidate in candidates:
            if candidate and (prompt_dir / self.name / f"{candidate}.md").exists():
                return candidate
        return "default"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._text_provider()
        prompt_variant = self._prop_prompt_variant()
        self.logger.info(
            "node=prop_prompt provider=%s model=%s prompt_variant=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            prompt_variant,
        )
        source_prop_assets_path = "assets/json/nodes/prop_finalize.json"
        try:
            prop_source_output = self.load_layout_prop_boundary_review_output(project_dir)
            source_prop_assets_path = "assets/json/nodes/layout_prop_boundary_review.json"
        except FileNotFoundError:
            prop_source_output = self.load_prop_finalize_output(project_dir)
        if not prop_source_output.props:
            raise ValueError("prop_prompt requires non-empty props from layout_prop_boundary_review or prop_finalize")
        output = None
        prompted_props = None
        for attempt in range(1, 4):
            output = await self.asset_service.prop_prompt(
                state,
                provider,
                props=self.prop_output_payload(prop_source_output),
                prompt_variant=prompt_variant,
            )
            try:
                prompted_props = self.prop_prompt_output_to_state(
                    project_dir,
                    output,
                    state,
                    source_prop_assets_path=source_prop_assets_path,
                )
                break
            except ValueError as exc:
                if attempt >= 3:
                    self.logger.warning(
                        "prop_prompt LLM output remained incomplete after %d attempts: %s; using deterministic fallback prompts",
                        attempt,
                        exc,
                    )
                    output = self._fallback_prompt_output(state)
                    prompted_props = self.prop_prompt_output_to_state(
                        project_dir,
                        output,
                        state,
                        source_prop_assets_path=source_prop_assets_path,
                    )
                    break
                self.logger.warning(
                    "prop_prompt attempt %d/3 did not cover every prop asset: %s; retrying",
                    attempt,
                    exc,
                )
        if output is None or prompted_props is None:
            raise AssertionError("prop_prompt did not produce a complete output")
        owner_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        state.props = {
            **owner_role_props,
            **prompted_props,
        }
        state.budget.used_text_calls += attempt
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class PropImageGenerationNode(StaticAssetNodeBase):
    name = "prop_image_generation"

    @classmethod
    def _ordered_prop_batches(cls, prop_assets: list[tuple[Prop, PropAsset]]) -> list[list[tuple[Prop, PropAsset]]]:
        base_assets = [item for item in prop_assets if str(item[1].asset_role or "base") == "base"]
        variant_assets = [item for item in prop_assets if str(item[1].asset_role or "base") != "base"]
        return [batch for batch in (base_assets, variant_assets) if batch]

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

    async def _generate_one_prop_asset(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        prop: Prop,
        asset: PropAsset,
        semaphore: asyncio.Semaphore,
    ) -> StaticAssetGenerationItem:
        prompt = self.prop_prompt_for_generation(project_dir, prop, asset)
        refs = []
        if asset.asset_role == "variant" and not getattr(provider, "supports_reference_images", False):
            raise ValueError(f"prop variant {prop.name}/{asset.name} requires an image provider with reference image support")
        if getattr(provider, "supports_reference_images", False):
            refs = self.prop_reference_refs(project_dir, prop, asset)
        async with semaphore:
            result, prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
                provider=provider,
                state=state,
                node_name=self.name,
                asset_id=asset.id,
                prompt=prompt,
                refs=refs,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "prop_id": prop.id,
                    "prop_asset_id": asset.id,
                    "asset_id": asset.id,
                },
                context={
                    "asset_type": "prop",
                    "prop_id": prop.id,
                    "prop_name": prop.name,
                    "prop_asset_id": asset.id,
                    "prop_asset_name": asset.name,
                    "prop_status": asset.status,
                    "owner_role_id": prop.owner_role_id,
                    "owner_role_name": prop.owner_role_name,
                    "reference_prop_asset_ids": [ref.id for ref in refs if ref.id],
                },
            )
            asset_path = await self.media_store.write_first_generated_image(
                project_dir,
                self.layout.image_asset_path(project_dir, "props", asset.id),
                result,
            )
        asset_url = self.first_image_url(result)
        asset.prompt = prompt
        asset.asset_id = asset.id
        asset.asset_path = asset_path
        asset.asset_url = asset_url
        asset.provider = result.provider
        asset.model = result.model
        asset.request_id = result.request_id
        asset.usage = result.usage
        self.update_prop_design_image_result(project_dir, prop, asset, result, asset_path)
        item = StaticAssetGenerationItem(
            asset_id=asset.id,
            asset_type="prop",
            owner_id=prop.id,
            name=f"{prop.name}/{asset.name}",
            prompt=prompt,
            asset_path=asset_path,
            asset_url=asset_url,
            provider=result.provider,
            model=result.model,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )
        self.logger.info("%s generated successfully, saved in %s", asset.id, asset_path)
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
        all_prop_assets = self.ordered_prop_assets_for_generation(state.props)
        prop_assets = [
            (prop, asset)
            for prop, asset in all_prop_assets
            if self.prop_asset_matches_active_episode_keys(asset, active_episode_keys)
            and self.asset_is_selected(asset.id, asset.asset_id)
        ]
        if active_episode_keys or self.active_asset_ids():
            self.logger.info(
                "node=prop_image_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(prop_assets),
            )
        self.logger.info("node=prop_image_generation total_images=%d", len(prop_assets))
        generated_by_asset_id: dict[str, StaticAssetGenerationItem] = {}
        if active_episode_keys or self.active_asset_ids():
            path = self.layout.node_output_path(project_dir, self.name)
            if path.exists():
                try:
                    existing_output = StaticAssetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
                    generated_by_asset_id = {item.asset_id: item for item in existing_output.generated_assets}
                except Exception as exc:
                    self.logger.warning("prop_image_generation ignored invalid existing node output %s: %s", path, exc)
        concurrency = self._generation_concurrency(provider)
        self.logger.info("node=prop_image_generation concurrency=%d", concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        for batch in self._ordered_prop_batches(prop_assets):
            batch_items = await asyncio.gather(
                *[
                    self._generate_one_prop_asset(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        prop=prop,
                        asset=asset,
                        semaphore=semaphore,
                    )
                    for prop, asset in batch
                ]
            )
            for item in batch_items:
                generated.append(item)
                generated_by_asset_id[item.asset_id] = item
        if (active_episode_keys or self.active_asset_ids()) and generated_by_asset_id:
            ordered_generated = [
                generated_by_asset_id[asset.id]
                for _prop, asset in all_prop_assets
                if asset.id in generated_by_asset_id
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
        existing_layouts = self.existing_layout_items(project_dir, state)
        output = await self.asset_service.layout_extract(
            state,
            provider,
            novel_full_all_episodes=self.novel_full_contents(project_dir, state, episode_keys),
            existing_layouts=[item.model_dump() for item in existing_layouts],
        )
        output.layouts = self.normalize_layout_items(output.layouts, state)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutDedupeReviewNode(StaticAssetNodeBase):
    name = "layout_finalize"
    DEFAULT_MAX_ITERATIONS = 1

    @staticmethod
    def _max_iterations(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        try:
            return max(1, int(params.get("max_iterations") or LayoutDedupeReviewNode.DEFAULT_MAX_ITERATIONS))
        except (TypeError, ValueError):
            return LayoutDedupeReviewNode.DEFAULT_MAX_ITERATIONS

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout", node_name=self.name)
        extract_provider = self.router.text("layout", node_name=LayoutExtractNode.name)
        self.logger.info(
            "node=layout_finalize provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        extract_output = self.load_layout_extract_output(project_dir)
        current_items = self.normalize_layout_items(extract_output.layouts, state)
        previous_review_items: list[LayoutExtractItem] | None = None
        final_output: LayoutDedupeReviewOutput | None = None
        novel_full_all_episodes = self.novel_full_contents(project_dir, state, self.expected_episode_keys(state))
        max_iterations = self._max_iterations(provider)

        for iteration in range(1, max_iterations + 1):
            output = await self.asset_service.layout_finalize(
                state,
                provider,
                layouts=[item.model_dump() for item in current_items],
            )
            output.layouts = self.normalize_layout_items(output.layouts, state)
            state.budget.used_text_calls += 1
            final_output = output
            self.logger.info(
                "node=layout_finalize iteration=%d layouts=%d",
                iteration,
                len(output.layouts),
            )

            if max_iterations <= 1:
                output.merge_notes.append("layout_finalize completed as a single-pass audit.")
                break

            if (
                previous_review_items is not None
                and self.canonical_layout_items(previous_review_items)
                == self.canonical_layout_items(output.layouts)
            ):
                output.merge_notes.append(f"layout_extract/layout_finalize converged after {iteration} dedupe pass(es).")
                break

            previous_review_items = list(output.layouts)
            if iteration >= max_iterations:
                output.merge_notes.append(
                    f"layout_extract/layout_finalize reached max_iterations={max_iterations}; using latest dedupe output."
                )
                self.logger.warning(
                    "node=layout_finalize reached max_iterations=%d without exact convergence",
                    max_iterations,
                )
                break

            extract_output = await self.asset_service.layout_extract(
                state,
                extract_provider,
                novel_full_all_episodes=novel_full_all_episodes,
                existing_layouts=[item.model_dump() for item in output.layouts],
            )
            extract_output.layouts = self.normalize_layout_items(extract_output.layouts, state)
            state.budget.used_text_calls += 1
            self.repo.save_node_output(project_dir, LayoutExtractNode.name, extract_output)
            current_items = extract_output.layouts

        if final_output is None:
            raise ValueError("layout_finalize produced no output")
        state.layouts = self.layouts_from_items_and_prompts(
            final_output.layouts,
            [],
            state,
            existing_layouts=state.layouts,
        )
        self.repo.save_node_output(project_dir, self.name, final_output)
        return state


class LayoutPropBoundaryReviewNode(StaticAssetNodeBase):
    name = "layout_prop_boundary_review"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout", node_name=self.name)
        self.logger.info(
            "node=layout_prop_boundary_review provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        prop_output = self.load_prop_finalize_output(project_dir)
        layout_output = self.load_layout_dedupe_output(project_dir)
        output = await self.asset_service.layout_prop_boundary_review(
            state,
            provider,
            props=self.prop_output_payload(prop_output),
            layouts=[item.model_dump(mode="json") for item in self.normalize_layout_items(layout_output.layouts, state)],
        )
        finalized_props = self.props_from_extract_items(
            project_dir,
            output.props,
            state,
            existing_props=state.props,
            source="layout_prop_boundary_review",
            save_records=True,
        )
        output.props = self.prop_items_from_props(finalized_props)
        output.layouts = self.normalize_layout_items(output.layouts, state)

        owner_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        state.props = {**owner_role_props, **finalized_props}
        state.layouts = self.layouts_from_items_and_prompts(
            output.layouts,
            [],
            state,
            existing_layouts=state.layouts,
        )
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class LayoutPromptNode(StaticAssetNodeBase):
    name = "layout_prompt"

    def _text_provider(self):
        return self.router.text("layout", node_name=self.name)

    @staticmethod
    def _fallback_prompt_output(state: ProjectState, layouts: list[LayoutExtractItem]) -> LayoutPromptOutput:
        style = str(
            state.metadata.get("layout_design_style_prompt")
            or "premium production-ready scene design"
        ).strip()
        prompts: list[dict[str, object]] = []
        for layout in layouts:
            if layout.asset_role == "variant":
                prompt = (
                    f"Edit the referenced spatial-anchor sheet for {layout.reference_asset_name or layout.name}. "
                    "Preserve both complementary isometric views, topology, entrances, fixed structures, "
                    "materials, scale, north orientation, paths, framing, and lighting logic. "
                    f"Apply only this visible state change: {layout.state_delta or layout.brief}. {style}. "
                    "No people, camera overlays, character markers, readable text, logo, or watermark."
                )
                prompt_type = "image_edit"
            else:
                features = "; ".join(str(value) for value in layout.space_features if str(value).strip())
                prompt = (
                    f"Create one empty 2:3 spatial-anchor sheet for {layout.name}: {layout.brief}. "
                    f"Fixed spatial anchors: {features}. {style}. Show the same complete physical location "
                    "in two vertically stacked, complementary high-angle isometric views from opposite corners. "
                    "Lock topology, entrances, architecture, fixed set dressing, materials, scale, paths, and "
                    "lighting across both views. Include a small north arrow and orientation inset. No people, "
                    "camera overlays, character markers, readable text, logo, or watermark."
                )
                prompt_type = "text_to_image"
            prompts.append(
                {
                    "name": layout.name,
                    "group": layout.group,
                    "asset_role": layout.asset_role,
                    "reference_asset_name": layout.reference_asset_name,
                    "prompt_type": prompt_type,
                    "prompt": prompt,
                }
            )
        return LayoutPromptOutput.model_validate({"layout_prompts": prompts})

    def _layout_prompt_variant(self) -> str:
        image_node_settings = self.repo.settings.nodes.get(LayoutImageGenerationNode.name)
        image_params = getattr(image_node_settings, "params", {}) if image_node_settings is not None else {}
        explicit = str(image_params.get("prompt_template") or "").strip()
        if explicit:
            return explicit.removesuffix(".md")

        try:
            image_provider = self.router.image("layout", node_name=LayoutImageGenerationNode.name)
            provider_key = slugify(str(getattr(image_provider, "name", "") or "")).lower()
            model_key = slugify(str(getattr(image_provider, "model", "") or "")).lower()
        except Exception:
            provider_key = ""
            model_key = ""

        candidates = [
            f"{provider_key}_{model_key}",
            f"{provider_key}_seedream" if "seedream" in model_key else "",
            f"{provider_key}_gpt_image_2" if "gpt_image" in model_key else "",
            model_key,
            "seedream" if "seedream" in model_key else "",
            "gpt_image_2" if "gpt_image" in model_key else "",
            provider_key,
            "default",
        ]
        prompt_dir = self.asset_service.prompts.prompt_dir
        for candidate in candidates:
            if candidate and (prompt_dir / self.name / f"{candidate}.md").exists():
                return candidate
        return "default"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self._text_provider()
        prompt_variant = self._layout_prompt_variant()
        self.logger.info(
            "node=layout_prompt provider=%s model=%s prompt_variant=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            prompt_variant,
        )
        try:
            layout_source_output = self.load_layout_prop_boundary_review_output(project_dir)
            layout_items = self.normalize_layout_items(layout_source_output.layouts, state)
        except FileNotFoundError:
            dedupe_output = self.load_layout_dedupe_output(project_dir)
            layout_items = self.normalize_layout_items(dedupe_output.layouts, state)
        if not layout_items:
            raise ValueError("layout_prompt requires non-empty layouts from layout_prop_boundary_review or layout_finalize")
        output = None
        for attempt in range(1, 4):
            output = await self.asset_service.layout_prompt(
                state,
                provider,
                layouts=[item.model_dump() for item in layout_items],
                prompt_variant=prompt_variant,
            )
            try:
                self.normalize_layout_prompt_items(output.layout_prompts, layout_items)
                break
            except ValueError as exc:
                if attempt >= 3:
                    self.logger.warning(
                        "layout_prompt LLM output remained incomplete after %d attempts: %s; using deterministic fallback prompts",
                        attempt,
                        exc,
                    )
                    output = self._fallback_prompt_output(state, layout_items)
                    break
                self.logger.warning(
                    "layout_prompt attempt %d/3 did not cover every layout: %s; retrying",
                    attempt,
                    exc,
                )
        if output is None:
            raise AssertionError("layout_prompt did not produce an output")
        self.normalize_layout_prompt_items(output.layout_prompts, layout_items)
        state.layouts = self.layout_prompt_output_to_state(layout_items, output, state)
        state.budget.used_text_calls += attempt
        self.repo.save_node_output(project_dir, self.name, output)
        return state

class LayoutImageGenerationNode(StaticAssetNodeBase):
    name = "layout_image_generation"
    SPATIAL_ANCHOR_TEMPLATE_RELATIVE_PATH = Path(
        ".assets/image_templates/scene_spatial_anchor_template.png"
    )

    @staticmethod
    def _is_variant_layout(layout: Layout) -> bool:
        return layout.asset_role == "variant"

    @classmethod
    def _layout_generation_stages(
        cls,
        layouts: list[Layout],
        layouts_by_name: dict[str, Layout],
    ) -> list[tuple[str, list[Layout]]]:
        del layouts_by_name
        base_layouts = [layout for layout in layouts if not cls._is_variant_layout(layout)]
        variant_layouts = [layout for layout in layouts if cls._is_variant_layout(layout)]
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
        state: ProjectState,
    ) -> list[AssetRef]:
        if layout.asset_role != "variant":
            return [
                self.key_vision_reference_ref(
                    project_dir,
                    state,
                    reference_for=layout.id,
                )
            ]
        base_name = str(layout.reference_asset_name or "").strip()
        if not base_name:
            raise ValueError(f"layout variant {layout.name} requires reference_asset_name")
        base_layout = layouts_by_name.get(base_name)
        if base_layout is None:
            raise ValueError(f"layout variant {layout.name} references missing base layout {base_name}")
        path = base_layout.asset_path
        if path:
            path = self.layout.absolute_project_path(project_dir, path)
        if not (path or base_layout.asset_url):
            raise ValueError(
                f"layout variant {layout.name} requires generated base image for {base_layout.name}"
            )
        return [
            AssetRef(
                id=base_layout.asset_id or base_layout.id,
                type="image",
                path=str(path) if path else None,
                url=base_layout.asset_url,
                metadata={
                    "asset_type": "layout",
                    "reference_for": layout.id,
                    "reference_role": "base_layout_for_variant",
                    "layout_id": base_layout.id,
                    "layout_name": base_layout.name,
                },
            )
        ]

    @staticmethod
    def _layout_prompt_for_generation(layout: Layout, prompt: str) -> str:
        if layout.asset_role == "variant":
            return prompt
        template_contract = (
            "Reference image 1 is a style reference only. Transfer only its rendering language onto the scene: "
            "offline PBR material response, physically plausible global illumination, camera response, color "
            "grading, atmosphere and finish. Keep this layout as one coherent single view of the target location "
            "from a readable natural camera height and angle; do not split it into paired or stacked panels, and "
            "do not add a floor-plan inset, north arrow, compass, crop marks, or border. Do NOT copy the reference "
            "image's people, characters, faces, bodies, costumes, props, incense burner, pine tree, composition, "
            "camera framing, or any specific object. Do not imitate its white-clay rendering, letters, or lighting. "
            "You may reuse the reference image's level of detail, material fidelity, and finish."
        )
        if prompt.startswith(template_contract):
            return prompt
        return "\n\n".join([template_contract, prompt])

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
        refs = self._layout_reference_refs(project_dir, layout, layouts_by_name, state)
        prompt = self._layout_prompt_for_generation(layout, prompt)
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
                    "reference_roles": [
                        str(ref.metadata.get("reference_role") or "")
                        for ref in refs
                    ],
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
        if layout.asset_role == "base":
            layout.reference_image_kind = "spatial_anchor"
            layout.prompt_language = "en"
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

    def _recover_existing_layout_items(
        self,
        *,
        project_dir: Path,
        layouts: list[Layout],
        provider: Any,
        generated_by_asset_id: dict[str, StaticAssetGenerationItem],
    ) -> None:
        """Keep single-asset reruns from discarding valid on-disk layout records."""
        restored = 0
        for layout in layouts:
            if layout.id in generated_by_asset_id:
                continue
            image_path = self.layout.image_asset_path(project_dir, "layouts", layout.id)
            if not image_path.is_file():
                continue
            asset_path = self.layout.project_relative(project_dir, image_path)
            layout.asset_id = layout.id
            layout.asset_path = asset_path
            layout.provider = layout.provider or str(getattr(provider, "name", ""))
            layout.model = layout.model or str(getattr(provider, "model", ""))
            generated_by_asset_id[layout.id] = StaticAssetGenerationItem(
                asset_id=layout.id,
                asset_type="layout",
                owner_id=layout.id,
                name=layout.name,
                prompt=str(layout.prompt or ""),
                asset_path=asset_path,
                asset_url=layout.asset_url,
                provider=layout.provider,
                model=layout.model,
                request_id=layout.request_id,
                usage=layout.usage,
            )
            restored += 1
        if restored:
            self.logger.info("layout_image_generation recovered %d existing layout record(s)", restored)

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
            and self.asset_is_selected(layout.id, layout.asset_id)
        ]
        if active_episode_keys or self.active_asset_ids():
            self.logger.info(
                "node=layout_image_generation episode-scoped rerun episodes=%s target_images=%d",
                ",".join(active_episode_keys),
                len(layouts),
            )
        self.logger.info("node=layout_image_generation total_images=%d", len(layouts))
        generated_by_asset_id: dict[str, StaticAssetGenerationItem] = {}
        if active_episode_keys or self.active_asset_ids():
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
        if active_episode_keys or self.active_asset_ids():
            self._recover_existing_layout_items(
                project_dir=project_dir,
                layouts=all_layouts,
                provider=provider,
                generated_by_asset_id=generated_by_asset_id,
            )
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
        if (active_episode_keys or self.active_asset_ids()) and generated_by_asset_id:
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
        LayoutExtractNode.name: LayoutExtractNode(**deps),
        LayoutDedupeReviewNode.name: LayoutDedupeReviewNode(**deps),
        LayoutPropBoundaryReviewNode.name: LayoutPropBoundaryReviewNode(**deps),
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
    "LayoutPropBoundaryReviewNode",
    "PropDedupeNode",
    "PropExtractNode",
    "PropImageGenerationNode",
    "PropPromptNode",
    "RoleboardGenerationNode",
    "StaticAssetNodeBase",
    "build_static_asset_node_runners",
    "build_static_asset_nodes",
]
