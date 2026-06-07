from __future__ import annotations

import asyncio
import base64
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    ProjectState,
    SafeImagePromptRewriteOutput,
    ScriptBundle,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.providers.base import ImageGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402


class UnsafeOnceImageProvider:
    name = "unsafe_once_image"
    model = "unsafe-once-model"
    supports_reference_images = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        self.calls.append({"prompt": prompt, "refs": refs or [], "size": size, "metadata": metadata or {}})
        if len(self.calls) == 1:
            raise ProviderBadResponseError(
                "ToAPI image task tsk_fake failed: {'code': 'generation_failed', "
                "'message': 'image job failed: {\"error_code\":\"image_unsafe\","
                "\"message\":\"The generated images appear to be unsafe.\"}'}"
            )
        asset_id = str((metadata or {}).get("asset_id") or "asset")
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_data=[base64.b64encode(f"safe image:{asset_id}:{prompt}".encode("utf-8")).decode("ascii")],
            request_id=f"safe-image-{asset_id}",
            raw_response={"asset_id": asset_id, "prompt": prompt},
        )


class SafetyRewriteTextProvider:
    name = "safety_rewrite_text"
    model = "safety-rewrite-model"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_json(self, prompt: str, schema, *, temperature=0.7, metadata=None):
        self.calls.append({"prompt": prompt, "schema": schema, "temperature": temperature, "metadata": metadata or {}})
        if schema is not SafeImagePromptRewriteOutput:
            raise AssertionError(f"Unexpected schema: {schema}")
        return SafeImagePromptRewriteOutput(
            prompt="安全改写后的参考帧 prompt：用暗色旧污渍和紧张神情替代血迹与伤口，保持原构图。",
            notes="removed unsafe gore terms",
        )


class Router:
    def __init__(self) -> None:
        self.image_provider = UnsafeOnceImageProvider()
        self.text_provider = SafetyRewriteTextProvider()

    def image(self, _purpose: str):
        return self.image_provider

    def text(self, _purpose: str):
        return self.text_provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "ref_frame_safety_prompt_rewrite"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root
    repo = ProjectRepository(settings)
    router = Router()
    workflow = GenerationWorkflow(repo=repo, router=router)  # type: ignore[arg-type]
    project_dir = tmp_root / "project"
    state = ProjectState(
        project_id="ref-frame-safety-prompt-rewrite-smoke",
        title="Ref Frame Safety Prompt Rewrite Smoke",
        raw_script="",
        script=ScriptBundle(raw_script=""),
    )
    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=[])
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_room",
        title="Unsafe prompt",
        duration_seconds=4,
        ref_frame_prompt="原始 prompt 带有血迹和开放性伤口。",
        video_prompt="角色站在房间里。",
    )

    result, final_prompt, rewrites = await workflow._generate_image_with_safety_prompt_rewrites(
        provider=router.image_provider,
        project_dir=project_dir,
        state=state,
        episode=episode,
        shot=shot,
        node_name="ref_frame_generation",
        asset_id="episode_001_shot_001_ref_frame",
        prompt=shot.ref_frame_prompt,
        refs=[],
        metadata={
            "node_name": "ref_frame_generation",
            "project_id": state.project_id,
            "episode_key": episode.episode_key,
            "shot_id": shot.shot_id,
            "asset_id": "episode_001_shot_001_ref_frame",
        },
    )

    require(len(router.image_provider.calls) == 2, f"Expected original + rewritten image calls: {router.image_provider.calls}")
    require(len(router.text_provider.calls) == 1, f"Expected one prompt rewrite call: {router.text_provider.calls}")
    require(len(rewrites) == 1, f"Expected one rewrite record: {rewrites}")
    require("安全改写后的参考帧 prompt" in final_prompt, final_prompt)
    require(result.raw_response.get("safety_prompt_rewrite", {}).get("final_prompt") == final_prompt, result.raw_response)
    require(state.budget.used_text_calls == 1, f"Rewrite text call should be counted: {state.budget.used_text_calls}")

    print("ref_frame_safety_prompt_rewrite_smoke=ok")
    print(f"image_calls={len(router.image_provider.calls)}")
    print(f"rewrite_calls={len(router.text_provider.calls)}")
    print(f"final_prompt={final_prompt}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
