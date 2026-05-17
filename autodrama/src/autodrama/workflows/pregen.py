from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Awaitable, Callable

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import ProjectState, Role, RoleAudio, RoleVoiceGenerationItem, RoleVoiceGenerationOutput
from autodrama.core.visual_style import visual_style_metadata
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore

NodeRunner = Callable[[ProjectState], Awaitable[ProjectState]]


PREGEN_NODES = [
    "script_outline",
    "script_detail",
    "script_polish",
    "role_design",
    "role_voice_design",
    "role_voice_generation",
]


class PregenWorkflow:
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self.repo = repo
        self.router = router
        self.prompts = prompts or PromptStore()
        self.script_service = ScriptService(self.prompts)
        self.role_service = RoleService(self.prompts)

    def _expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

    def _validate_episode_keys(self, label: str, payload: dict[str, str], state: ProjectState) -> None:
        expected_keys = self._expected_episode_keys(state)
        expected = set(expected_keys)
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                f"{label} must contain exactly {', '.join(expected_keys)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    def _validate_script_outline(self, output_episode_count: int, output_duration: int, state: ProjectState) -> None:
        expected_episode_count = self.script_service.episode_count(state)
        expected_duration = self.script_service.episode_duration_seconds(state)
        if output_episode_count != expected_episode_count:
            raise ValueError(
                f"script_outline episode_count must be {expected_episode_count}; got {output_episode_count}"
            )
        if output_duration != expected_duration:
            raise ValueError(
                f"script_outline target_duration_seconds must be {expected_duration}; got {output_duration}"
            )

    def _apply_script_plan_settings(self, state: ProjectState) -> None:
        state.metadata["episode_count"] = self.repo.settings.project.episode_count
        state.metadata["episode_duration_seconds"] = self.repo.settings.project.episode_duration_seconds
        state.metadata.update(visual_style_metadata(self.repo.settings.project.visual_style))

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "role_voice_generation",
        force: bool = False,
    ) -> ProjectState:
        if until not in PREGEN_NODES:
            raise ValueError(f"Unsupported pregen stop node: {until}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        stop_index = PREGEN_NODES.index(until)
        target_nodes = PREGEN_NODES[: stop_index + 1]
        logger.info(
            "workflow=pregen project_id=%s until=%s force=%s completed=%s",
            state.project_id,
            until,
            force,
            ",".join(state.completed_nodes) or "-",
        )
        for index, node_name in enumerate(target_nodes, start=1):
            if not force and node_name in state.completed_nodes:
                logger.info("node %d/%d %s skipped", index, len(target_nodes), node_name)
                continue
            logger.info("node %d/%d %s started", index, len(target_nodes), node_name)
            try:
                state = await getattr(self, f"_run_{node_name}")(project_dir, state)
                state.mark_completed(node_name)
                self.repo.save_state(project_dir, state)
            except Exception:
                logger.exception("node %d/%d %s failed", index, len(target_nodes), node_name)
                raise
            logger.info(
                "node %d/%d %s completed current_node=%s",
                index,
                len(target_nodes),
                node_name,
                state.current_node,
            )
        logger.info("workflow=pregen completed project_id=%s current_node=%s", state.project_id, state.current_node)
        return state

    async def _run_script_outline(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_outline provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_outline(state, provider)
        self._validate_script_outline(output.episode_count, output.target_duration_seconds, state)
        self._validate_episode_keys("script_outline.episode_outlines", output.episode_outlines, state)
        state.script.outline = output.outline
        state.script.episode_outlines = output.episode_outlines
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_outline", output)
        return state

    async def _run_script_detail(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_detail provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_detail(state, provider)
        self._validate_episode_keys("script_detail.detailed_script", output.detailed_script, state)
        state.script.detailed_script = output.detailed_script
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_detail", output)
        return state

    async def _run_script_polish(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_polish provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_polish(state, provider)
        self._validate_episode_keys("script_polish.final_script", output.final_script, state)
        state.script.final_script = output.final_script
        state.script.revision_notes = output.revision_notes
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_polish", output)
        return state

    async def _run_role_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        get_logger().info(
            "node=role_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.role_service.role_design(state, provider)
        roles: dict[str, Role] = {}
        for item in output.roles:
            role_id = normalize_id("role", item.name)
            roles[role_id] = Role(
                id=role_id,
                name=item.name,
                intro=item.intro,
                personality=item.personality,
                aliases=item.aliases,
            )
        state.roles = roles
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_design", output)
        return state

    async def _run_role_voice_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        get_logger().info(
            "node=role_voice_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.role_service.role_voice_design(state, provider)
        roles_by_name = {role.name: role for role in state.roles.values()}

        for item in output.role_voices:
            role = roles_by_name.get(item.role_name)
            if role is None:
                continue
            audio_id = normalize_id(f"{role.id}_audio", str(item.emotion))
            role.audio[str(item.emotion)] = RoleAudio(
                id=audio_id,
                role_id=role.id,
                emotion=str(item.emotion),
                desc=item.desc,
                sample_text=item.sample_text,
            )
            normal_voice = role.audio.get("normal")
            if normal_voice:
                role.voice_summary = normal_voice.desc

        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_voice_design", output)
        return state

    def _voice_preferred_name(self, state: ProjectState, audio: RoleAudio) -> str:
        digest = hashlib.sha1(f"{state.project_id}:{audio.id}".encode("utf-8")).hexdigest()
        return f"ad_{digest[:13]}"

    def _preview_text(self, role: Role, audio: RoleAudio) -> str:
        return (audio.sample_text or f"我是{role.name}。")[:1024]

    def _synthesis_text(self, provider, audio: RoleAudio, preview_text: str) -> str:
        if not getattr(provider, "is_cosyvoice", False):
            return preview_text

        emotion_tags = {
            "angry": "<|ANGRY|>",
            "sad": "<|SAD|>",
            "happy": "<|HAPPY|>",
            "tense": "<|NEUTRAL|><|1.10|>",
            "whisper": "<|CALM|><|0.80|>",
        }
        return f"{emotion_tags.get(audio.emotion, '')}{preview_text}"

    def _write_preview_audio(
        self,
        project_dir: Path,
        *,
        audio: RoleAudio,
        data: str | None,
        response_format: str | None,
    ) -> str | None:
        if not data:
            return None

        extension = (response_format or "wav").lower()
        if extension not in {"wav", "mp3", "pcm", "opus"}:
            extension = "bin"

        output_dir = project_dir / "assets" / "audios" / "role_voices"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{audio.id}.{extension}"
        output_path.write_bytes(base64.b64decode(data))
        return str(output_path.relative_to(project_dir)).replace("\\", "/")

    def _absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return str(project_dir / relative_path)

    async def _generate_designed_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        preview_text = self._preview_text(role, audio)
        result = await provider.create_voice(
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preferred_name=self._voice_preferred_name(state, audio),
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "design",
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=result.preview_audio_data,
            response_format=result.preview_audio_format,
        )
        audio.asset_id = result.voice
        audio.asset_path = preview_audio_path
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="design",
            voice=result.voice,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preview_audio_path=preview_audio_path,
            provider=result.provider,
            model=result.model,
            target_model=result.target_model,
            sample_rate=result.preview_audio_sample_rate,
            response_format=result.preview_audio_format,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )

    async def _generate_cloned_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        normal_audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        if not normal_audio.asset_path:
            raise ValueError(f"Cannot clone {role.name}/{audio.emotion}: normal voice preview audio is missing")

        preview_text = self._preview_text(role, audio)
        clone_result = await provider.clone_voice_from_audio(
            source_audio_path=self._absolute_project_path(project_dir, normal_audio.asset_path),
            preferred_name=self._voice_preferred_name(state, audio),
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "clone",
                "source_audio_id": normal_audio.id,
                "source_audio_path": normal_audio.asset_path,
            },
        )
        synthesis_result = await provider.synthesize_speech(
            voice=clone_result.voice,
            text=preview_text,
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "clone",
                "source_audio_id": normal_audio.id,
                "source_audio_path": normal_audio.asset_path,
                "target_model": clone_result.target_model,
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        audio.asset_id = clone_result.voice
        audio.asset_path = preview_audio_path
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="clone",
            voice=clone_result.voice,
            source_audio_id=normal_audio.id,
            source_audio_path=normal_audio.asset_path,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preview_audio_path=preview_audio_path,
            provider=clone_result.provider,
            model=clone_result.model,
            target_model=clone_result.target_model,
            sample_rate=synthesis_result.audio_sample_rate,
            response_format=synthesis_result.audio_format,
            request_id=clone_result.request_id,
            usage={
                "clone": clone_result.usage,
                "synthesis": synthesis_result.usage,
            },
            raw_response={
                "clone": clone_result.raw_response,
                "synthesis": synthesis_result.raw_response,
            },
        )

    async def _generate_reused_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        normal_audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        if not normal_audio.asset_id:
            raise ValueError(f"Cannot reuse {role.name}/{audio.emotion}: normal voice id is missing")

        preview_text = self._preview_text(role, audio)
        synthesis_result = await provider.synthesize_speech(
            voice=normal_audio.asset_id,
            text=self._synthesis_text(provider, audio, preview_text),
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "reuse",
                "source_audio_id": normal_audio.id,
                "source_audio_path": normal_audio.asset_path,
                "target_model": getattr(provider, "target_model", None),
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        audio.asset_id = normal_audio.asset_id
        audio.asset_path = preview_audio_path
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="reuse",
            voice=normal_audio.asset_id,
            source_audio_id=normal_audio.id,
            source_audio_path=normal_audio.asset_path,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preview_audio_path=preview_audio_path,
            provider=synthesis_result.provider,
            model=synthesis_result.model,
            target_model=synthesis_result.model,
            sample_rate=synthesis_result.audio_sample_rate,
            response_format=synthesis_result.audio_format,
            request_id=synthesis_result.request_id,
            usage=synthesis_result.usage,
            raw_response=synthesis_result.raw_response,
        )

    async def _run_role_voice_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.audio("speech")
        get_logger().info(
            "node=role_voice_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )

        generated: list[RoleVoiceGenerationItem] = []
        for role in state.roles.values():
            normal_audio = role.audio.get("normal")
            if normal_audio is None:
                raise ValueError(f"Cannot generate role voice for {role.name}: missing normal voice design")

            generated.append(
                await self._generate_designed_voice(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    role=role,
                    audio=normal_audio,
                )
            )

            for emotion, audio in role.audio.items():
                if emotion == "normal":
                    continue
                if not getattr(provider, "supports_local_voice_clone", True):
                    generated.append(
                        await self._generate_reused_voice(
                            provider=provider,
                            project_dir=project_dir,
                            state=state,
                            role=role,
                            audio=audio,
                            normal_audio=normal_audio,
                        )
                    )
                    continue
                generated.append(
                    await self._generate_cloned_voice(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        role=role,
                        audio=audio,
                        normal_audio=normal_audio,
                    )
                )

        output = RoleVoiceGenerationOutput(generated_voices=generated)
        self.repo.save_node_output(project_dir, "role_voice_generation", output)
        return state
