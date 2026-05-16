from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class TextLLM(Protocol):
    name: str

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """Generate JSON validated by the requested Pydantic schema."""


class AssetRef(BaseModel):
    id: str | None = None
    type: Literal["image", "video", "audio", "file", "url"] = "url"
    path: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceDesignResult(BaseModel):
    provider: str
    model: str
    voice: str
    target_model: str | None = None
    preview_audio_data: str | None = None
    preview_audio_sample_rate: int | None = None
    preview_audio_format: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class VoiceSynthesisResult(BaseModel):
    provider: str
    model: str
    voice: str
    audio_data: str | None = None
    audio_sample_rate: int | None = None
    audio_format: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class VoiceDesigner(Protocol):
    name: str

    async def create_voice(
        self,
        *,
        voice_prompt: str,
        preview_text: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        """Create a reusable TTS voice and return its preview audio."""

    async def clone_voice_from_audio(
        self,
        *,
        source_audio_path: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        """Clone a reusable TTS voice from a local audio sample."""

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        """Synthesize sample speech using an existing voice."""


class VideoGenerationResult(BaseModel):
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    video_url: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class VideoGenerator(Protocol):
    name: str

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        """Submit a video generation task and return its task id/status."""

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        """Query an async video generation task."""

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        """Submit a video task, optionally polling until completion."""
