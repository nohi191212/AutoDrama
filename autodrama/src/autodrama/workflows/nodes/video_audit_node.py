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
from autodrama.utils.video_prompts import (
    VideoDialogueCue,
    classify_video_prompt_profile,
    is_grounded_locomotion_action,
    is_prop_transfer_action,
)
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
        dialogue_cues = [
            VideoDialogueCue(
                speaker=line.speaker_name or line.speaker_role_id or "未指定说话人",
                text=line.text,
                delivery_mode=line.delivery_mode,
                emotion=line.emotion,
            )
            for line in shot.dialogue_lines
        ]
        profile = classify_video_prompt_profile(
            dialogue_cues,
            duration_seconds=shot.duration_seconds,
        )
        if profile == "voiceover":
            exact_lines = "；".join(
                f"{cue.speaker}逐字旁白“{cue.text}”" for cue in dialogue_cues
            )
            audio_expectation = (
                f"旁白：{exact_lines}。旁白应随镜头开始并完整播放；所有画中人物始终闭嘴，"
                "输入中已有的画面动作与旁白同步推进。"
            )
        elif profile == "offscreen_dialogue":
            cue = dialogue_cues[0]
            audio_expectation = (
                f"画外对白：{cue.speaker}在画外逐字说“{cue.text}”。"
                "声音应在0.8秒内开始且声源始终在画外；所有画中人物始终闭嘴，"
                "输入中已有的画面动作与画外对白同步推进。"
            )
        elif profile == "multi_dialogue":
            exact_lines = "；".join(
                f"{cue.speaker}（{cue.delivery_mode}）逐字说“{cue.text}”" for cue in dialogue_cues
            )
            audio_expectation = (
                f"多句对白：{exact_lines}。实际音频必须完整、顺序和说话人正确；"
                "画中对白只允许当前具名说话者动嘴，所有听者闭嘴；"
                "画外对白和旁白不得激活任何画中人物嘴型；第一句应在0.8秒内开始。"
            )
        elif dialogue_cues:
            exact_lines = "；".join(
                f"{cue.speaker}逐字说“{cue.text}”" for cue in dialogue_cues
            )
            audio_expectation = (
                f"对白：{exact_lines}。实际音频必须完整、顺序和说话人正确；"
                "说话应与输入中已有的镜头表演同步开始，不得增加独立无声开场；"
                "所有非说话者始终闭嘴，说完后嘴部闭合并短暂稳定。"
            )
        else:
            audio_expectation = "静默镜头：所有人物始终闭嘴，不得出现对白或旁白。"
        action = shot.video_prompt
        prop_expectation = ""
        if shot.prop_ids and is_prop_transfer_action(action):
            prop_expectation = (
                "输入中已有的道具交接应呈现清楚的接触、重量承接、释放与分离；"
                "不得增加未描述的道具行为，道具不得脱离接触自行运动，手指不得融合或穿插。"
            )
        locomotion_expectation = ""
        if is_grounded_locomotion_action(action):
            locomotion_expectation = (
                "输入中已有的位移动作应保持可信的平衡、支撑和重量转移，次级运动自然跟随；"
                "不得增加未描述的位移动作，也不得出现滑行或失重感。"
            )
        silent_pacing_expectation = ""
        if not dialogue_cues:
            silent_pacing_expectation = (
                "记录输入中已有主动作的完成时间、已有后续动作的结束时间和纯停留长度；"
                "不得把动作拖满全片，也不得过早完成后长时间空等；"
                "运镜随已有动作落点结束，结尾短暂稳定，不增加新的表演动作。"
            )
        return (
            f"镜头提示词：{shot.video_prompt}。"
            f"{audio_expectation}{prop_expectation}{locomotion_expectation}{silent_pacing_expectation}"
            "动作自然连贯、节奏稳定，角色身份和服饰持续一致，肢体结构正确；"
            "画面不得出现字幕、logo、水印、假文字或无关主体。"
            "不得把输入中没有的姿态、视线、表情、动作、道具行为或镜头设计作为通过条件。"
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
            self.repo.append_audit_rejection(
                project_dir,
                node_name=self.name,
                asset_id=shot.video_asset_id or shot.shot_id,
                attempt=attempt,
                issues=decision.issues,
                rationale=str(decision.rationale or "").strip(),
                current_prompt=shot.final_video_prompt or shot.video_prompt,
                revised_prompt=decision.revised_prompt,
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
