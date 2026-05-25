from __future__ import annotations

import asyncio
import base64
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Layout,
    RefFrameSpatialPlan,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.local.mock.fake import FakeImageProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402

T = TypeVar("T", bound=BaseModel)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
        )
    )


class SpatialTextProvider:
    name = "spatial_text"
    model = "deepseek-v4-flash"
    thinking_enabled = False
    reasoning_effort = "low"

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del prompt, temperature
        metadata = metadata or {}
        if schema is not RefFrameSpatialPlan:
            raise AssertionError(f"Unexpected schema: {schema.__name__}")
        shot_id = str(metadata.get("shot_id") or "")
        if shot_id.endswith("_013"):
            data = {
                "same_physical_space_as_previous": True,
                "confidence": 0.91,
                "continuity_mode": "previous_shot",
                "physical_space_key": "layout_room::东南侧",
                "physical_space_note": "会议室东南侧",
                "reference_shot_ids": [],
                "spatial_structure_summary": "林舟保持在证物桌左侧，围观人群带仍在后景右侧。",
                "spatial_constraints": ["证物桌不能从画面右侧跳到左侧", "后景人群带保持在右后方"],
                "movement_allowed": False,
                "movement_reason": None,
            }
        else:
            data = {
                "same_physical_space_as_previous": False,
                "confidence": 0.88,
                "continuity_mode": "reuse_prior_space",
                "physical_space_key": "layout_room::东南侧",
                "physical_space_note": "会议室东南侧",
                "reference_shot_ids": [f"episode_001_shot_{index:03d}" for index in range(1, 13)],
                "spatial_structure_summary": "复用会议室东南侧空间结构，证物桌、人群带和投影墙的相对位置保持稳定。",
                "spatial_constraints": ["最多使用 10 张历史参考帧", "背景人群保持大致站位区域"],
                "movement_allowed": False,
                "movement_reason": None,
            }
        return schema.model_validate(data)


class RecordingImageProvider(FakeImageProvider):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "refs": list(refs or []),
                "metadata": dict(metadata or {}),
            }
        )
        return await super().generate_image(prompt, refs=refs, size=size, metadata=metadata)


class Router:
    def __init__(self) -> None:
        self.text_provider = SpatialTextProvider()
        self.image_provider = RecordingImageProvider()

    def text(self, purpose: str):
        require(purpose == "ref_frame_spatial", f"Unexpected text purpose: {purpose}")
        return self.text_provider

    def image(self, purpose: str):
        require(purpose == "ref_frame", f"Unexpected image purpose: {purpose}")
        return self.image_provider


def shot(index: int, *, generated: bool) -> StoryboardShot:
    shot_id = f"episode_001_shot_{index:03d}"
    payload = StoryboardShot(
        shot_id=shot_id,
        index=index,
        layout_id="layout_room",
        title=f"镜头 {index}",
        duration_seconds=6,
        role_ids=[],
        prop_ids=[],
        ref_frame_prompt="林舟站在证物桌左侧，背景人群在右后方围观。",
        video_prompt="镜头保持会议室东南侧空间关系。",
    )
    if generated:
        payload.ref_frame_asset_id = f"{shot_id}_ref_frame"
        payload.ref_frame_asset_path = f"assets/images/ref_frames/{shot_id}_ref_frame.png"
        payload.physical_space_key = "layout_room::东南侧"
        payload.physical_space_note = "会议室东南侧"
        payload.spatial_structure_summary = "林舟在证物桌左侧，背景人群在右后方。"
    return payload


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"ref_frame_spatial_continuity_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Ref Frame Spatial Continuity Smoke",
        raw_script="林舟在会议室东南侧连续展示证据。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    state.layouts["layout_room"] = Layout(
        id="layout_room",
        name="会议室",
        desc="带投影墙和证物桌的会议室。",
        prompt="会议室东南侧，证物桌居中偏右，围观人群在后景右侧。",
    )
    repo.save_state(project_dir, state)

    shots = [shot(index, generated=index <= 12) for index in range(1, 15)]
    for item in shots[:12]:
        write_png(project_dir / str(item.ref_frame_asset_path))
    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=shots)
    repo.write_json(project_dir / "shots" / "episode_001.json", episode)

    router = Router()
    workflow = GenerationWorkflow(repo=repo, router=router)  # type: ignore[arg-type]
    await workflow.run(
        project_dir,
        only="ref_frame_generation",
        episode_keys=["episode_001"],
        shot_selectors=["13", "14"],
        force=True,
    )

    calls = router.image_provider.calls
    require(len(calls) == 2, f"Expected two ref-frame image calls, got {len(calls)}")
    first_continuity_refs = [
        ref for ref in calls[0]["refs"] if ref.metadata.get("asset_type") == "continuity_ref_frame"
    ]
    require(first_continuity_refs, "Shot 13 should receive previous-shot continuity refs")
    require(
        first_continuity_refs[0].metadata.get("source_shot_id") == "episode_001_shot_012",
        f"Shot 13 should prioritize shot 12, got {first_continuity_refs[0].metadata}",
    )
    require("会议室东南侧" in calls[0]["prompt"], "Shot 13 prompt missing physical space note")
    require("背景人群" in calls[0]["prompt"], "Shot 13 prompt missing background crowd constraint")

    second_continuity_refs = [
        ref for ref in calls[1]["refs"] if ref.metadata.get("asset_type") == "continuity_ref_frame"
    ]
    require(len(second_continuity_refs) == 10, f"Shot 14 should cap continuity refs at 10, got {len(second_continuity_refs)}")

    updated = StoryboardEpisodeOutput.model_validate_json(
        (project_dir / "shots" / "episode_001.json").read_text(encoding="utf-8")
    )
    shot_013 = next(item for item in updated.shots if item.index == 13)
    require(shot_013.physical_space_note == "会议室东南侧", "Shot 13 physical space note was not persisted")
    require(shot_013.spatial_reference_shot_ids[0] == "episode_001_shot_012", "Shot 13 references were not persisted")

    index_path = project_dir / "assets" / "json" / "spatial_ref_frame_index.json"
    require(index_path.exists(), "spatial_ref_frame_index.json was not written")

    print("ref_frame_spatial_continuity_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"shot13_refs={len(first_continuity_refs)} shot14_refs={len(second_continuity_refs)}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
