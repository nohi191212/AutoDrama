from __future__ import annotations

from math import gcd
from pathlib import Path
from typing import Any

from autodrama.core.schemas import (
    KeyVisionPromptOutput,
    ProjectState,
    StaticAssetGenerationItem,
    StaticAssetGenerationOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.services.director_service import DirectorService
from autodrama.services.media_store import MediaStore
from autodrama.providers.base import AssetRef
from autodrama.workflows.runner import WorkflowNode

KEY_VISION_ASSET_ID = "key_vision_original"
KEY_VISION_NAME = "主视觉原图"

DIRECTOR_NODE_NAMES = [
    "key_vision_prompt",
    "key_vision_image_generation",
]

MANUAL_DIRECTOR_NODE_NAMES = [
    "key_vision_edit",
]


class DirectorNodeBase:
    _KEY_VISION_CANVAS_BY_RATIO = {
        "1:1": {"1k": "1024x1024", "2k": "2048x2048", "4k": "2880x2880"},
        "3:2": {"1k": "1536x1024", "2k": "3072x2048", "4k": "3456x2304"},
        "2:3": {"1k": "1024x1536", "2k": "2048x3072", "4k": "2304x3456"},
        "4:3": {"1k": "1280x960", "2k": "2560x1920", "4k": "3200x2400"},
        "3:4": {"1k": "960x1280", "2k": "1920x2560", "4k": "2400x3200"},
        "16:9": {"1k": "1920x1088", "2k": "1920x1088", "4k": "3840x2160"},
        "9:16": {"1k": "1088x1920", "2k": "1088x1920", "4k": "2160x3840"},
        "1:2": {"1k": "1088x1920", "2k": "1088x1920", "4k": "2160x3840"},
        "2:1": {"1k": "1920x1088", "2k": "1920x1088", "4k": "3840x2160"},
    }

    def __init__(
        self,
        *,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        director_service: DirectorService,
        media_store: MediaStore,
        logger: Any,
    ) -> None:
        self.repo = repo
        self.layout = layout
        self.router = router
        self.director_service = director_service
        self.media_store = media_store
        self.logger = logger

    def text_provider(self):
        try:
            return self.router.text("director", node_name=self.name)
        except KeyError:
            return self.router.text("script", node_name=self.name)

    @staticmethod
    def first_image_url(result: Any) -> str | None:
        image_urls = getattr(result, "image_urls", None)
        return image_urls[0] if image_urls else None

    @staticmethod
    def _normalize_aspect_ratio(value: Any) -> str | None:
        text = str(value or "").strip().lower().replace("×", "x")
        if not text:
            return None
        separator = ":" if ":" in text else "x" if "x" in text else None
        if separator is None:
            return None
        left, right = text.split(separator, 1)
        try:
            width = int(left.strip())
            height = int(right.strip())
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None
        divisor = gcd(width, height)
        return f"{width // divisor}:{height // divisor}"

    @staticmethod
    def _resolution_bucket(value: Any) -> str:
        text = str(value or "").strip().lower()
        if "4" in text or text in {"high", "large"}:
            return "4k"
        if "1" in text or text in {"low", "small"}:
            return "1k"
        return "2k"

    @classmethod
    def _banana_canvas(cls, aspect_ratio: Any, image_size: Any) -> str | None:
        ratio = cls._normalize_aspect_ratio(aspect_ratio)
        if ratio is None:
            return None
        return cls._KEY_VISION_CANVAS_BY_RATIO.get(ratio, {}).get(
            cls._resolution_bucket(image_size)
        )

    @classmethod
    def key_vision_image_canvas(cls, image_provider: Any) -> str:
        """Resolve the actual canvas used by GPT Image or Banana Pro."""
        binding = getattr(image_provider, "model_binding", None)
        binding_params = getattr(binding, "params", None)
        params = binding_params if isinstance(binding_params, dict) else {}

        # Banana Pro declares its canvas as aspectRatio + imageSize on the
        # node binding. Its inherited GPT Image ``size`` remains ``auto``.
        aspect_ratio = params.get("aspectRatio") or params.get("aspect_ratio")
        image_size = params.get("imageSize") or params.get("image_size")
        if aspect_ratio is None:
            aspect_ratio = getattr(image_provider, "aspect_ratio", None)
        if image_size is None:
            image_size = getattr(image_provider, "image_size", None)
        banana_canvas = cls._banana_canvas(aspect_ratio, image_size)
        if banana_canvas:
            return banana_canvas

        for candidate in (params.get("size"), getattr(image_provider, "size", None)):
            image_canvas = str(candidate or "").strip()
            if image_canvas and image_canvas.lower() != "auto":
                return image_canvas
        raise ValueError("key_vision image provider must declare an image size")

    def load_key_vision_prompt_output(self, state: ProjectState) -> KeyVisionPromptOutput:
        payload = state.metadata.get("key_vision_prompt")
        if not isinstance(payload, dict):
            raise ValueError("key_vision_prompt metadata is missing; run key_vision_prompt first")
        output = KeyVisionPromptOutput.model_validate(payload)
        for field_name in ("shot_contract", "scene_style_contract", "prompt"):
            value = str(getattr(output, field_name) or "").strip()
            if not value:
                raise ValueError(f"key_vision_prompt output has an empty {field_name}")
            setattr(output, field_name, value)
        return output


class DesignKeyVisionPromptNode(DirectorNodeBase):
    name = "key_vision_prompt"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.text_provider()
        self.logger.info(
            "node=key_vision_prompt provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        image_provider = self.router.image(
            "key_vision",
            node_name=DesignKeyVisionImageNode.name,
        )
        image_canvas = self.key_vision_image_canvas(image_provider)
        output = await self.director_service.key_vision_prompt(
            state,
            provider,
            image_canvas=image_canvas,
        )
        path = self.repo.save_node_output(project_dir, self.name, output)
        state.metadata["key_vision_prompt"] = output.model_dump(mode="json")
        state.metadata["key_vision_prompt_path"] = self.layout.project_relative(project_dir, path)
        state.metadata["key_vision_asset_id"] = KEY_VISION_ASSET_ID
        state.metadata["key_vision_name"] = KEY_VISION_NAME
        state.budget.used_text_calls += 1
        return state


class DesignKeyVisionImageNode(DirectorNodeBase):
    name = "key_vision_image_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("key_vision", node_name=self.name)
        self.logger.info(
            "node=key_vision_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        prompt_output = self.load_key_vision_prompt_output(state)
        prompt = prompt_output.prompt
        result = await provider.generate_image(
            prompt,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "asset_id": KEY_VISION_ASSET_ID,
                "asset_type": "key_vision",
            },
        )
        asset_path = await self.media_store.write_first_generated_image(
            project_dir,
            self.layout.image_asset_path(project_dir, "key_visions", KEY_VISION_ASSET_ID),
            result,
        )
        asset_url = self.first_image_url(result)
        item = StaticAssetGenerationItem(
            asset_id=KEY_VISION_ASSET_ID,
            asset_type="key_vision",
            owner_id=state.project_id,
            name=KEY_VISION_NAME,
            prompt=prompt,
            asset_path=asset_path,
            asset_url=asset_url,
            provider=result.provider,
            model=result.model,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )
        output = StaticAssetGenerationOutput(generated_assets=[item])
        path = self.repo.save_node_output(project_dir, self.name, output)
        state.metadata["key_vision_asset"] = item.model_dump(mode="json")
        state.metadata["key_vision_asset_path"] = asset_path
        state.metadata["key_vision_asset_url"] = asset_url
        state.metadata["key_vision_generation_path"] = self.layout.project_relative(project_dir, path)
        self.logger.info("%s generated successfully, saved in %s", KEY_VISION_ASSET_ID, asset_path)
        return state


class EditKeyVisionImageNode(DirectorNodeBase):
    """Edit the current key-vision image using the latest project audit feedback."""

    name = "key_vision_edit"

    @staticmethod
    def _feedback_text(state: ProjectState) -> str:
        feedback = DirectorService.key_vision_audit_feedback(state)
        if feedback == "（没有上一轮主视觉审计拒绝原因。）":
            return (
                "No previous audit feedback is available. Preserve the image and correct only any "
                "clearly visible character-to-architecture scale, body proportion, perspective, "
                "occlusion, or scene-causality defect; do not redesign the shot."
            )
        return feedback

    @classmethod
    def _default_edit_prompt(cls, state: ProjectState) -> str:
        return (
            "Edit the provided reference image as a targeted production repair. Preserve the same "
            "world, architecture, any anonymous scale figures, camera side, camera distance, lighting, "
            "palette, image dimensions, and overall composition. Apply "
            "only the visible structural corrections supported by the audit feedback below. Keep "
            "normal adult body proportions, believable character-to-building scale, one continuous "
            "ground plane, readable spatial perspective, and clear occlusion order. Do not enlarge "
            "a face, foot, hand, or foreground limb independently; do not invent a new scene or add "
            "plot elements. Keep the image faithful to the configured script type and global visual "
            "style, not live-action photography. Do not add people, text, subtitles, logo, watermark, "
            "weapons, or decorative effects.\n\n"
            "AUDIT FEEDBACK:\n"
            f"{cls._feedback_text(state)}"
        )

    @staticmethod
    def _source_item(project_dir: Path, layout: ProjectLayout) -> StaticAssetGenerationItem:
        path = layout.node_output_path(project_dir, DesignKeyVisionImageNode.name)
        if not path.exists():
            raise FileNotFoundError(
                "key_vision_edit requires key_vision_image_generation output; "
                "run key_vision_image_generation first"
            )
        output = StaticAssetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        if not output.generated_assets:
            raise ValueError("key_vision_image_generation output has no generated asset")
        return output.generated_assets[0]

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        source_item = self._source_item(project_dir, self.layout)
        existing = self.layout.existing_project_file(project_dir, source_item.asset_path)
        if not existing:
            raise FileNotFoundError(
                f"{self.name} cannot edit missing current image: "
                f"{source_item.asset_path or source_item.asset_id}"
            )

        provider = self.router.image("key_vision", node_name=self.name)
        prompt = str(state.metadata.get("key_vision_edit_prompt") or "").strip()
        if not prompt:
            prompt = self._default_edit_prompt(state)
        self.logger.info(
            "node=%s provider=%s model=%s source=%s",
            self.name,
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            source_item.asset_path or source_item.asset_id,
        )
        result = await provider.generate_image(
            prompt,
            refs=[
                AssetRef(
                    id=source_item.asset_id,
                    type="image",
                    path=str(project_dir / existing),
                    url=source_item.asset_url,
                    metadata={
                        "asset_type": "key_vision",
                        "reference_role": "current_key_vision_image",
                    },
                )
            ],
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "asset_id": source_item.asset_id,
                "asset_type": "key_vision",
                "prompt_asset_type": "key_vision_edit",
                "prompt_asset_name": source_item.asset_id,
            },
        )
        asset_path = await self.media_store.write_first_generated_image(
            project_dir,
            self.layout.image_asset_path(project_dir, "key_visions", f"{source_item.asset_id}_edit"),
            result,
        )
        edited_item = source_item.model_copy(
            update={
                "prompt": prompt,
                "asset_path": asset_path,
                "asset_url": self.first_image_url(result),
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "usage": result.usage,
                "raw_response": result.raw_response,
            }
        )
        edited_output = StaticAssetGenerationOutput(generated_assets=[edited_item])
        edit_output_path = self.repo.save_node_output(project_dir, self.name, edited_output)

        # The following audit group reads the generation node by contract. Point
        # that source at the edited current image while retaining the original
        # PNG and the edit node output for comparison and rollback.
        self.repo.save_node_output(project_dir, DesignKeyVisionImageNode.name, edited_output)
        state.metadata["key_vision_asset"] = edited_item.model_dump(mode="json")
        state.metadata["key_vision_asset_path"] = asset_path
        state.metadata["key_vision_asset_url"] = edited_item.asset_url
        state.metadata["key_vision_edit_path"] = self.layout.project_relative(project_dir, edit_output_path)
        state.metadata["key_vision_edit_source_path"] = source_item.asset_path
        self.logger.info("%s generated successfully, saved in %s", self.name, asset_path)
        return state


def build_director_node_runners(workflow: Any) -> dict[str, DirectorNodeBase]:
    media_store = getattr(workflow, "media_store", None)
    if media_store is None:
        timeout_seconds = getattr(getattr(workflow, "settings", None), "runtime", None)
        media_store = MediaStore(
            workflow.layout,
            timeout_seconds=getattr(timeout_seconds, "request_timeout_seconds", 120),
        )

    deps = {
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "director_service": workflow.director_service,
        "media_store": media_store,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        DesignKeyVisionPromptNode.name: DesignKeyVisionPromptNode(**deps),
        DesignKeyVisionImageNode.name: DesignKeyVisionImageNode(**deps),
        EditKeyVisionImageNode.name: EditKeyVisionImageNode(**deps),
    }


def build_director_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_director_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in DIRECTOR_NODE_NAMES
    ]


__all__ = [
    "DIRECTOR_NODE_NAMES",
    "MANUAL_DIRECTOR_NODE_NAMES",
    "DesignKeyVisionImageNode",
    "DesignKeyVisionPromptNode",
    "EditKeyVisionImageNode",
    "KEY_VISION_ASSET_ID",
    "KEY_VISION_NAME",
    "build_director_node_runners",
    "build_director_nodes",
]
