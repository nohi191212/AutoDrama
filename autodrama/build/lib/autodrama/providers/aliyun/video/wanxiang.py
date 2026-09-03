from __future__ import annotations

import asyncio
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError, ProviderError
from autodrama.providers.base import AssetRef, VideoGenerationResult


def _dashscope_api_key(settings: ProviderSettings) -> str | None:
    return settings.secret("api_key_env")


class WanxiangVideoProvider:
    """Tongyi Wanxiang video provider via DashScope async video-synthesis API."""

    name = "wanxiang"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
        self.model = settings.models.get("text_to_video") or settings.models.get("video", "wan2.7-t2v-2026-04-25")
        self.api_key = _dashscope_api_key(settings)
        self.poll_interval_seconds = float(settings.options.get("poll_interval_seconds", 15))
        self.max_polls = int(settings.options.get("max_polls", 80))

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Wanxiang/DashScope API key environment variable")

        metadata = metadata or {}
        input_payload = {"prompt": prompt}
        if negative_prompt := metadata.get("negative_prompt"):
            input_payload["negative_prompt"] = negative_prompt
        if audio_url := metadata.get("audio_url"):
            input_payload["audio_url"] = audio_url
        if reference_urls := metadata.get("reference_urls"):
            input_payload["reference_urls"] = reference_urls
        elif refs:
            urls = [ref.url for ref in refs if ref.url]
            if urls:
                input_payload["reference_urls"] = urls

        parameters: dict[str, Any] = {}
        for key in ("size", "resolution", "ratio", "prompt_extend", "watermark", "shot_type", "audio", "seed"):
            if key in self.settings.options:
                parameters[key] = self.settings.options[key]
        parameters.update(metadata.get("parameters", {}))
        if duration is not None:
            parameters["duration"] = duration
        elif "duration" not in parameters and "duration" in self.settings.options:
            parameters["duration"] = self.settings.options["duration"]

        payload = {
            "model": metadata.get("model", self.model),
            "input": input_payload,
            "parameters": parameters,
        }

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/services/aigc/video-generation/video-synthesis",
                headers={
                    "X-DashScope-Async": "enable",
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Wanxiang video submit failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        body = response.json()
        output = body.get("output", {})
        task_id = output.get("task_id")
        if not task_id:
            raise ProviderBadResponseError(f"Wanxiang response missing output.task_id: {body}")
        return self._result(body)

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Wanxiang/DashScope API key environment variable")

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Wanxiang task query failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        return self._result(response.json())

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        result = await self.submit_video(prompt, refs, duration=duration, metadata=metadata)
        if not wait:
            return result

        if not result.task_id:
            raise ProviderBadResponseError("Wanxiang submit result has no task_id")

        for _ in range(self.max_polls):
            await asyncio.sleep(self.poll_interval_seconds)
            result = await self.query_video_task(result.task_id)
            if result.task_status == "SUCCEEDED":
                return result
            if result.task_status in {"FAILED", "CANCELED", "UNKNOWN"}:
                raise ProviderError(f"Wanxiang task {result.task_id} ended with status {result.task_status}")

        raise ProviderError(f"Wanxiang task {result.task_id} did not finish after {self.max_polls} polls")

    def _result(self, body: dict[str, Any]) -> VideoGenerationResult:
        output = body.get("output", {})
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=output.get("task_id"),
            task_status=output.get("task_status"),
            video_url=output.get("video_url"),
            request_id=body.get("request_id") or body.get("requestId"),
            usage=body.get("usage") or {},
            raw_response=body,
        )
