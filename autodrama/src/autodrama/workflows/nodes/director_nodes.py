from __future__ import annotations

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
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.director_service import DirectorService
from autodrama.services.media_store import MediaStore
from autodrama.workflows.runner import WorkflowNode

KEY_VISION_ASSET_ID = "key_vision_original"
KEY_VISION_NAME = "主视觉原图"

DIRECTOR_NODE_NAMES = [
    "key_vision_prompt",
    "key_vision_image_generation",
]


class DirectorNodeBase:
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
        script_contents = ScriptContentRepository(self.repo, self.layout)
        episode_keys = sorted(state.script.novel_extract)
        extracts = script_contents.load_contents(
            project_dir,
            state.script.novel_extract,
            episode_keys,
            label="script_novel_extract.novel_extract",
        )
        story_context = "\n\n".join(
            extracts[episode_key] for episode_key in episode_keys if extracts.get(episode_key)
        )
        if not story_context:
            raise ValueError("key_vision_prompt requires non-empty script_novel_extract content")
        image_provider = self.router.image(
            "key_vision",
            node_name=DesignKeyVisionImageNode.name,
        )
        image_canvas = str(getattr(image_provider, "size", "") or "").strip()
        if not image_canvas or image_canvas.lower() == "auto":
            raise ValueError("key_vision image provider must declare an image size")
        output = await self.director_service.key_vision_prompt(
            state,
            provider,
            story_context=story_context,
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
    }


def build_director_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_director_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in DIRECTOR_NODE_NAMES
    ]


__all__ = [
    "DIRECTOR_NODE_NAMES",
    "DesignKeyVisionImageNode",
    "DesignKeyVisionPromptNode",
    "KEY_VISION_ASSET_ID",
    "KEY_VISION_NAME",
    "build_director_node_runners",
    "build_director_nodes",
]
