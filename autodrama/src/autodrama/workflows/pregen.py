from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    BGM,
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    RoleVoiceDesignOutput,
    RoleVoiceGenerationItem,
    RoleVoiceGenerationOutput,
    StaticAssetGenerationItem,
    StaticAssetGenerationOutput,
    StoryboardGenerationOutput,
)
from autodrama.core.visual_style import visual_style_metadata
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.services.storyboard_service import StoryboardService
from autodrama.utils.prompts import PromptStore

NodeRunner = Callable[[ProjectState], Awaitable[ProjectState]]


PREGEN_NODES = [
    "script_outline",
    "script_detail",
    "script_polish",
    "role_design",
    "role_voice_design",
    "role_voice_generation",
    "role_appearance_design",
    "role_appearance_generation",
    "prop_design",
    "prop_image_generation",
    "script_compress",
    "layout_design",
    "layout_dedupe_review",
    "layout_image_generation",
    "bgm_design",
    "bgm_generation",
    "storyboard_generation",
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
        self.asset_service = AssetService(self.prompts)
        self.storyboard_service = StoryboardService(self.prompts)

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

    @staticmethod
    def _add_role_lookup_key(lookup: dict[str, Role], key: str | None, role: Role) -> None:
        if not key:
            return
        normalized_key = str(key).strip()
        if not normalized_key:
            return
        lookup.setdefault(normalized_key, role)
        lookup.setdefault(normalized_key.casefold(), role)

    def _role_lookup(self, state: ProjectState) -> dict[str, Role]:
        lookup: dict[str, Role] = {}
        for role in state.roles.values():
            self._add_role_lookup_key(lookup, role.id, role)
            self._add_role_lookup_key(lookup, role.name, role)
            self._add_role_lookup_key(lookup, normalize_id("role", role.name), role)
            for alias in role.aliases:
                self._add_role_lookup_key(lookup, alias, role)
                self._add_role_lookup_key(lookup, normalize_id("role", alias), role)
        return lookup

    @staticmethod
    def _resolve_role(lookup: dict[str, Role], role_name: str) -> Role | None:
        key = str(role_name).strip()
        return lookup.get(key) or lookup.get(key.casefold())

    @staticmethod
    def _default_voice_sample_text(role: Role) -> str:
        intro = role.intro.rstrip("。")
        return f"我是{role.name}。{intro}。面对眼前的问题，我会保持冷静，按照自己的判断继续向前。"

    def _ensure_normal_role_audio(self, role: Role) -> None:
        if "normal" in role.audio:
            normal_voice = role.audio["normal"]
            if not normal_voice.sample_text:
                normal_voice.sample_text = self._default_voice_sample_text(role)
            role.voice_summary = normal_voice.desc
            return

        first_audio = next(iter(role.audio.values()), None)
        if first_audio is not None:
            desc = (
                f"{role.name}的常规音色。参考已有{first_audio.emotion}音色的年龄感、性别感和基础音色，"
                "但情绪保持平稳自然，语速适中，咬字清晰，适合作为后续情绪音色克隆的基础音色。"
            )
        else:
            desc = (
                f"{role.name}的常规音色。{role.intro} "
                "声音自然平稳，语速适中，咬字清晰，情绪克制，适合作为后续 TTS 的基础音色。"
            )
        audio_id = normalize_id(f"{role.id}_audio", "normal")
        role.audio["normal"] = RoleAudio(
            id=audio_id,
            role_id=role.id,
            emotion="normal",
            desc=desc,
            sample_text=self._default_voice_sample_text(role),
        )
        role.voice_summary = desc

    def _apply_role_voice_design_output(self, state: ProjectState, output: RoleVoiceDesignOutput) -> None:
        roles_by_key = self._role_lookup(state)
        unmatched_role_names: list[str] = []

        for item in output.role_voices:
            role = self._resolve_role(roles_by_key, item.role_name)
            if role is None:
                unmatched_role_names.append(item.role_name)
                continue
            audio_id = normalize_id(f"{role.id}_audio", str(item.emotion))
            role.audio[str(item.emotion)] = RoleAudio(
                id=audio_id,
                role_id=role.id,
                emotion=str(item.emotion),
                desc=item.desc,
                sample_text=item.sample_text,
            )
        if unmatched_role_names:
            get_logger().warning(
                "node=role_voice_design ignored unmatched role_name values: %s",
                ", ".join(unmatched_role_names),
            )
        for role in state.roles.values():
            self._ensure_normal_role_audio(role)

    def _repair_role_voice_design_if_needed(self, project_dir: Path, state: ProjectState) -> None:
        if all("normal" in role.audio for role in state.roles.values()):
            return

        path = project_dir / "assets" / "json" / "nodes" / "role_voice_design.json"
        if path.exists():
            output = RoleVoiceDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))
            self._apply_role_voice_design_output(state, output)
            return

        for role in state.roles.values():
            self._ensure_normal_role_audio(role)

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
        self._apply_role_voice_design_output(state, output)
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
        extension = {"ogg_opus": "opus"}.get(extension, extension)
        if extension not in {"wav", "mp3", "pcm", "opus", "ogg", "m4a", "aac"}:
            extension = "bin"

        output_dir = project_dir / "assets" / "audios" / "role_voices"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{audio.id}.{extension}"
        output_path.write_bytes(base64.b64decode(data))
        return str(output_path.relative_to(project_dir)).replace("\\", "/")

    def _absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return str(project_dir / relative_path)

    @staticmethod
    def _project_relative(project_dir: Path, path: Path) -> str:
        return str(path.relative_to(project_dir)).replace("\\", "/")

    @staticmethod
    def _image_asset_path(project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return project_dir / "assets" / "images" / asset_type / f"{asset_id}.png"

    @staticmethod
    def _music_asset_path(project_dir: Path, asset_id: str, audio_format: str | None) -> Path:
        extension = (audio_format or "mp3").lower().lstrip(".")
        if extension not in {"mp3", "wav", "m4a", "aac", "ogg"}:
            extension = "mp3"
        return project_dir / "assets" / "audios" / "bgms" / f"{asset_id}.{extension}"

    async def _write_first_generated_image(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.image_data:
            data = result.image_data[0]
            if data.startswith("data:") and ";base64," in data:
                data = data.split(";base64,", 1)[1]
            output_path.write_bytes(base64.b64decode(data))
            return self._project_relative(project_dir, output_path)

        if not result.image_urls:
            raise ValueError("Image generation result has no image data or URL")

        async with httpx.AsyncClient(timeout=self.repo.settings.runtime.request_timeout_seconds) as client:
            response = await client.get(result.image_urls[0])
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated image HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return self._project_relative(project_dir, output_path)

    async def _write_generated_music(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.audio_data:
            data = result.audio_data
            if data.startswith("data:") and ";base64," in data:
                data = data.split(";base64,", 1)[1]
            output_path.write_bytes(base64.b64decode(data))
            return self._project_relative(project_dir, output_path)

        if not result.audio_url:
            raise ValueError("Music generation result has no audio data or URL")

        async with httpx.AsyncClient(timeout=self.repo.settings.runtime.request_timeout_seconds) as client:
            response = await client.get(result.audio_url)
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated music HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return self._project_relative(project_dir, output_path)

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

    def _role_synthesis_voice(self, provider, role: Role) -> str:
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
    def _role_emotion_synthesis_plan(provider, audio: RoleAudio) -> tuple[str | None, dict[str, Any]]:
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

    async def _generate_synthesized_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        voice: str,
    ) -> RoleVoiceGenerationItem:
        preview_text = self._preview_text(role, audio)
        emotion_instruction, emotion_params = self._role_emotion_synthesis_plan(provider, audio)
        synthesis_result = await provider.synthesize_speech(
            voice=voice,
            text=preview_text,
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "synthesis",
                "voice_prompt": audio.desc,
                "emotion_instruction": emotion_instruction,
                "emotion_params": emotion_params,
                "target_model": getattr(provider, "model", None),
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        resolved_voice = synthesis_result.voice or voice
        audio.asset_id = resolved_voice
        audio.voice_type = resolved_voice
        audio.asset_path = preview_audio_path
        audio.emotion_instruction = emotion_instruction
        audio.emotion_params = emotion_params
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="synthesis",
            voice=resolved_voice,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            emotion_instruction=emotion_instruction,
            emotion_params=emotion_params,
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

    async def _run_role_voice_synthesis_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
    ) -> ProjectState:
        generated: list[RoleVoiceGenerationItem] = []
        for role in state.roles.values():
            if role.audio.get("normal") is None:
                raise ValueError(f"Cannot generate role voice for {role.name}: missing normal voice design")
            role_voice = self._role_synthesis_voice(provider, role)
            for audio in role.audio.values():
                generated.append(
                    await self._generate_synthesized_voice(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        role=role,
                        audio=audio,
                        voice=role_voice,
                    )
                )

        output = RoleVoiceGenerationOutput(generated_voices=generated)
        self.repo.save_node_output(project_dir, "role_voice_generation", output)
        return state

    async def _run_role_voice_design_clone_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
    ) -> ProjectState:
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

    async def _run_role_voice_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.audio("speech")
        get_logger().info(
            "node=role_voice_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self._repair_role_voice_design_if_needed(project_dir, state)

        if getattr(provider, "supports_direct_emotion_synthesis", False):
            return await self._run_role_voice_synthesis_generation(
                provider=provider,
                project_dir=project_dir,
                state=state,
            )

        return await self._run_role_voice_design_clone_generation(
            provider=provider,
            project_dir=project_dir,
            state=state,
        )

    async def _run_role_appearance_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        get_logger().info(
            "node=role_appearance_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.role_appearance_design(state, provider)
        roles_by_key = self._role_lookup(state)
        unmatched_role_names: list[str] = []
        for item in output.appearances:
            role = self._resolve_role(roles_by_key, item.role_name)
            if role is None:
                unmatched_role_names.append(item.role_name)
                continue
            appearance_id = normalize_id(f"{role.id}_appearance", item.name)
            role.appearances[item.name] = RoleAppearance(
                id=appearance_id,
                role_id=role.id,
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
            )
        if unmatched_role_names:
            get_logger().warning(
                "node=role_appearance_design ignored unmatched role_name values: %s",
                ", ".join(unmatched_role_names),
            )
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_appearance_design", output)
        return state

    async def _run_role_appearance_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role")
        get_logger().info(
            "node=role_appearance_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        for role in state.roles.values():
            for appearance in role.appearances.values():
                result = await provider.generate_image(
                    appearance.prompt,
                    metadata={
                        "node_name": "role_appearance_generation",
                        "project_id": state.project_id,
                        "role_id": role.id,
                        "appearance_id": appearance.id,
                        "asset_id": appearance.id,
                    },
                )
                asset_path = await self._write_first_generated_image(
                    project_dir,
                    self._image_asset_path(project_dir, "roles", appearance.id),
                    result,
                )
                appearance.asset_id = appearance.id
                appearance.asset_path = asset_path
                appearance.provider = result.provider
                appearance.model = result.model
                appearance.request_id = result.request_id
                appearance.usage = result.usage
                generated.append(
                    StaticAssetGenerationItem(
                        asset_id=appearance.id,
                        asset_type="role_appearance",
                        owner_id=role.id,
                        name=f"{role.name}/{appearance.name}",
                        prompt=appearance.prompt,
                        asset_path=asset_path,
                        provider=result.provider,
                        model=result.model,
                        request_id=result.request_id,
                        usage=result.usage,
                        raw_response=result.raw_response,
                    )
                )
        self.repo.save_node_output(project_dir, "role_appearance_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_prop_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop")
        get_logger().info(
            "node=prop_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.prop_design(state, provider)
        state.props = {
            normalize_id("prop", item.name): Prop(
                id=normalize_id("prop", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                status=item.status,
            )
            for item in output.props
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "prop_design", output)
        return state

    async def _run_prop_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("prop")
        get_logger().info(
            "node=prop_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        for prop in state.props.values():
            result = await provider.generate_image(
                prop.prompt,
                metadata={
                    "node_name": "prop_image_generation",
                    "project_id": state.project_id,
                    "prop_id": prop.id,
                    "asset_id": prop.id,
                },
            )
            asset_path = await self._write_first_generated_image(
                project_dir,
                self._image_asset_path(project_dir, "props", prop.id),
                result,
            )
            prop.asset_id = prop.id
            prop.asset_path = asset_path
            prop.provider = result.provider
            prop.model = result.model
            prop.request_id = result.request_id
            prop.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=prop.id,
                    asset_type="prop",
                    owner_id=prop.id,
                    name=prop.name,
                    prompt=prop.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
        self.repo.save_node_output(project_dir, "prop_image_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_script_compress(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_compress provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.script_compress(state, provider)
        self._validate_episode_keys("script_compress.simple_script", output.simple_script, state)
        state.metadata["simple_script"] = output.simple_script
        state.metadata["global_script"] = output.global_script
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_compress", output)
        return state

    async def _run_layout_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        get_logger().info(
            "node=layout_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.layout_design(state, provider)
        state.layouts = {
            normalize_id("layout", item.name): Layout(
                id=normalize_id("layout", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
            for item in output.layouts
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "layout_design", output)
        return state

    async def _run_layout_dedupe_review(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        get_logger().info(
            "node=layout_dedupe_review provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.layout_dedupe_review(state, provider)
        state.layouts = {
            normalize_id("layout", item.name): Layout(
                id=normalize_id("layout", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
            for item in output.layouts
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "layout_dedupe_review", output)
        return state

    async def _run_layout_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("layout")
        get_logger().info(
            "node=layout_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        for layout in state.layouts.values():
            result = await provider.generate_image(
                layout.prompt,
                metadata={
                    "node_name": "layout_image_generation",
                    "project_id": state.project_id,
                    "layout_id": layout.id,
                    "asset_id": layout.id,
                },
            )
            asset_path = await self._write_first_generated_image(
                project_dir,
                self._image_asset_path(project_dir, "layouts", layout.id),
                result,
            )
            layout.asset_id = layout.id
            layout.asset_path = asset_path
            layout.provider = result.provider
            layout.model = result.model
            layout.request_id = result.request_id
            layout.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=layout.id,
                    asset_type="layout",
                    owner_id=layout.id,
                    name=layout.name,
                    prompt=layout.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
        self.repo.save_node_output(project_dir, "layout_image_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_bgm_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("bgm_plan")
        get_logger().info(
            "node=bgm_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.bgm_design(state, provider)
        state.bgms = {
            normalize_id("bgm", item.name): BGM(
                id=normalize_id("bgm", item.name),
                name=item.name,
                mood=item.mood,
                prompt=item.prompt,
                usage_hint=item.usage_hint,
            )
            for item in output.bgms
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "bgm_design", output)
        return state

    async def _run_bgm_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.music("bgm")
        get_logger().info(
            "node=bgm_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        for bgm in state.bgms.values():
            result = await provider.generate_music(
                bgm.prompt,
                metadata={
                    "node_name": "bgm_generation",
                    "project_id": state.project_id,
                    "bgm_id": bgm.id,
                    "asset_id": bgm.id,
                },
            )
            asset_path = await self._write_generated_music(
                project_dir,
                self._music_asset_path(project_dir, bgm.id, result.audio_format),
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
        self.repo.save_node_output(project_dir, "bgm_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_storyboard_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("storyboard")
        get_logger().info(
            "node=storyboard_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        slots_dir = project_dir / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        generated_episode_keys: list[str] = []
        for episode_key in self._expected_episode_keys(state):
            output = await self.storyboard_service.storyboard_episode(state, provider, episode_key=episode_key)
            if output.episode_key != episode_key:
                raise ValueError(f"Storyboard episode_key must be {episode_key}; got {output.episode_key}")
            self.repo.write_json(slots_dir / f"{episode_key}.json", output)
            generated_episode_keys.append(episode_key)

        state.budget.used_text_calls += len(generated_episode_keys)
        self.repo.save_node_output(
            project_dir,
            "storyboard_generation",
            StoryboardGenerationOutput(generated_episodes=generated_episode_keys),
        )
        return state
