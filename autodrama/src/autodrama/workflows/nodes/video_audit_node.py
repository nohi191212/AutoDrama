"""Visual acceptance and precise regeneration for generated shot videos."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autodrama.core.schemas import (
    ProjectState,
    ShotManifestEpisodeOutput,
    ShotManifestItem,
    ShotVideoGenerationItem,
    ShotVideoGenerationOutput,
    VideoAssetAuditItem,
    VideoAssetAuditOutput,
)
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef
from autodrama.workflows.runner import EpisodeWorkflowNode


SHOT_VIDEO_AUDIT_NODE_NAME = "shot_video_audit"


class VideoAuditDecision(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    revised_prompt: str = ""
    rationale: str = ""


class ShotVideoAuditNode:
    name = SHOT_VIDEO_AUDIT_NODE_NAME

    def __init__(self, *, workflow: Any) -> None:
        self.workflow = workflow
        self.repo = workflow.repo
        self.layout = workflow.layout
        self.router = workflow.router
        self.prompts = workflow.prompts
        self.logger = getattr(workflow, "logger", None) or get_logger()

    def _max_attempts(self) -> int:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        try:
            return max(1, min(3, int(params.get("max_iterations", 2))))
        except (TypeError, ValueError):
            return 2

    @staticmethod
    def _asset_ref(project_dir: Path, shot: ShotManifestItem) -> AssetRef:
        if not shot.video_asset_path:
            raise FileNotFoundError(f"shot_video_audit cannot find generated video for {shot.shot_id}")
        path = project_dir / shot.video_asset_path
        if not path.is_file():
            raise FileNotFoundError(f"shot_video_audit video file is missing for {shot.shot_id}: {shot.video_asset_path}")
        return AssetRef(
            id=shot.video_asset_id or shot.shot_id,
            type="video",
            path=str(path),
            metadata={"asset_type": "shot_video", "shot_id": shot.shot_id},
        )

    @staticmethod
    def _expectation(shot: ShotManifestItem) -> str:
        return (
            f"镜头内容：{shot.shot_description or shot.content or shot.title}。"
            f"动作与叙事：{shot.narrative_angle}。"
            "动作自然连贯、节奏稳定，角色身份和服饰持续一致，肢体结构正确；"
            "画面不得出现字幕、logo、水印、假文字或无关主体。"
        )

    def _merge_regenerated_video(
        self,
        project_dir: Path,
        episode_key: str,
        item: ShotVideoGenerationItem,
    ) -> None:
        path = self.layout.node_output_path(project_dir, "shot_video_generation")
        output = ShotVideoGenerationOutput(generated_videos=[])
        if path.exists():
            output = ShotVideoGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        by_key = {(row.episode_key, row.shot_id): row for row in output.generated_videos}
        by_key[(episode_key, item.shot_id)] = item
        self.repo.save_node_output(
            project_dir,
            "shot_video_generation",
            ShotVideoGenerationOutput(generated_videos=list(by_key.values())),
        )

    async def _regenerate_one(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        shot_id: str,
    ) -> None:
        previous_force = getattr(self.workflow, "_force_generation", None)
        previous_shots = getattr(self.workflow, "_active_shot_selectors", None)
        self.workflow._force_generation = True
        self.workflow._active_shot_selectors = {shot_id}
        try:
            output = await self.workflow._run_shot_video_generation_for_episode(project_dir, state, episode_key)
            for item in output.generated_videos:
                if item.shot_id == shot_id:
                    self._merge_regenerated_video(project_dir, episode_key, item)
                    return
            raise ValueError(f"shot_video_audit regeneration produced no output for {shot_id}")
        finally:
            if previous_force is None:
                delattr(self.workflow, "_force_generation")
            else:
                self.workflow._force_generation = previous_force
            if previous_shots is None:
                delattr(self.workflow, "_active_shot_selectors")
            else:
                self.workflow._active_shot_selectors = previous_shots

    async def _audit_one(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        episode: ShotManifestEpisodeOutput,
        initial_shot: ShotManifestItem,
    ) -> VideoAssetAuditItem:
        shot = initial_shot
        max_repairs = self._max_attempts()
        for attempt in range(1, max_repairs + 2):
            request = self.prompts.render(
                "video_asset_audit",
                shot_name=shot.shot_id,
                expectation=self._expectation(shot),
                current_prompt=shot.final_video_prompt or shot.video_prompt,
            )
            decision = await provider.generate_json(
                request,
                VideoAuditDecision,
                temperature=0.1,
                refs=[self._asset_ref(project_dir, shot)],
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "episode_key": episode_key,
                    "shot_id": shot.shot_id,
                    "asset_id": shot.video_asset_id or shot.shot_id,
                    "prompt_asset_type": "video_audit",
                    "prompt_asset_name": shot.shot_id,
                    "reasoning_effort": "high",
                },
            )
            state.budget.used_text_calls += 1
            decision.issues = [str(issue).strip() for issue in decision.issues if str(issue).strip()]
            decision.revised_prompt = str(decision.revised_prompt or "").strip()
            if decision.approved:
                return VideoAssetAuditItem(
                    episode_key=episode_key,
                    shot_id=shot.shot_id,
                    asset_id=shot.video_asset_id or shot.shot_id,
                    approved=True,
                    issues=decision.issues,
                    rationale=str(decision.rationale or "").strip(),
                    attempts=attempt,
                )
            if not decision.revised_prompt:
                raise ValueError(f"shot_video_audit rejected {shot.shot_id} without a revised_prompt")
            if attempt > max_repairs:
                raise ValueError(
                    f"shot_video_audit could not obtain an accepted video for {initial_shot.shot_id} "
                    f"after {max_repairs} repair attempt(s)"
                )
            self.logger.warning(
                "shot_video_audit rejected episode=%s shot=%s attempt=%d/%d issues=%s; regenerating only this shot",
                episode_key,
                shot.shot_id,
                attempt,
                max_repairs,
                "; ".join(decision.issues) or "unspecified video issue",
            )
            shot.final_video_prompt = decision.revised_prompt
            shot.video_prompt = decision.revised_prompt
            self.workflow._save_shot_manifest(project_dir, episode)
            await self._regenerate_one(project_dir, state, episode_key, shot.shot_id)
            episode = self.workflow._load_shot_manifest(project_dir, episode_key)
            refreshed = next((row for row in episode.shots if row.shot_id == initial_shot.shot_id), None)
            if refreshed is None:
                raise ValueError(f"shot_video_audit regeneration lost manifest shot {initial_shot.shot_id}")
            shot = refreshed
        raise AssertionError("unreachable video audit loop")

    async def run(self, project_dir: Path, state: ProjectState, episode_key: str) -> VideoAssetAuditOutput:
        provider = self.router.text("shot", node_name=self.name)
        self.logger.info(
            "node=shot_video_audit provider=%s model=%s episode=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            episode_key,
        )
        episode = self.workflow._load_shot_manifest(project_dir, episode_key)
        audited = [
            await self._audit_one(
                provider=provider,
                project_dir=project_dir,
                state=state,
                episode_key=episode_key,
                episode=episode,
                initial_shot=shot,
            )
            for shot in self.workflow._active_shots_for_episode(episode)
        ]
        return VideoAssetAuditOutput(audited_videos=audited)


def build_shot_video_audit_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    runner = ShotVideoAuditNode(workflow=workflow)
    return EpisodeWorkflowNode(name=runner.name, run=runner.run)


__all__ = ["SHOT_VIDEO_AUDIT_NODE_NAME", "build_shot_video_audit_episode_node"]
