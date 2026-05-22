from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import BGM, ProjectState, StaticAssetGenerationItem, StaticAssetGenerationOutput
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.media_store import MediaStore
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

BGM_NODE_NAMES = [
    "bgm_design",
    "bgm_generation",
]


class BGMNodeBase:
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        script_service: ScriptService,
        asset_service: AssetService,
        script_contents: ScriptContentRepository,
        media_store: MediaStore,
        logger: Any,
    ) -> None:
        self.repo = repo
        self.layout = layout
        self.router = router
        self.script_service = script_service
        self.asset_service = asset_service
        self.script_contents = script_contents
        self.media_store = media_store
        self.logger = logger

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

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


class BGMDesignNode(BGMNodeBase):
    name = "bgm_design"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("bgm_plan")
        self.logger.info(
            "node=bgm_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.bgm_design(
            state,
            provider,
            episode_stories=self.episode_stories(project_dir, state),
        )
        expected_bgm_count = self.repo.settings.project.bgm_count
        if len(output.bgms) != expected_bgm_count:
            raise ValueError(f"bgm_design must generate exactly {expected_bgm_count} BGM items; got {len(output.bgms)}")
        bgm_ids = [normalize_id("bgm", item.name) for item in output.bgms]
        if len(set(bgm_ids)) != expected_bgm_count:
            raise ValueError("bgm_design generated duplicate BGM names after id normalization")
        state.bgms = {
            bgm_id: BGM(
                id=bgm_id,
                name=item.name,
                mood=item.mood,
                prompt=item.prompt,
                usage_hint=item.usage_hint,
            )
            for bgm_id, item in zip(bgm_ids, output.bgms, strict=True)
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class BGMGenerationNode(BGMNodeBase):
    name = "bgm_generation"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.music("bgm")
        self.logger.info(
            "node=bgm_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        for bgm in state.bgms.values():
            result = await provider.generate_music(
                bgm.prompt,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "bgm_id": bgm.id,
                    "asset_id": bgm.id,
                },
            )
            asset_path = await self.media_store.write_generated_music(
                project_dir,
                self.layout.music_asset_path(project_dir, bgm.id, result.audio_format),
                result,
            )
            bgm.asset_id = result.audio_id or bgm.id
            bgm.asset_path = asset_path
            bgm.provider = result.provider
            bgm.model = result.model
            bgm.request_id = result.request_id
            bgm.duration_seconds = result.duration_seconds
            bgm.lyrics = result.lyrics
            bgm.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=bgm.id,
                    asset_type="bgm",
                    owner_id=bgm.id,
                    name=bgm.name,
                    prompt=bgm.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
        self.repo.save_node_output(project_dir, self.name, StaticAssetGenerationOutput(generated_assets=generated))
        return state


def build_bgm_node_runners(workflow: Any) -> dict[str, BGMNodeBase]:
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)
    deps = {
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "asset_service": workflow.asset_service,
        "script_contents": script_contents,
        "media_store": workflow.media_store,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        BGMDesignNode.name: BGMDesignNode(**deps),
        BGMGenerationNode.name: BGMGenerationNode(**deps),
    }


def build_bgm_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_bgm_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in BGM_NODE_NAMES
    ]


__all__ = [
    "BGM_NODE_NAMES",
    "BGMDesignNode",
    "BGMGenerationNode",
    "build_bgm_node_runners",
    "build_bgm_nodes",
]
