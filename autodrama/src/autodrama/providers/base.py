from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

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
    metadata: dict[str, Any] = {}


class VideoGenerationResult(BaseModel):
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    video_url: str | None = None
    usage: dict[str, Any] = {}
    raw_response: dict[str, Any] = {}


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
