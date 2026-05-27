from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from autodrama.core.schemas import (
    DynamicAssetSolidificationOutput,
    ProjectState,
    RefFrameGenerationOutput,
    ShotVideoGenerationOutput,
    StoryboardEpisodeOutput,
    StoryboardGenerationOutput,
    StoryboardShot,
)
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.dynamic_asset_repo import DynamicAssetRepository
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.delegation import PregenWorkflowDelegateMixin
from autodrama.workflows.generation_checklist import (
    selected_episode_keys_from_checklist,
    update_checklist_from_state,
)
from autodrama.workflows.context import WorkflowRunContext
from autodrama.workflows.dynamic_assets import DynamicAssetNodeMixin
from autodrama.workflows.nodes import GENERATION_NODE_NAMES, build_generation_episode_nodes
from autodrama.workflows.selection import sort_episode_keys_in_story_order
from autodrama.workflows.storyboard_history import update_storyboard_history_from_episode

GENERATION_NODES = GENERATION_NODE_NAMES

DEFAULT_GENERATION_NODES = list(GENERATION_NODE_NAMES)


class GenerationWorkflow(DynamicAssetNodeMixin, PregenWorkflowDelegateMixin):
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self._init_pregen_delegate(repo=repo, router=router, prompts=prompts)
        self.dynamic_assets = DynamicAssetRepository(self.repo, self.layout)
        self.generation_episode_nodes = build_generation_episode_nodes(self)

    @staticmethod
    def _cg_character_safety_prompt() -> str:
        return (
            "人物形象安全风格要求: 保持项目整体电影级写实CG画面语言，但人类/类人角色必须是高质量风格化CG动漫角色，"
            "不是照片级真人肖像；脸部、皮肤和毛发要有CG建模与动画电影材质感，避免真实摄影人像、真实皮肤毛孔、"
            "汗渍、微血管、皮肤斑点、真实人脸扫描、明星脸或身份证照感。角色识别依靠风格化脸型、眼型、发型、"
            "服装剪裁、配饰、色彩和气质。"
        )

    @staticmethod
    def _ref_frame_only_video_prompt() -> str:
        return (
            "Seedance ref_frame_only 参考图处理要求: 本片段只使用镜头参考帧作为视觉锚点。"
            "请尽可能去掉参考帧中的真人肖像特征，保留并强化动漫化CG角色特征：五官更干净概括、皮肤更平滑、"
            "毛孔/汗渍/微血管/皮肤斑点等真实人脸细节弱化或消除，眼型、眉形、发型、服装轮廓和配饰保持稳定；"
            "人物应呈现高质量CG动漫角色而非现实人物、明星脸、证件照或真实摄影人像。"
            "场景、构图、光影、动作节奏、服装材质和空间关系仍然遵守参考帧，不要改变剧情主体。"
        )

    @staticmethod
    def _shot_spatial_continuity_prompt(shot: StoryboardShot) -> str:
        if not (shot.physical_space_note or shot.spatial_structure_summary or shot.spatial_constraints):
            return ""
        spatial_parts = [
            "最高优先级空间连续性要求:",
        ]
        if shot.physical_space_note:
            spatial_parts.append(f"空间备注: {shot.physical_space_note}")
        if shot.spatial_continuity_mode:
            spatial_parts.append(f"连续性模式: {shot.spatial_continuity_mode}")
        if shot.spatial_reference_shot_ids:
            spatial_parts.append("参考帧来源 shot: " + "、".join(shot.spatial_reference_shot_ids))
        if shot.spatial_structure_summary:
            spatial_parts.append("稳定空间结构: " + shot.spatial_structure_summary)
        if shot.spatial_constraints:
            spatial_parts.append("必须遵守: " + "；".join(shot.spatial_constraints))
        if shot.spatial_movement_allowed:
            reason = f"，原因: {shot.spatial_movement_reason}" if shot.spatial_movement_reason else ""
            spatial_parts.append(
                f"当前剧本只允许理由中点名的人物或道具发生合理运动{reason}。"
                "这不代表摄影机可以越轴，也不代表整套空间拓扑可以镜像反转。"
            )
        else:
            spatial_parts.append(
                "如果当前剧本没有明确写人物移动、绕行或换位，人物与关键物体的左右、前后、远近关系不能无故反转。"
            )
        spatial_parts.append(
            "传入的历史参考帧只用于锁定物理空间拓扑，不要求逐像素复制背景。"
            "背景人群保持大致一致的站位区域、密度、朝向和围观/队列结构；"
            "可以自然变化个体姿态和面孔，但不要把人群带突然移到另一侧或前后景关系完全改变。"
        )
        spatial_parts.append(
            "如果下方主体画面描述中的画面左侧/右侧、前景/后景、人物边缘位置与历史参考帧或本空间要求冲突，"
            "以历史参考帧和本空间要求为准；可以调整构图和景别来表现当前剧情，但不要镜像、换轴或把人物/石柱/传送阵/人群带反到另一侧。"
        )
        return "\n".join(spatial_parts)

    def _shot_ref_frame_prompt(self, state: ProjectState, episode: StoryboardEpisodeOutput, shot: StoryboardShot) -> str:
        layout = state.layouts.get(shot.layout_id)
        role_lines = []
        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role:
                role_lines.append(f"{role.name}: {role.intro}")
        appearance_lines = []
        appearance_ids = list(shot.role_appearance_ids)
        seen_appearance_ids = set(appearance_ids)
        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            base_appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if base_appearance and base_appearance.id not in seen_appearance_ids:
                appearance_ids.append(base_appearance.id)
                seen_appearance_ids.add(base_appearance.id)
        for appearance_id in appearance_ids:
            for role in state.roles.values():
                appearance = next(
                    (
                        item
                        for item in role.appearances.values()
                        if item.id == appearance_id or item.name == appearance_id
                    ),
                    None,
                )
                if appearance:
                    appearance_lines.append(f"{role.name}外观锁定: {appearance.desc}")
                    break
        prop_lines = []
        for prop_id in shot.prop_ids:
            prop = state.props.get(prop_id)
            if prop:
                prop_lines.append(f"{prop.name}: {prop.desc}")

        spatial_prompt = self._shot_spatial_continuity_prompt(shot)
        parts = [
            self._cg_character_safety_prompt(),
            f"剧集: {episode.episode_key}",
            "静态锚点参考帧生成要求:",
        ]
        if spatial_prompt:
            parts.append(spatial_prompt)
            parts.append("主体画面描述如下；若它与上方空间连续性要求冲突，以上方空间连续性要求为准:")
        parts.append(shot.ref_frame_prompt)
        if layout:
            parts.append(f"场景设定: {layout.name} - {layout.desc}")
        if role_lines:
            parts.append(
                "角色资产参考: "
                "仅当主体 prompt 明确该角色在这一帧可见时绘制；否则只作为本片段后续一致性锚点，不要擅自加入画面。参考列表: "
                + "；".join(role_lines)
            )
        if appearance_lines:
            parts.append("人物一致性要求: " + "；".join(appearance_lines))
        if prop_lines:
            parts.append(
                "道具资产参考: "
                "仅当主体 prompt 明确该道具在这一帧可见时绘制；否则只作为本片段后续一致性锚点，不要擅自加入画面。参考列表: "
                + "；".join(prop_lines)
            )
        if shot.dialogue:
            parts.append("画面对白气氛: " + " / ".join(shot.dialogue))
        if spatial_prompt:
            parts.append("最终空间冲突处理: 历史参考帧和最高优先级空间连续性要求优先于主体画面描述中的构图左右词。")
        parts.append(
            "生成单帧锚点剧照，用于锁定本片段的静态资产表现；它不是视频首帧。"
            "不要添加字幕、水印、文字标识或片段编号。"
        )
        return "\n".join(item for item in parts if item)

    def _shot_video_prompt(
        self,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        provider=None,
    ) -> str:
        body = shot.video_prompt.strip()
        parts: list[str] = [self._cg_character_safety_prompt()]
        if self._video_reference_mode(provider) in {"ref_frame_only", "ref_frame"}:
            parts.append(self._ref_frame_only_video_prompt())
        if shot.start_frame_source == "previous_shot_last_frame":
            lead = (
                "本片段按硬切进入当前画面；图片1是上一段视频尾帧，仅用于承接上一段末尾的人物姿态、"
                "空间方向、道具位置、能量位置和环境粒子，再从该状态继续本片段动作。"
            )
            if shot.start_frame_inheritance_reason:
                lead = f"{lead} 延续原因是{shot.start_frame_inheritance_reason.rstrip('。')}。"
            for prefix in ("首帧承接上一段视频尾帧，", "首帧为上一段视频尾帧，", "首帧为图片1，"):
                if body.startswith(prefix):
                    body = body.removeprefix(prefix).lstrip()
                    break
            parts.append(lead)
        else:
            for prefix in (
                "首帧为参考帧，",
                "首帧为本片段参考帧，",
                "首帧为图片1，",
                "首帧为锚点参考帧，",
            ):
                if body.startswith(prefix):
                    body = body.removeprefix(prefix).lstrip()
                    break
            parts.append(
                "本片段按硬切进入当前画面。参考图仅作为静态锚点，保持当前片段中人物外观、服装、"
                "道具造型和场景表现一致；参考视频仅作为动态锚点，保持角色动态气质、动作节奏和动态特效表现一致；"
                "参考音频或对白音频仅用于锁定角色音色、语气、口型节奏和对白情绪。"
                "不要把任何参考素材当作本片段首帧或尾帧，不要逐帧复刻参考素材。"
            )
        parts.append(body)
        parts.append(
            "片段首尾只允许硬切；任何 J-Cut 或 L-Cut 只能发生在本片段内部中段，"
            "不要让声音提前进入本片段之前，也不要让声音拖尾到下一片段。"
        )
        parts.append(
            "如果背景中存在人群或群众，不要让他们静止不动；让他们进行符合场景逻辑、"
            "情绪氛围和空间关系的自然移动、避让、聚散或反应，但不要抢占主体动作。"
        )
        parts.append("全片不要出现任何字幕、标志、logo、水印、文字标识、片段编号、可读文字或无关商标。")
        return " ".join(item for item in parts if item)

    async def _run_generation_node_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        node_name: str,
        episode_key: str,
    ) -> BaseModel:
        node_by_name = {node.name: node for node in self.generation_episode_nodes}
        return await node_by_name[node_name].run(project_dir, state, episode_key)

    @staticmethod
    def _empty_run_outputs(target_nodes: list[str]) -> dict[str, BaseModel]:
        outputs: dict[str, BaseModel] = {}
        if "storyboard_generation" in target_nodes:
            outputs["storyboard_generation"] = StoryboardGenerationOutput(generated_episodes=[])
        if "ref_frame_generation" in target_nodes:
            outputs["ref_frame_generation"] = RefFrameGenerationOutput(generated_ref_frames=[])
        if "shot_video_generation" in target_nodes:
            outputs["shot_video_generation"] = ShotVideoGenerationOutput(generated_videos=[])
        if "dynamic_asset_solidification" in target_nodes:
            outputs["dynamic_asset_solidification"] = DynamicAssetSolidificationOutput(solidified_assets=[])
        return outputs

    @staticmethod
    def _merge_run_output(run_outputs: dict[str, BaseModel], node_name: str, output: BaseModel) -> None:
        current = run_outputs[node_name]
        if node_name == "storyboard_generation":
            if not isinstance(current, StoryboardGenerationOutput) or not isinstance(output, StoryboardGenerationOutput):
                raise TypeError("storyboard_generation output type mismatch")
            current.generated_episodes.extend(output.generated_episodes)
        elif node_name == "ref_frame_generation":
            if not isinstance(current, RefFrameGenerationOutput) or not isinstance(output, RefFrameGenerationOutput):
                raise TypeError("ref_frame_generation output type mismatch")
            current.generated_ref_frames.extend(output.generated_ref_frames)
        elif node_name == "shot_video_generation":
            if not isinstance(current, ShotVideoGenerationOutput) or not isinstance(output, ShotVideoGenerationOutput):
                raise TypeError("shot_video_generation output type mismatch")
            current.generated_videos.extend(output.generated_videos)
        elif node_name == "dynamic_asset_solidification":
            if not isinstance(current, DynamicAssetSolidificationOutput) or not isinstance(
                output, DynamicAssetSolidificationOutput
            ):
                raise TypeError("dynamic_asset_solidification output type mismatch")
            current.solidified_assets.extend(output.solidified_assets)
        else:
            raise ValueError(f"Unsupported generation node output merge: {node_name}")

    def _save_run_outputs(self, project_dir: Path, run_outputs: dict[str, BaseModel]) -> None:
        for node_name, output in run_outputs.items():
            self.repo.save_node_output(project_dir, node_name, output)

    @staticmethod
    def _target_generation_nodes(until: str, only: str | None) -> list[str]:
        if only:
            return [only]
        if until in DEFAULT_GENERATION_NODES:
            return DEFAULT_GENERATION_NODES[: DEFAULT_GENERATION_NODES.index(until) + 1]
        return GENERATION_NODES[: GENERATION_NODES.index(until) + 1]

    def _sort_episode_keys_in_story_order(self, state: ProjectState, episode_keys: list[str]) -> list[str]:
        return sort_episode_keys_in_story_order(state, episode_keys)

    def _active_episode_keys_in_order(self, state: ProjectState) -> list[str]:
        context = getattr(self, "_run_context", None)
        if context is not None and context.selected_episode_keys is not None:
            selected = context.selected_episode_key_set
            return [episode_key for episode_key in self._expected_episode_keys(state) if episode_key in selected]
        active_episode_keys = getattr(self, "_active_episode_keys", None)
        expected = self._expected_episode_keys(state)
        if active_episode_keys is None:
            return expected
        return [episode_key for episode_key in expected if episode_key in active_episode_keys]

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "dynamic_asset_solidification",
        force: bool = False,
        episode_keys: list[str] | None = None,
        only: str | None = None,
        shot_selectors: list[str] | None = None,
        max_shots: int | None = None,
    ) -> ProjectState:
        if until not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation stop node: {until}")
        if only is not None and only not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation only node: {only}")
        try:
            effective_max_shots = int(max_shots if max_shots is not None else self.settings.generation.max_shots)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"generation max_shots must be a positive integer; got {max_shots!r}") from exc
        if effective_max_shots < 1:
            raise ValueError(f"generation max_shots must be >= 1; got {effective_max_shots}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        self._hydrate_roles_from_design_files(project_dir, state)
        selected_episode_keys, checklist = selected_episode_keys_from_checklist(
            self.repo,
            project_dir,
            state,
            episode_keys=episode_keys,
        )
        if force and not episode_keys:
            selected_episode_keys = self._expected_episode_keys(state)

        if not selected_episode_keys:
            update_checklist_from_state(self.repo, project_dir, state, default_generate=False)
            logger.info("workflow=generation skipped project_id=%s reason=no_selected_episodes", state.project_id)
            return state

        selected_set = set(selected_episode_keys)
        known_episode_keys = {str(item.get("episode_key")) for item in checklist.get("episodes", [])}
        unknown_episode_keys = sorted(selected_set.difference(known_episode_keys))
        if unknown_episode_keys:
            raise ValueError(f"Unknown episode keys: {', '.join(unknown_episode_keys)}")
        selected_episode_keys = self._sort_episode_keys_in_story_order(state, selected_episode_keys)
        selected_set = set(selected_episode_keys)

        target_nodes = self._target_generation_nodes(until, only)
        if shot_selectors and "storyboard_generation" in target_nodes:
            raise ValueError("--shots can only be used with generation nodes after storyboard_generation")
        run_outputs = self._empty_run_outputs(target_nodes)
        logger.info(
            "workflow=generation project_id=%s until=%s only=%s force=%s episodes=%s shots=%s max_shots=%d",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys),
            ",".join(shot_selectors or []) or "-",
            effective_max_shots,
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        previous_active_shot_selectors = getattr(self, "_active_shot_selectors", None)
        previous_force_generation = getattr(self, "_force_generation", None)
        previous_run_context = getattr(self, "_run_context", None)
        self._run_context = WorkflowRunContext(
            workflow_name="generation",
            project_dir=project_dir,
            until=until,
            only=only,
            force=force,
            selected_episode_keys=selected_episode_keys,
            shot_selectors={str(selector).strip().lower() for selector in shot_selectors or [] if str(selector).strip()},
            max_shots=effective_max_shots,
        )
        self._force_generation = bool(force)
        if shot_selectors:
            self._active_shot_selectors = {str(selector).strip().lower() for selector in shot_selectors if str(selector).strip()}
        elif hasattr(self, "_active_shot_selectors"):
            delattr(self, "_active_shot_selectors")
        try:
            for episode_index, episode_key in enumerate(selected_episode_keys, start=1):
                self._active_episode_keys = {episode_key}
                logger.info(
                    "episode %d/%d %s started nodes=%s",
                    episode_index,
                    len(selected_episode_keys),
                    episode_key,
                    ",".join(target_nodes),
                )
                try:
                    for node_index, node_name in enumerate(target_nodes, start=1):
                        with log_context(node_name=node_name, episode_key=episode_key):
                            logger.info(
                                "episode %s node %d/%d %s started",
                                episode_key,
                                node_index,
                                len(target_nodes),
                                node_name,
                            )
                            output = await self._run_generation_node_for_episode(
                                project_dir,
                                state,
                                node_name,
                                episode_key,
                            )
                            self._merge_run_output(run_outputs, node_name, output)
                            if node_name == "storyboard_generation":
                                update_storyboard_history_from_episode(
                                    self.repo,
                                    project_dir,
                                    state,
                                    self._load_storyboard_episode(project_dir, episode_key),
                                )
                            if node_name == "dynamic_asset_solidification":
                                if not isinstance(output, DynamicAssetSolidificationOutput):
                                    raise TypeError("dynamic_asset_solidification output type mismatch")
                                output = cast(DynamicAssetSolidificationOutput, output)
                                self.dynamic_assets.merge_episode_assets(
                                    project_dir,
                                    episode_key,
                                    output.solidified_assets,
                                )
                            state.mark_completed(node_name)
                            self.repo.save_state(project_dir, state)
                            self._save_run_outputs(project_dir, run_outputs)
                            logger.info(
                                "episode %s node %d/%d %s completed current_node=%s",
                                episode_key,
                                node_index,
                                len(target_nodes),
                                node_name,
                                state.current_node,
                            )
                except Exception:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        failed_episode_keys={episode_key},
                    )
                    logger.exception("episode %s failed", episode_key)
                    raise
                if target_nodes[-1] == GENERATION_NODES[-1]:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        processed_episode_keys={episode_key},
                    )
                logger.info("episode %d/%d %s completed", episode_index, len(selected_episode_keys), episode_key)
        finally:
            if previous_active_episode_keys is None:
                if hasattr(self, "_active_episode_keys"):
                    delattr(self, "_active_episode_keys")
            else:
                self._active_episode_keys = previous_active_episode_keys
            if previous_active_shot_selectors is None:
                if hasattr(self, "_active_shot_selectors"):
                    delattr(self, "_active_shot_selectors")
            else:
                self._active_shot_selectors = previous_active_shot_selectors
            if previous_force_generation is None:
                if hasattr(self, "_force_generation"):
                    delattr(self, "_force_generation")
            else:
                self._force_generation = previous_force_generation
            if previous_run_context is None:
                if hasattr(self, "_run_context"):
                    delattr(self, "_run_context")
            else:
                self._run_context = previous_run_context

        processed_episode_keys = selected_set if target_nodes[-1] == GENERATION_NODES[-1] else None
        update_checklist_from_state(
            self.repo,
            project_dir,
            state,
            default_generate=False,
            processed_episode_keys=processed_episode_keys,
        )
        get_logger().info(
            "workflow=generation completed project_id=%s current_node=%s episodes=%s",
            state.project_id,
            state.current_node,
            ",".join(selected_episode_keys),
        )
        return state
