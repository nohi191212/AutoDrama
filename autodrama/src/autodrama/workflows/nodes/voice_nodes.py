from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from autodrama.core.schemas import (
    ProjectState,
    Role,
    RoleAudio,
    RoleVoiceGenerationItem,
    RoleVoiceGenerationOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.media_store import MediaStore
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

VOICE_NODE_NAMES = [
    "role_voice_generation",
]


class VoiceNodeBase:
    def __init__(
        self,
        *,
        workflow: Any,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        script_service: ScriptService,
        role_service: RoleService,
        script_contents: ScriptContentRepository,
        media_store: MediaStore,
        logger: Any,
    ) -> None:
        self.workflow = workflow
        self.repo = repo
        self.layout = layout
        self.router = router
        self.script_service = script_service
        self.role_service = role_service
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

    def write_preview_audio(
        self,
        project_dir: Path,
        *,
        audio: RoleAudio,
        data: str | None,
        response_format: str | None,
    ) -> str | None:
        return self.media_store.write_preview_audio(
            project_dir,
            audio_id=audio.id,
            data=data,
            response_format=response_format,
        )

    def absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return self.layout.absolute_project_path(project_dir, relative_path)

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

    @staticmethod
    def role_matches_active_episode_keys(role: Role, active_episode_keys: list[str], *, label: str) -> bool:
        if not active_episode_keys:
            return True
        role_episode_keys = [str(key).strip() for key in role.episode_keys if str(key).strip()]
        if not role_episode_keys:
            raise ValueError(f"{label} cannot scope role {role.name}: missing episode_keys")
        return bool(set(role_episode_keys).intersection(active_episode_keys))

    def target_roles(self, state: ProjectState, active_episode_keys: list[str], *, label: str) -> list[Role]:
        return [
            role
            for role in state.roles.values()
            if self.role_matches_active_episode_keys(role, active_episode_keys, label=label)
        ]


class RoleVoiceDesignNode(VoiceNodeBase):
    name = "role_voice_design"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        speech_provider = None
        available_voices: list[dict[str, Any]] = []
        try:
            speech_provider = self.router.audio("speech")
            available_voices = self.workflow._available_speakers_for_prompt(speech_provider)
        except Exception as exc:
            self.logger.warning("node=role_voice_design could not load speech voice catalog: %s", exc)
        self.logger.info(
            "node=role_voice_design provider=%s model=%s available_voices=%d",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            len(available_voices),
        )
        output = await self.role_service.role_voice_design(
            state,
            provider,
            episode_stories=self.episode_stories(project_dir, state),
            available_voices=available_voices,
        )
        self.workflow._apply_role_voice_design_output(state, output, speech_provider=speech_provider)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class RoleVoiceGenerationNode(VoiceNodeBase):
    name = "role_voice_generation"

    @staticmethod
    def voice_preferred_name(state: ProjectState, audio: RoleAudio) -> str:
        digest = hashlib.sha1(f"{state.project_id}:{audio.id}".encode("utf-8")).hexdigest()
        return f"ad_{digest[:13]}"

    @staticmethod
    def preview_text(role: Role, audio: RoleAudio) -> str:
        return (audio.sample_text or f"我是{role.name}。")[:1024]

    @staticmethod
    def voice_prompt(role: Role, audio: RoleAudio) -> str:
        return audio.desc or f"{role.name}的{audio.emotion}音色。{role.intro}"

    @staticmethod
    def synthesis_text(provider, audio: RoleAudio, preview_text: str) -> str:
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

    @staticmethod
    def copy_role_voice_to_audio(role: Role, audio: RoleAudio) -> None:
        if not role.voice_type:
            return
        audio.voice_name = role.voice_name
        audio.voice_type = role.voice_type
        audio.voice_resource_id = role.voice_resource_id
        audio.voice_model_family = role.voice_model_family

    async def generate_designed_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        preview_text = self.preview_text(role, audio)
        voice_prompt = self.voice_prompt(role, audio)
        result = await provider.create_voice(
            voice_prompt=voice_prompt,
            preview_text=preview_text,
            preferred_name=self.voice_preferred_name(state, audio),
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "design",
            },
        )
        preview_audio_path = self.write_preview_audio(
            project_dir,
            audio=audio,
            data=result.preview_audio_data,
            response_format=result.preview_audio_format,
        )
        audio.asset_id = result.voice
        audio.asset_path = preview_audio_path
        audio.generation_status = "generated" if preview_audio_path or result.voice else "pending"
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="design",
            voice=result.voice,
            voice_prompt=voice_prompt,
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

    async def generate_cloned_voice(
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

        preview_text = self.preview_text(role, audio)
        voice_prompt = self.voice_prompt(role, audio)
        clone_result = await provider.clone_voice_from_audio(
            source_audio_path=self.absolute_project_path(project_dir, normal_audio.asset_path),
            preferred_name=self.voice_preferred_name(state, audio),
            metadata={
                "node_name": self.name,
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
                "node_name": self.name,
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
        preview_audio_path = self.write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        audio.asset_id = clone_result.voice
        audio.asset_path = preview_audio_path
        audio.generation_status = "generated" if preview_audio_path or clone_result.voice else "pending"
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="clone",
            voice=clone_result.voice,
            source_audio_id=normal_audio.id,
            source_audio_path=normal_audio.asset_path,
            voice_prompt=voice_prompt,
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

    async def generate_reused_voice(
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

        preview_text = self.preview_text(role, audio)
        voice_prompt = self.voice_prompt(role, audio)
        synthesis_result = await provider.synthesize_speech(
            voice=normal_audio.asset_id,
            text=self.synthesis_text(provider, audio, preview_text),
            metadata={
                "node_name": self.name,
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
        preview_audio_path = self.write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        audio.asset_id = normal_audio.asset_id
        audio.asset_path = preview_audio_path
        audio.generation_status = "generated" if preview_audio_path or normal_audio.asset_id else "pending"
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="reuse",
            voice=normal_audio.asset_id,
            source_audio_id=normal_audio.id,
            source_audio_path=normal_audio.asset_path,
            voice_prompt=voice_prompt,
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

    @staticmethod
    def role_synthesis_voice(provider, role: Role) -> str:
        if role.voice_type:
            return role.voice_type
        resolver = getattr(provider, "resolve_role_voice", None)
        if callable(resolver):
            return str(
                resolver(
                    role_id=role.id,
                    role_name=role.name,
                    role_intro=role.intro,
                    role_voice_summary=role.voice_summary,
                    role_personality=role.personality,
                )
            )
        normal_audio = role.audio.get("normal")
        if normal_audio and normal_audio.asset_id:
            return normal_audio.asset_id
        raise ValueError(f"Cannot synthesize role voice for {role.name}: provider cannot resolve a voice")

    @staticmethod
    def role_synthesis_resource_id(provider, role: Role, voice: str) -> str | None:
        if role.voice_resource_id:
            return role.voice_resource_id
        resolver = getattr(provider, "resolve_voice_resource_id", None)
        if callable(resolver):
            resource_id = resolver(voice)
            if resource_id:
                return str(resource_id)
        resource_id = getattr(provider, "resource_id", None) or getattr(provider, "model", None)
        return str(resource_id) if resource_id else None

    @staticmethod
    def role_emotion_synthesis_plan(provider, audio: RoleAudio) -> tuple[str | None, dict[str, Any]]:
        resolver = getattr(provider, "resolve_emotion_plan", None)
        plan = resolver(audio.emotion) if callable(resolver) else {}
        if not isinstance(plan, dict):
            plan = {}

        instruction_value = plan.get("instruction")
        instruction = str(instruction_value).strip() if instruction_value is not None else None
        if instruction == "":
            instruction = None

        params_resolver = getattr(provider, "emotion_params_from_plan", None)
        if callable(params_resolver):
            params = params_resolver(plan)
        else:
            params = {key: value for key, value in plan.items() if key != "instruction"}
        return instruction, dict(params)

    async def generate_synthesized_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        voice: str,
        voice_resource_id: str | None = None,
    ) -> RoleVoiceGenerationItem:
        preview_text = self.preview_text(role, audio)
        voice_prompt = self.voice_prompt(role, audio)
        emotion_instruction, emotion_params = self.role_emotion_synthesis_plan(provider, audio)
        target_model = voice_resource_id or getattr(provider, "model", None)
        synthesis_result = await provider.synthesize_speech(
            voice=voice,
            text=preview_text,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "synthesis",
                "voice_prompt": voice_prompt,
                "emotion_instruction": emotion_instruction,
                "emotion_params": emotion_params,
                "resource_id": voice_resource_id,
                "target_model": target_model,
            },
        )
        preview_audio_path = self.write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        resolved_voice = synthesis_result.voice or voice
        audio.asset_id = resolved_voice
        audio.voice_name = role.voice_name
        audio.voice_type = resolved_voice
        audio.voice_resource_id = voice_resource_id
        audio.voice_model_family = role.voice_model_family
        audio.asset_path = preview_audio_path
        audio.emotion_instruction = emotion_instruction
        audio.emotion_params = emotion_params
        audio.generation_status = "generated" if preview_audio_path or resolved_voice else "pending"
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="synthesis",
            voice=resolved_voice,
            voice_name=role.voice_name,
            voice_resource_id=voice_resource_id,
            voice_model_family=role.voice_model_family,
            voice_selection_reason=role.voice_selection_reason,
            voice_prompt=voice_prompt,
            preview_text=preview_text,
            emotion_instruction=emotion_instruction,
            emotion_params=emotion_params,
            preview_audio_path=preview_audio_path,
            provider=synthesis_result.provider,
            model=synthesis_result.model,
            target_model=voice_resource_id or synthesis_result.model,
            sample_rate=synthesis_result.audio_sample_rate,
            response_format=synthesis_result.audio_format,
            request_id=synthesis_result.request_id,
            usage=synthesis_result.usage,
            raw_response=synthesis_result.raw_response,
        )

    async def run_synthesis_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        roles: list[Role] | None = None,
    ) -> ProjectState:
        generated: list[RoleVoiceGenerationItem] = []
        target_roles = roles if roles is not None else list(state.roles.values())
        for role in target_roles:
            if not self.workflow._role_needs_voice(role):
                self.logger.info("role_voice_generation skipped functional role without dialogue: %s", role.name)
                continue
            if role.audio.get("normal") is None:
                raise ValueError(f"Cannot generate role voice for {role.name}: missing normal voice design")
            role_voice = self.role_synthesis_voice(provider, role)
            role_resource_id = self.role_synthesis_resource_id(provider, role, role_voice)
            if not role.voice_type:
                role.voice_type = role_voice
            if role_resource_id and not role.voice_resource_id:
                role.voice_resource_id = role_resource_id
            for audio in role.audio.values():
                self.copy_role_voice_to_audio(role, audio)
                generated.append(
                    await self.generate_synthesized_voice(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        role=role,
                        audio=audio,
                        voice=role_voice,
                        voice_resource_id=role_resource_id,
                    )
                )

        output = RoleVoiceGenerationOutput(generated_voices=generated)
        self.repo.save_node_output(project_dir, self.name, output)
        return state

    async def run_design_clone_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        roles: list[Role] | None = None,
    ) -> ProjectState:
        generated: list[RoleVoiceGenerationItem] = []
        target_roles = roles if roles is not None else list(state.roles.values())
        for role in target_roles:
            if not self.workflow._role_needs_voice(role):
                self.logger.info("role_voice_generation skipped functional role without dialogue: %s", role.name)
                continue
            normal_audio = role.audio.get("normal")
            if normal_audio is None:
                raise ValueError(f"Cannot generate role voice for {role.name}: missing normal voice design")

            generated.append(
                await self.generate_designed_voice(
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
                        await self.generate_reused_voice(
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
                    await self.generate_cloned_voice(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        role=role,
                        audio=audio,
                        normal_audio=normal_audio,
                    )
                )

        output = RoleVoiceGenerationOutput(generated_voices=generated)
        self.repo.save_node_output(project_dir, self.name, output)
        return state

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.audio("speech")
        self.logger.info(
            "node=role_voice_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state, speech_provider=provider)
        self.workflow._repair_role_voice_design_if_needed(project_dir, state, speech_provider=provider)
        active_episode_keys = self.active_episode_keys(state)
        roles = self.target_roles(state, active_episode_keys, label=self.name)
        if active_episode_keys:
            self.logger.info(
                "node=role_voice_generation episode-scoped rerun episodes=%s target_roles=%s",
                ",".join(active_episode_keys),
                ",".join(role.name for role in roles) or "-",
            )

        if getattr(provider, "supports_direct_emotion_synthesis", False):
            return await self.run_synthesis_generation(
                provider=provider,
                project_dir=project_dir,
                state=state,
                roles=roles,
            )

        return await self.run_design_clone_generation(
            provider=provider,
            project_dir=project_dir,
            state=state,
            roles=roles,
        )


def build_voice_node_runners(workflow: Any) -> dict[str, VoiceNodeBase]:
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)
    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "role_service": workflow.role_service,
        "script_contents": script_contents,
        "media_store": workflow.media_store,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        RoleVoiceDesignNode.name: RoleVoiceDesignNode(**deps),
        RoleVoiceGenerationNode.name: RoleVoiceGenerationNode(**deps),
    }


def build_voice_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_voice_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in VOICE_NODE_NAMES
    ]


__all__ = [
    "VOICE_NODE_NAMES",
    "RoleVoiceDesignNode",
    "RoleVoiceGenerationNode",
    "VoiceNodeBase",
    "build_voice_node_runners",
    "build_voice_nodes",
]
