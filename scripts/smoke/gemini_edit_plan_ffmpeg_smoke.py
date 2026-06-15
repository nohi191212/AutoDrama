from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = ROOT_DIR / ".tmp" / "smoke" / "gemini_edit_plan_ffmpeg"


def resolve_ffmpeg() -> tuple[str | None, str | None]:
    candidates = [
        Path("D:/miniforge3/envs/autodrama/Library/bin/ffmpeg.exe"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            ffmpeg = str(candidate)
            ffprobe_path = Path(ffmpeg).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
            ffprobe = str(ffprobe_path) if ffprobe_path.exists() else shutil.which("ffprobe")
            return ffmpeg, ffprobe
    return None, shutil.which("ffprobe")


def run_process(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise RuntimeError(f"command failed with exit code {process.returncode}: {detail[-3000:]}")
    return process


def ffmpeg_seconds(value: float | int) -> str:
    return f"{max(0.001, float(value)):.3f}"


def create_fixture_video(ffmpeg_path: str, output_path: Path, *, color: str, duration: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=360x640:d={ffmpeg_seconds(duration)}:r=25",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        cwd=ROOT_DIR,
    )


def build_gemini_prompt(source_clips: list[dict[str, Any]]) -> str:
    clip_lines = "\n".join(
        (
            f"- shot_id={clip['shot_id']}, duration={clip['duration_seconds']}s, "
            f"title={clip['title']}, notes={clip['notes']}"
        )
        for clip in source_clips
    )
    return (
        "You are an editing planner for a vertical short drama episode.\n"
        "Return only strict JSON matching schema autodrama.edit_plan.v1.\n"
        "Use fewer than 10 source shots, keep chronological continuity unless there is a strong reason,\n"
        "and express each timeline item with shot_id, source_in, source_out, speed, transition_after, and rationale.\n\n"
        "Source clips:\n"
        f"{clip_lines}\n\n"
        "JSON shape:\n"
        "{\n"
        '  "schema_version": "autodrama.edit_plan.v1",\n'
        '  "episode_key": "episode_001",\n'
        '  "source_clips": [{"shot_id": "...", "path": "...", "duration_seconds": 2.4}],\n'
        '  "timeline": [{"clip_id": "...", "shot_id": "...", "source_in": 0.0, "source_out": 1.2, '
        '"speed": 1.0, "transition_after": {"type": "cut"}, "rationale": "..."}],\n'
        '  "output": {"path": "outputs/episode_001_edit.mp4", "width": 360, "height": 640, "fps": 25}\n'
        "}\n"
    )


def create_mock_gemini_plan(work_dir: Path) -> dict[str, Any]:
    source_clips = [
        {
            "shot_id": "shot_001",
            "path": "clips/shot_001.mp4",
            "duration_seconds": 2.4,
            "title": "Wake-up close-up",
            "notes": "Use a short opening beat; avoid lingering too long.",
        },
        {
            "shot_id": "shot_002",
            "path": "clips/shot_002.mp4",
            "duration_seconds": 3.0,
            "title": "Space reveal",
            "notes": "Keep the middle section for spatial context.",
        },
        {
            "shot_id": "shot_003",
            "path": "clips/shot_003.mp4",
            "duration_seconds": 2.0,
            "title": "Reaction",
            "notes": "End on the reaction moment.",
        },
    ]
    for clip, color in zip(source_clips, ["#223A5E", "#3A6B35", "#8A2E2E"], strict=True):
        create_fixture_video(
            resolve_ffmpeg()[0] or "ffmpeg",
            work_dir / clip["path"],
            color=color,
            duration=float(clip["duration_seconds"]),
        )

    prompt_path = work_dir / "gemini_prompt.txt"
    prompt_path.write_text(build_gemini_prompt(source_clips), encoding="utf-8")

    return {
        "schema_version": "autodrama.edit_plan.v1",
        "episode_key": "episode_001",
        "source_clips": source_clips,
        "timeline": [
            {
                "clip_id": "cut_001",
                "shot_id": "shot_001",
                "source_in": 0.25,
                "source_out": 1.25,
                "speed": 1.0,
                "transition_after": {"type": "cut"},
                "rationale": "Start after the black lead-in and keep the eye-open beat tight.",
            },
            {
                "clip_id": "cut_002",
                "shot_id": "shot_002",
                "source_in": 0.55,
                "source_out": 2.05,
                "speed": 1.0,
                "transition_after": {"type": "cut"},
                "rationale": "Use the clearest spatial reveal and remove slow head/tail frames.",
            },
            {
                "clip_id": "cut_003",
                "shot_id": "shot_003",
                "source_in": 0.10,
                "source_out": 0.95,
                "speed": 1.0,
                "transition_after": {"type": "cut"},
                "rationale": "End quickly on the reaction to keep short-drama pacing.",
            },
        ],
        "output": {
            "path": "outputs/episode_001_edit_smoke.mp4",
            "width": 360,
            "height": 640,
            "fps": 25,
        },
    }


def load_plan(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_plan_relative(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else base_dir / path


def validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != "autodrama.edit_plan.v1":
        raise ValueError("Unsupported edit plan schema_version.")
    source_clips = plan.get("source_clips")
    timeline = plan.get("timeline")
    output = plan.get("output")
    if not isinstance(source_clips, list) or not 1 <= len(source_clips) < 10:
        raise ValueError("source_clips must contain 1-9 clips.")
    if not isinstance(timeline, list) or not timeline:
        raise ValueError("timeline must contain at least one cut.")
    if not isinstance(output, dict):
        raise ValueError("output must be an object.")

    clips_by_id = {}
    for clip in source_clips:
        shot_id = str(clip.get("shot_id") or "").strip()
        if not shot_id:
            raise ValueError("Every source clip needs shot_id.")
        if shot_id in clips_by_id:
            raise ValueError(f"Duplicate shot_id: {shot_id}")
        duration = float(clip.get("duration_seconds") or 0)
        if duration <= 0:
            raise ValueError(f"Invalid duration_seconds for {shot_id}.")
        clips_by_id[shot_id] = {**clip, "duration_seconds": duration}

    for item in timeline:
        clip_id = str(item.get("clip_id") or "").strip()
        shot_id = str(item.get("shot_id") or "").strip()
        if not clip_id or shot_id not in clips_by_id:
            raise ValueError(f"Invalid timeline item clip_id={clip_id!r} shot_id={shot_id!r}.")
        source_in = float(item.get("source_in"))
        source_out = float(item.get("source_out"))
        speed = float(item.get("speed", 1.0))
        duration = clips_by_id[shot_id]["duration_seconds"]
        if source_in < 0 or source_out <= source_in or source_out > duration + 0.05:
            raise ValueError(
                f"Invalid trim range for {clip_id}: {source_in:.3f}-{source_out:.3f}, source duration={duration:.3f}."
            )
        if speed <= 0 or speed > 4:
            raise ValueError(f"Invalid speed for {clip_id}: {speed}.")
        transition_type = (item.get("transition_after") or {}).get("type", "cut")
        if transition_type != "cut":
            raise ValueError(f"Only cut transitions are supported in this smoke script, got {transition_type!r}.")

    for key in ("path", "width", "height", "fps"):
        if key not in output:
            raise ValueError(f"output.{key} is required.")


def normalize_cut(
    ffmpeg_path: str,
    *,
    base_dir: Path,
    source_path: Path,
    output_path: Path,
    item: dict[str, Any],
    render: dict[str, Any],
) -> float:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_in = float(item["source_in"])
    source_out = float(item["source_out"])
    speed = float(item.get("speed", 1.0))
    source_duration = source_out - source_in
    target_duration = source_duration / speed
    width = int(render["width"])
    height = int(render["height"])
    fps = int(render["fps"])
    vf = (
        f"trim=start={ffmpeg_seconds(source_in)}:duration={ffmpeg_seconds(source_duration)},"
        f"setpts=(PTS-STARTPTS)/{speed:.6f},"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p"
    )
    run_process(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        cwd=base_dir,
    )
    return target_duration


def write_concat_file(path: Path, video_paths: list[Path]) -> None:
    lines = []
    for video_path in video_paths:
        normalized = str(video_path.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{normalized}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def concat_cuts(ffmpeg_path: str, *, base_dir: Path, concat_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(output_path),
        ],
        cwd=base_dir,
    )


def probe_duration(ffprobe_path: str | None, video_path: Path) -> float | None:
    if not ffprobe_path:
        return None
    process = run_process(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        cwd=ROOT_DIR,
    )
    try:
        return float(process.stdout.strip())
    except ValueError:
        return None


def execute_plan(ffmpeg_path: str, ffprobe_path: str | None, plan_path: Path) -> dict[str, Any]:
    plan = load_plan(plan_path)
    validate_plan(plan)
    base_dir = plan_path.parent
    clips_by_id = {str(clip["shot_id"]): clip for clip in plan["source_clips"]}
    tmp_dir = base_dir / "normalized_cuts"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    normalized_paths: list[Path] = []
    expected_duration = 0.0
    cut_outputs: list[dict[str, Any]] = []
    for index, item in enumerate(plan["timeline"], start=1):
        shot_id = str(item["shot_id"])
        source_path = resolve_plan_relative(base_dir, str(clips_by_id[shot_id]["path"]))
        if not source_path.exists():
            raise FileNotFoundError(f"Source clip not found: {source_path}")
        cut_path = tmp_dir / f"{index:03d}_{item['clip_id']}.mp4"
        cut_duration = normalize_cut(
            ffmpeg_path,
            base_dir=base_dir,
            source_path=source_path,
            output_path=cut_path,
            item=item,
            render=plan["output"],
        )
        expected_duration += cut_duration
        normalized_paths.append(cut_path)
        cut_outputs.append(
            {
                "clip_id": item["clip_id"],
                "shot_id": shot_id,
                "path": str(cut_path.relative_to(base_dir)).replace("\\", "/"),
                "expected_duration_seconds": round(cut_duration, 3),
            }
        )

    concat_path = base_dir / "concat.txt"
    write_concat_file(concat_path, normalized_paths)
    output_path = resolve_plan_relative(base_dir, str(plan["output"]["path"]))
    concat_cuts(ffmpeg_path, base_dir=base_dir, concat_path=concat_path, output_path=output_path)
    actual_duration = probe_duration(ffprobe_path, output_path)
    if actual_duration is not None and abs(actual_duration - expected_duration) > 0.45:
        raise AssertionError(
            f"Output duration mismatch: actual={actual_duration:.3f}, expected={expected_duration:.3f}"
        )

    manifest = {
        "smoke": "gemini_edit_plan_ffmpeg",
        "plan_path": str(plan_path),
        "output_path": str(output_path),
        "expected_duration_seconds": round(expected_duration, 3),
        "actual_duration_seconds": round(actual_duration, 3) if actual_duration is not None else None,
        "cut_outputs": cut_outputs,
    }
    manifest_path = base_dir / "execution_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test a Gemini-style standardized edit plan by trimming and concatenating clips with ffmpeg."
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="Optional existing edit-plan JSON. Without it, the script creates local fixture clips and a mock Gemini response.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR / datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Fixture/output directory used when --plan is omitted.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ffmpeg_path, ffprobe_path = resolve_ffmpeg()
    if not ffmpeg_path:
        print("gemini_edit_plan_ffmpeg_smoke=skipped reason=ffmpeg_unavailable")
        return 0

    if args.plan is None:
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = create_mock_gemini_plan(work_dir)
        plan_path = work_dir / "gemini_mock_response.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        plan_path = args.plan.resolve()

    manifest = execute_plan(ffmpeg_path, ffprobe_path, plan_path)
    print("gemini_edit_plan_ffmpeg_smoke=ok")
    print(f"plan_path={manifest['plan_path']}")
    print(f"output_path={manifest['output_path']}")
    print(f"expected_duration_seconds={manifest['expected_duration_seconds']}")
    print(f"actual_duration_seconds={manifest['actual_duration_seconds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
