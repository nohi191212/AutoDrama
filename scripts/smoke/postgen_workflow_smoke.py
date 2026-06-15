from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardEpisodeOutput, StoryboardShot  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.postgen import PostgenWorkflow  # noqa: E402


def resolve_ffmpeg() -> str | None:
    candidates = [
        Path("D:/miniforge3/envs/autodrama/Library/bin/ffmpeg.exe"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def run_ffmpeg(ffmpeg_path: str, args: list[str]) -> None:
    process = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", *args],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise RuntimeError(f"ffmpeg fixture command failed: {detail[-2000:]}")


def create_fixture_video(ffmpeg_path: str, path: Path, *, color: str, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ffmpeg_path,
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=360x640:d={duration}:r=25",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
    )


async def main_async() -> int:
    ffmpeg_path = resolve_ffmpeg()
    if not ffmpeg_path:
        print("postgen_workflow_smoke=skipped reason=ffmpeg_unavailable")
        return 0

    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    settings.runtime.ffmpeg_path = ffmpeg_path
    settings.postgen.edit_plan_mode = "deterministic"
    settings.postgen.render_width = 360
    settings.postgen.render_height = 640
    settings.postgen.fps = 25

    repo = ProjectRepository(settings)
    project_id = f"postgen_workflow_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Postgen Workflow Smoke",
        raw_script="A short proof reveal scene.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=10,
    )

    shot_paths = []
    for index, color in enumerate(["#223A5E", "#3A6B35", "#8A2E2E"], start=1):
        path = project_dir / "assets" / "videos" / "shots" / f"episode_001_shot_{index:03d}.mp4"
        create_fixture_video(ffmpeg_path, path, color=color, duration=2.0 + index * 0.2)
        shot_paths.append(path)

    episode = StoryboardEpisodeOutput(
        episode_key="episode_001",
        shots=[
            StoryboardShot(
                shot_id=f"episode_001_shot_{index:03d}",
                index=index,
                layout_id="layout_test",
                title=f"Test shot {index}",
                content=f"Postgen fixture content {index}.",
                camera_shooting_angle="medium",
                camera_movement="locked",
                duration_seconds=2.0 + index * 0.2,
                transition="hard cut",
                dialogue=[],
                video_prompt=f"Fixture video prompt {index}.",
                video_asset_id=f"shot_video_{index:03d}",
                video_asset_path=str(path.relative_to(project_dir)).replace("\\", "/"),
            )
            for index, path in enumerate(shot_paths, start=1)
        ],
    )
    repo.write_json(project_dir / "shots" / "episode_001.json", episode)

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PostgenWorkflow(repo=repo, router=router)
    state = await workflow.run(
        project_dir,
        until="postgen_video_composition",
        episode_keys=["episode_001"],
        force=True,
    )

    output_path = project_dir / "outputs" / "videos" / "episode_001_postgen.mp4"
    raw_plan_path = project_dir / "assets" / "json" / "postgen" / "edit_plans" / "episode_001.raw.json"
    validated_plan_path = project_dir / "assets" / "json" / "postgen" / "edit_plans" / "episode_001.validated.json"
    node_path = project_dir / "assets" / "json" / "nodes" / "postgen_video_composition.json"
    if "postgen_video_composition" not in state.completed_nodes:
        raise AssertionError("postgen_video_composition was not marked completed")
    for path in (output_path, raw_plan_path, validated_plan_path, node_path):
        if not path.exists() or path.stat().st_size <= 0:
            raise AssertionError(f"Expected postgen output missing or empty: {path}")

    print("postgen_workflow_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"output_video={output_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
