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
from autodrama.core.schemas import BGM, ShotDialogueAudioAsset, StoryboardEpisodeOutput, StoryboardShot  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.editing import EditingWorkflow  # noqa: E402


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
        [ffmpeg_path, "-y", *args],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise RuntimeError(f"ffmpeg fixture command failed: {detail[-2000:]}")


async def main_async() -> int:
    ffmpeg_path = resolve_ffmpeg()
    if not ffmpeg_path:
        print("final_video_composition_smoke=skipped reason=ffmpeg_unavailable")
        return 0

    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    settings.runtime.ffmpeg_path = ffmpeg_path
    repo = ProjectRepository(settings)
    project_id = f"final_video_composition_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Final Video Composition Smoke",
        raw_script="A one-shot proof reveal scene.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=2,
    )

    shot_video_path = project_dir / "assets" / "videos" / "shots" / "shot_001.mp4"
    dialogue_audio_path = project_dir / "assets" / "audios" / "shot_dialogues" / "dialogue_001.wav"
    bgm_path = project_dir / "assets" / "audios" / "bgms" / "bgm_001.wav"
    shot_video_path.parent.mkdir(parents=True, exist_ok=True)
    dialogue_audio_path.parent.mkdir(parents=True, exist_ok=True)
    bgm_path.parent.mkdir(parents=True, exist_ok=True)

    run_ffmpeg(
        ffmpeg_path,
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=640x360:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(shot_video_path),
        ],
    )
    run_ffmpeg(
        ffmpeg_path,
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.8",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(dialogue_audio_path),
        ],
    )
    run_ffmpeg(
        ffmpeg_path,
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=3",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(bgm_path),
        ],
    )

    state = repo.load_state(project_dir)
    state.bgms["bgm_test"] = BGM(
        id="bgm_test",
        name="Test BGM",
        mood="neutral",
        prompt="Low background tone.",
        asset_path=str(bgm_path.relative_to(project_dir)).replace("\\", "/"),
        duration_seconds=3,
    )
    repo.save_state(project_dir, state)

    episode = StoryboardEpisodeOutput(
        episode_key="episode_001",
        shots=[
            StoryboardShot(
                shot_id="episode_001_shot_001",
                index=1,
                layout_id="layout_test",
                title="Proof reveal",
                content="The lead points at the proof on the screen.",
                camera_shooting_angle="medium shot",
                camera_movement="locked",
                duration_seconds=2,
                dialogue=["Lead: The proof is on the screen."],
                bgm_id="bgm_test",
                ref_frame_prompt="A proof reveal frame.",
                video_prompt="A proof reveal shot.",
                dialogue_audio_assets=[
                    ShotDialogueAudioAsset(
                        asset_id="dialogue_001",
                        line_index=1,
                        text="The proof is on the screen.",
                        asset_path=str(dialogue_audio_path.relative_to(project_dir)).replace("\\", "/"),
                        provider="local",
                        model="ffmpeg_sine",
                    )
                ],
                video_asset_id="shot_video_001",
                video_asset_path=str(shot_video_path.relative_to(project_dir)).replace("\\", "/"),
            )
        ],
    )
    repo.write_json(project_dir / "slots" / "episode_001.json", episode)

    router = ProviderRouter(settings, provider_override="fake")
    workflow = EditingWorkflow(repo=repo, router=router)
    state = await workflow.run(
        project_dir,
        until="final_video_composition",
        episode_keys=["episode_001"],
        force=True,
        burn_subtitles=True,
    )

    output_path = project_dir / "outputs" / "videos" / "episode_001.mp4"
    node_path = project_dir / "assets" / "json" / "nodes" / "final_video_composition.json"
    if "final_video_composition" not in state.completed_nodes:
        raise AssertionError("final_video_composition was not marked completed")
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise AssertionError(f"Output video missing or empty: {output_path}")
    if not node_path.exists():
        raise AssertionError(f"Node output missing: {node_path}")

    print("final_video_composition_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"output_video={output_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
