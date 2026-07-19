from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "autodrama" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.config import Settings, VoiceAlignmentSettings
from autodrama.postgen.audio_pipeline import convert_and_rebuild_vocals, extract_audio
from autodrama.postgen.schemas import PostgenSourceClip
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.postgen import POSTGEN_NODES, PostgenWorkflow

def make_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=360x640:rate=25:duration=3",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True)


async def main() -> None:
    run_root = ROOT / ".tmp" / "postgen_smoke" / uuid.uuid4().hex
    settings = Settings.model_validate(
        {
            "output": {"root_dir": str(run_root / "projects")},
            "runtime": {"ffmpeg_path": "ffmpeg"},
            "postgen": {
                "render_width": 360,
                "render_height": 640,
                "fps": 25,
                "burn_subtitles": True,
                "edit_plan_mode": "deterministic",
                "voice_alignment": {"enabled": False},
                "subtitles": {
                    "enabled": True,
                    "backend": "sidecar",
                    "font_name": "Arial",
                    "max_chars_per_line": 12,
                },
                "audit": {"enabled": True, "source_asr": False, "source_audit": True, "final_audit": True},
            },
            "routing": {"text": {"postgen_audit": "fake", "postgen_edit_plan": "fake"}},
        }
    )
    repo = ProjectRepository(settings)
    project_dir = repo.create_project(title="postgen smoke", raw_script="smoke", project_id="postgen_smoke")
    source = project_dir / "assets" / "videos" / "source.mp4"
    make_source(source)
    raw_audio = project_dir / "assets" / "audios" / "conversion_smoke.wav"
    rebuilt_audio = project_dir / "assets" / "audios" / "conversion_rebuilt.wav"
    await extract_audio(source, raw_audio, ffmpeg_path="ffmpeg")
    await convert_and_rebuild_vocals(
        raw_audio,
        [{"speaker": "SPEAKER_00", "start": 0.2, "end": 1.3}],
        output_path=rebuilt_audio,
        work_dir=project_dir / ".tmp" / "conversion_smoke",
        settings=VoiceAlignmentSettings(
            enabled=True,
            rvc_command=["unused"],
            fail_on_unmapped_speaker=False,
        ),
        config_dir=ROOT,
        ffmpeg_path="ffmpeg",
    )
    assert rebuilt_audio.exists() and rebuilt_audio.stat().st_size > 0

    clip = PostgenSourceClip(
        episode_key="episode_001",
        shot_id="episode_001_shot_001",
        shot_index=1,
        title="smoke",
        source_path=repo.layout.project_relative(project_dir, source),
        duration_seconds=3.0,
        dialogue_lines=["测试对白"],
        content="single synthetic smoke shot",
    )
    source_path = project_dir / "assets" / "json" / "postgen" / "source_clips" / "episode_001.json"
    repo.write_json(source_path, {"episode_key": "episode_001", "source_clips": [clip.model_dump(mode="json")], "warnings": []})

    sidecar = project_dir / "assets" / "audios" / "postgen" / "episode_001" / "subtitle_audio.whisperx.json"
    repo.write_json(
        sidecar,
        {
            "language": "zh",
            "segments": [
                {
                    "start": 0.4,
                    "end": 2.2,
                    "text": "这是后处理字幕烟雾测试。",
                    "words": [
                        {"word": "这是", "start": 0.4, "end": 0.7},
                        {"word": "后处理", "start": 0.7, "end": 1.1},
                        {"word": "字幕", "start": 1.1, "end": 1.45},
                        {"word": "烟雾测试。", "start": 1.45, "end": 2.2},
                    ],
                }
            ],
        },
    )

    workflow = PostgenWorkflow(repo=repo, router=ProviderRouter(settings, provider_override="fake"))
    for node in POSTGEN_NODES[1:]:
        await workflow.run(project_dir, only=node, force=True, episode_keys=["episode_001"])

    final_video = project_dir / "outputs" / "videos" / "episode_001_postgen.mp4"
    final_audit = project_dir / "assets" / "json" / "postgen" / "audits" / "episode_001.final.json"
    assert final_video.exists() and final_video.stat().st_size > 0
    audit = json.loads(final_audit.read_text(encoding="utf-8"))
    assert audit["verdict"] == "pass", audit
    print(json.dumps({"ok": True, "project_dir": str(project_dir), "final_video": str(final_video)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
