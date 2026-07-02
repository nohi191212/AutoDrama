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
from autodrama.services.director_service import DirectorService
from autodrama.services.media_store import MediaStore
from autodrama.workflows.runner import WorkflowNode

KEY_VISION_ASSET_ID = "key_vision_original"
KEY_VISION_NAME = "主视觉原图"

DIRECTOR_NODE_NAMES = [
    "design_key_vision_prompt",
    "design_key_vision_image",
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

    def load_key_vision_prompt_output(self, project_dir: Path, state: ProjectState) -> KeyVisionPromptOutput:
        payload = state.metadata.get("key_vision_prompt")
        if isinstance(payload, dict):
            output = KeyVisionPromptOutput.model_validate(payload)
            output.prompt = str(output.prompt or "").strip()
            if output.prompt:
                return output

        path = self.layout.node_output_path(project_dir, DesignKeyVisionPromptNode.name)
        if not path.exists():
            raise FileNotFoundError(
                "design_key_vision_prompt output is missing; run pregen --only design_key_vision_prompt first"
            )
        output = KeyVisionPromptOutput.model_validate_json(path.read_text(encoding="utf-8"))
        output.prompt = str(output.prompt or "").strip()
        if not output.prompt:
            raise ValueError("design_key_vision_prompt output has an empty prompt")
        return output


class DesignKeyVisionPromptNode(DirectorNodeBase):
    name = "design_key_vision_prompt"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.text_provider()
        self.logger.info(
            "node=design_key_vision_prompt provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.director_service.design_key_vision_prompt(state, provider)
        path = self.repo.save_node_output(project_dir, self.name, output)
        state.metadata["key_vision_prompt"] = output.model_dump(mode="json")
        state.metadata["key_vision_prompt_path"] = self.layout.project_relative(project_dir, path)
        state.metadata["key_vision_asset_id"] = KEY_VISION_ASSET_ID
        state.metadata["key_vision_name"] = KEY_VISION_NAME
        state.budget.used_text_calls += 1
        return state


class DesignKeyVisionImageNode(DirectorNodeBase):
    name = "design_key_vision_image"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("key_vision", node_name=self.name)
        self.logger.info(
            "node=design_key_vision_image provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        prompt_output = self.load_key_vision_prompt_output(project_dir, state)
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
