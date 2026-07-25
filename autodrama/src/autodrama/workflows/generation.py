from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from autodrama.core.schemas import (
    DynamicAssetSolidificationOutput,
    ProjectState,
    ShotDialogueAudioGenerationOutput,
    ShotManifestEpisodeOutput,
    ShotManifestItem,
    ShotVideoGenerationOutput,
)
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.providers.base import AssetRef
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
from autodrama.workflows.generation_tasks import load_generation_tasks, save_generation_tasks
from autodrama.workflows.nodes import GENERATION_NODE_NAMES, build_generation_episode_nodes
from autodrama.workflows.selection import sort_episode_keys_in_story_order

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

    def _shot_video_inputs(
        self,
        project_dir: Path,
        shot: ShotManifestItem,
        *,
        provider=None,
    ) -> dict[str, object]:
        items = sorted(list(shot.video_inputs or []), key=lambda item: int(item.order or 0))
        normalized: list[dict[str, object]] = []
        missing_required: list[dict[str, object]] = []
        order_errors: list[str] = []
        kling_subject_mode = bool(
            getattr(provider, "supports_kling_omni_placeholders", False)
        )
        expected_order = {
            "shot_keyframe": 0,
            "shot_last_frame": 1,
            "roleboard": 2,
            "layout": 3,
            "prop": 4,
        }
        last_order = -1
        for expected_index, item in enumerate(items, start=1):
            asset_type = str(item.asset_type or "")
            if item.slot != f"image_{expected_index}":
                order_errors.append(f"{item.slot}: expected image_{expected_index}")
            rank = expected_order.get(asset_type, 99)
            if rank < last_order:
                order_errors.append(f"{item.slot}: {asset_type} appears after a later input type")
            last_order = max(last_order, rank)
            if expected_index == 1 and asset_type != "shot_keyframe":
                order_errors.append("shot manifests require image_1 to be the shot_keyframe")
            if asset_type not in expected_order:
                order_errors.append(f"{item.slot}: {asset_type} is not a supported shot video input")
            if kling_subject_mode and asset_type == "roleboard":
                order_errors.append(
                    f"{item.slot}: Kling characters must use subject elements, not roleboard images"
                )
            path_exists = bool(item.asset_path and self._path_exists(project_dir, item.asset_path))
            has_url = bool(str(item.asset_url or "").strip())
            row = {
                "slot": item.slot,
                "id": item.asset_id,
                "type": item.type,
                "asset_type": asset_type,
                "label": item.label,
                "path": item.asset_path,
                "url": item.asset_url,
                "required": item.required,
                "path_exists": path_exists,
                "metadata": item.metadata,
            }
            normalized.append({key: value for key, value in row.items() if value not in (None, "", {})})
            if item.required and not (path_exists or has_url):
                missing_required.append(row)

        max_images = int(getattr(provider, "max_reference_images", 99) or 99)
        binding = getattr(provider, "model_binding", None)
        if binding is not None:
            params = getattr(binding, "params", {}) or {}
            if isinstance(params, dict) and params.get("max_reference_images") is not None:
                max_images = int(params.get("max_reference_images") or max_images)
            spec = getattr(binding, "spec", None)
            limits = getattr(spec, "limits", {}) if spec is not None else {}
            if isinstance(limits, dict) and limits.get("max_reference_images") is not None:
                max_images = min(max_images, int(limits.get("max_reference_images") or max_images))
        omitted_inputs: list[dict[str, object]] = []
        elements_consume_image_limit = bool(
            binding is not None
            and getattr(getattr(binding, "spec", None), "limits", {}).get(
                "reference_elements_consume_image_limit",
                False,
            )
        )
        reserved_element_count = len(shot.role_ids) if kling_subject_mode and elements_consume_image_limit else 0
        allowed_input_count = max_images - reserved_element_count
        if len(normalized) > allowed_input_count:
            required_image_count = 1
            if allowed_input_count < required_image_count:
                order_errors.append(f"{shot.shot_id} requires a shot keyframe image reference")
            else:
                omitted_inputs = normalized[allowed_input_count:]
                normalized = normalized[:allowed_input_count]
                included_slots = {str(row.get("slot") or "") for row in normalized}
                missing_required = [
                    row
                    for row in missing_required
                    if str(row.get("slot") or "") in included_slots
                ]
        if missing_required or order_errors:
            raise ValueError(
                "shot_video_generation input validation failed: "
                + json.dumps(
                    {
                        "shot_id": shot.shot_id,
                        "missing_required_inputs": missing_required,
                        "order_errors": order_errors,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        episode_key = str(shot.shot_id or "").rsplit("_clip_", 1)[0]
        return {
            "contract": "shot_keyframe_with_subject_elements_v1",
            "final_video_prompt_source": "shot_manifest_generation",
            "episode_key": episode_key,
            "shot_id": shot.shot_id,
            "clip_id": shot.clip_id,
            "limits": {
                "max_reference_images": max_images,
                "reserved_reference_elements": reserved_element_count,
                "available_reference_images": allowed_input_count,
            },
            "inputs": normalized,
            "omitted_inputs": omitted_inputs,
        }

    @staticmethod
    def _asset_refs_from_shot_video_inputs(project_dir: Path, video_inputs: dict[str, object]) -> list:
        refs: list[AssetRef] = []
        rows = video_inputs.get("inputs") if isinstance(video_inputs, dict) else []
        if not isinstance(rows, list):
            return refs
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = row.get("path")
            url = row.get("url")
            refs.append(
                AssetRef(
                    id=str(row.get("id") or row.get("slot") or ""),
                    type="image",
                    path=str(project_dir / path) if isinstance(path, str) and path else None,
                    url=str(url) if isinstance(url, str) and url else None,
                    metadata={
                        "asset_type": row.get("asset_type"),
                        "slot": row.get("slot"),
                        "label": row.get("label"),
                        **(row.get("metadata") if isinstance(row.get("metadata"), dict) else {}),
                    },
                )
            )
        return refs

    @staticmethod
    def _kling_dialogue_role_names(shot: ShotManifestItem) -> set[str]:
        names: set[str] = set()
        for line in shot.dialogue:
            text = str(line or "").strip()
            for separator in ("：", ":"):
                if separator in text:
                    name = text.split(separator, 1)[0].strip()
                    if name:
                        names.add(name.casefold())
                    break
        return names

    @staticmethod
    def _shot_role_appearance(role, shot: ShotManifestItem):
        requested = set(shot.role_appearance_ids or [])
        for appearance in role.appearances.values():
            if appearance.id in requested:
                return appearance
        for appearance in role.appearances.values():
            if appearance.asset_role == "base":
                return appearance
        return next(iter(role.appearances.values()), None)

    def _kling_role_element_refs(
        self,
        state: ProjectState,
        shot: ShotManifestItem,
        provider: object,
    ) -> list[AssetRef]:
        if not bool(getattr(provider, "supports_kling_omni_placeholders", False)):
            return []
        refs: list[AssetRef] = []
        dialogue_names = self._kling_dialogue_role_names(shot)
        for role_index, role_id in enumerate(shot.role_ids, start=1):
            role = state.roles.get(role_id)
            if role is None:
                raise ValueError(f"{shot.shot_id} references unknown role_id {role_id}")
            appearance = self._shot_role_appearance(role, shot)
            if appearance is None or not appearance.subject_element_id:
                raise ValueError(
                    f"{shot.shot_id} requires Kling subject element for {role.name}; "
                    "rerun pregen through role_subject_element_generation"
                )
            speaks = bool(shot.dialogue) and (
                not dialogue_names or role.name.casefold() in dialogue_names or role.id.casefold() in dialogue_names
            )
            if speaks and role.has_dialogue and not role.kling_voice_id:
                raise ValueError(
                    f"{shot.shot_id} has dialogue for {role.name}, but no Kling voice is bound; "
                    "rerun role_kling_voice_generation and role_subject_element_generation"
                )
            refs.append(
                AssetRef(
                    id=appearance.subject_element_id,
                    type="element",
                    metadata={
                        "element_id": appearance.subject_element_id,
                        "kling_content_id": f"role_{role_index}",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "voice_id": role.kling_voice_id,
                    },
                )
            )
        max_elements = int(getattr(provider, "max_reference_elements", 3) or 3)
        if len(refs) > max_elements:
            raise ValueError(
                f"{shot.shot_id} requires {len(refs)} Kling role elements, provider limit is {max_elements}"
            )
        return refs

    def _shot_video_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: ShotManifestItem,
        provider: object,
        video_inputs: dict[str, object],
    ) -> list[AssetRef]:
        refs = self._asset_refs_from_shot_video_inputs(project_dir, video_inputs)
        refs.extend(self._kling_role_element_refs(state, shot, provider))
        return refs

    def _kling_native_prompt(self, prompt: str, state: ProjectState, shot: ShotManifestItem, provider: object) -> str:
        if not bool(getattr(provider, "supports_kling_omni_placeholders", False)):
            return prompt
        marker = "[AUTODRAMA_KLING_ROLE_BINDINGS]"
        if marker in prompt:
            return prompt
        lines = [marker, "角色主体与音色绑定（必须严格遵守）："]
        for role_index, role_id in enumerate(shot.role_ids, start=1):
            role = state.roles.get(role_id)
            if role is None:
                continue
            voice_note = f"，使用已绑定音色 {role.kling_voice_id}" if role.kling_voice_id else ""
            lines.append(f"- @role_{role_index} 对应角色“{role.name}”{voice_note}。")
        if shot.dialogue:
            lines.append("对白必须按以下顺序和角色归属逐字说出；角色依次说话，不要抢词、串音或互换音色：")
            lines.extend(f"- {str(line).strip()}" for line in shot.dialogue if str(line).strip())
        lines.append("生成与画面动作同步的对白、呼吸、环境声和动作音效；画面中不要出现字幕或对白文字。")
        # Keep role/voice/dialogue constraints before the long cinematic prompt so Kling's
        # documented prompt-length limit cannot truncate the binding block.
        return "\n".join(lines) + f"\n\n{prompt.rstrip()}"

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
        if "shot_dialogue_audio_generation" in target_nodes:
            outputs["shot_dialogue_audio_generation"] = ShotDialogueAudioGenerationOutput(
                generated_dialogue_audios=[],
                skipped_dialogue_lines=[],
            )
        if "shot_video_generation" in target_nodes:
            outputs["shot_video_generation"] = ShotVideoGenerationOutput(generated_videos=[])
        if "dynamic_asset_solidification" in target_nodes:
            outputs["dynamic_asset_solidification"] = DynamicAssetSolidificationOutput(solidified_assets=[])
        return outputs

    @staticmethod
    def _merge_run_output(run_outputs: dict[str, BaseModel], node_name: str, output: BaseModel) -> None:
        current = run_outputs[node_name]
        if node_name == "shot_dialogue_audio_generation":
            if not isinstance(current, ShotDialogueAudioGenerationOutput) or not isinstance(
                output,
                ShotDialogueAudioGenerationOutput,
            ):
                raise TypeError("shot_dialogue_audio_generation output type mismatch")
            current.generated_dialogue_audios.extend(output.generated_dialogue_audios)
            current.skipped_dialogue_lines.extend(output.skipped_dialogue_lines)
        elif node_name == "shot_video_generation":
            if not isinstance(current, ShotVideoGenerationOutput) or not isinstance(output, ShotVideoGenerationOutput):
                raise TypeError(f"{node_name} output type mismatch")
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

    def _shot_generation_saved_path(
        self,
        project_dir: Path,
        *,
        node_name: str,
        output: BaseModel,
        episode_key: str,
        shot: ShotManifestItem,
    ) -> str:
        if node_name == "shot_dialogue_audio_generation" and isinstance(output, ShotDialogueAudioGenerationOutput):
            for item in output.generated_dialogue_audios:
                if item.episode_key == episode_key and item.shot_id == shot.shot_id and item.asset.asset_path:
                    return item.asset.asset_path
        if node_name == "shot_video_generation" and isinstance(output, ShotVideoGenerationOutput):
            for item in output.generated_videos:
                if item.episode_key == episode_key and item.shot_id == shot.shot_id and item.asset_path:
                    return item.asset_path
        if node_name == "dynamic_asset_solidification":
            return self._project_relative(project_dir, self.dynamic_assets.index_path(project_dir))
        return self._project_relative(project_dir, self.layout.node_output_path(project_dir, node_name))

    @staticmethod
    def _log_shot_generation_started(logger, episode_key: str, shot: ShotManifestItem, node_name: str) -> None:
        logger.info(
            "%s shot %d %s started",
            episode_key,
            shot.index,
            node_name,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    @staticmethod
    def _log_shot_generation_finished(
        logger,
        episode_key: str,
        shot: ShotManifestItem,
        node_name: str,
        saved_path: str,
    ) -> None:
        logger.info(
            "%s shot %d %s finished successfully, saved in %s",
            episode_key,
            shot.index,
            node_name,
            saved_path,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    @staticmethod
    def _log_shot_generation_failed(
        logger,
        episode_key: str,
        shot: ShotManifestItem,
        node_name: str,
        exc: Exception,
    ) -> None:
        logger.error(
            "%s shot %d %s failed, %s",
            episode_key,
            shot.index,
            node_name,
            exc,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    def _delete_project_file_if_exists(self, project_dir: Path, path_value: str | None) -> None:
        if not path_value:
            return
        path = Path(path_value)
        if not path.is_absolute():
            path = project_dir / path
        resolved_project_dir = project_dir.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_project_dir)
        except ValueError:
            get_logger().warning("skip deleting dynamic asset outside project: %s", resolved_path)
            return
        if resolved_path.exists() and resolved_path.is_file():
            resolved_path.unlink()

    def _clear_shot_dynamic_asset_fields(self, project_dir: Path, shot: ShotManifestItem) -> None:
        for audio in shot.dialogue_audio_assets:
            self._delete_project_file_if_exists(project_dir, audio.asset_path)
        for bgm in shot.shot_bgm_assets:
            self._delete_project_file_if_exists(project_dir, bgm.asset_path)
        self._delete_project_file_if_exists(project_dir, shot.video_asset_path)
        self._delete_project_file_if_exists(project_dir, shot.video_last_frame_asset_path)

        shot.dialogue_audio_assets = []
        shot.shot_bgm_assets = []
        shot.video_asset_id = None
        shot.video_asset_path = None
        shot.video_provider = None
        shot.video_model = None
        shot.video_task_id = None
        shot.video_task_status = None
        shot.video_request_id = None
        shot.video_last_frame_asset_path = None
        shot.video_usage = {}
        shot.video_raw_response = {}
        shot.solidified_asset_ids = []

    def _clear_selected_shot_dynamic_assets(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: ShotManifestEpisodeOutput,
        shots: list[ShotManifestItem],
    ) -> None:
        selected_shot_ids = {shot.shot_id for shot in shots}
        for shot in episode.shots:
            if shot.shot_id in selected_shot_ids:
                self._clear_shot_dynamic_asset_fields(project_dir, shot)
        self._save_shot_manifest(project_dir, episode)

        existing_assets = self.dynamic_assets.load_index(project_dir)
        remaining_assets = [
            item
            for item in existing_assets
            if not (item.episode_key == episode.episode_key and item.shot_id in selected_shot_ids)
        ]
        if len(remaining_assets) != len(existing_assets):
            self.dynamic_assets.save_index(project_dir, remaining_assets)

        registry = load_generation_tasks(project_dir, project_id=state.project_id)
        task_keys = {
            self._shot_video_task_key(episode.episode_key, shot_id)
            for shot_id in selected_shot_ids
        }
        tasks = registry.get("tasks", [])
        if isinstance(tasks, list):
            remaining_tasks = [
                task
                for task in tasks
                if not (isinstance(task, dict) and task.get("task_key") in task_keys)
            ]
            if len(remaining_tasks) != len(tasks):
                registry["tasks"] = remaining_tasks
                save_generation_tasks(self.repo, project_dir, registry)

    @staticmethod
    def _target_generation_nodes(until: str, only: str | None) -> list[str]:
        if only:
            return [only]
        if until in DEFAULT_GENERATION_NODES:
            return DEFAULT_GENERATION_NODES[: DEFAULT_GENERATION_NODES.index(until) + 1]
        return GENERATION_NODES[: GENERATION_NODES.index(until) + 1]

    async def _run_shot_video_generation_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> ShotVideoGenerationOutput:
        episode = self._load_shot_manifest(project_dir, episode_key)
        if episode.schema_version != 4:
            raise ValueError("shot_video_generation requires the schema_version=4 shot manifest")
        previous = getattr(self, "_active_video_node_name", None)
        self._active_video_node_name = "shot_video_generation"
        try:
            return await self._run_shot_video_generation_for_episode_impl(project_dir, state, episode_key)
        finally:
            if previous is None:
                delattr(self, "_active_video_node_name")
            else:
                self._active_video_node_name = previous

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
    ) -> ProjectState:
        if until not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation stop node: {until}")
        if only is not None and only not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation only node: {only}")
        target_nodes = self._target_generation_nodes(until, only)

        logger = setup_logging(project_dir)
        self.router.set_prompt_audit_project_dir(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        self._hydrate_roles_from_design_files(project_dir, state)
        if only is None and target_nodes and target_nodes[-1] != "shot_dialogue_audio_generation":
            shot_provider = self.router.video("shot", node_name="shot_video_generation")
            if bool(getattr(shot_provider, "supports_kling_omni_placeholders", False)):
                target_nodes = [node for node in target_nodes if node != "shot_dialogue_audio_generation"]
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

        run_outputs = self._empty_run_outputs(target_nodes)
        logger.info(
            "workflow=generation project_id=%s until=%s only=%s force=%s episodes=%s shots=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys),
            ",".join(shot_selectors or []) or "-",
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
            shot_selectors={
                str(selector).strip().lower().replace("-", "_")
                for selector in shot_selectors or []
                if str(selector).strip()
            },
        )
        self._force_generation = bool(force)
        if shot_selectors:
            self._active_shot_selectors = {
                str(selector).strip().lower().replace("-", "_")
                for selector in shot_selectors
                if str(selector).strip()
            }
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
                    for node_name in target_nodes:
                        with log_context(node_name=node_name, episode_key=episode_key):
                            output = await self._run_generation_node_for_episode(
                                project_dir,
                                state,
                                node_name,
                                episode_key,
                            )
                            self._merge_run_output(run_outputs, node_name, output)
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
