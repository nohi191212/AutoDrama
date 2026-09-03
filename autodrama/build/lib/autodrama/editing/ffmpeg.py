from __future__ import annotations

import asyncio
from pathlib import Path
import re
import subprocess
from typing import Any

from autodrama.logging import get_logger


def ffmpeg_seconds(value: float | int) -> str:
    return f"{max(0.001, float(value)):.3f}"


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "clip"


def write_concat_file(path: Path, video_paths: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for video_path in video_paths:
        normalized = str(video_path.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{normalized}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{value}'"


def ffmpeg_base_command(ffmpeg_path: str) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
    ]


def audio_filter(plan: Any, layers: list[Any]) -> str:
    if not layers:
        duration = ffmpeg_seconds(plan.estimated_duration_seconds)
        return f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration={duration}[aout]"

    chains: list[str] = []
    labels: list[str] = []
    for index, layer in enumerate(layers, start=1):
        output_label = f"a{index}"
        labels.append(f"[{output_label}]")
        filters = ["aresample=44100", "aformat=channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
        if layer.duration_seconds is not None:
            filters.insert(0, f"atrim=duration={ffmpeg_seconds(layer.duration_seconds)}")
        filters.append(f"volume={max(0.0, min(float(layer.volume), 4.0)):.3f}")
        if layer.fade_in_seconds > 0:
            filters.append(f"afade=t=in:st=0:d={ffmpeg_seconds(layer.fade_in_seconds)}")
        if layer.fade_out_seconds > 0 and layer.duration_seconds:
            start = max(0.0, layer.duration_seconds - layer.fade_out_seconds)
            filters.append(f"afade=t=out:st={ffmpeg_seconds(start)}:d={ffmpeg_seconds(layer.fade_out_seconds)}")
        delay_ms = max(0, int(round(layer.start_time * 1000)))
        if delay_ms:
            filters.append(f"adelay={delay_ms}:all=1")
        chains.append(f"[{index}:a]{','.join(filters)}[{output_label}]")

    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]")
    return ";".join(chains)


async def run_ffmpeg(command: list[str], *, cwd: Path) -> None:
    get_logger().debug("ffmpeg command: %s", " ".join(command))
    process = await asyncio.to_thread(
        subprocess.run,
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
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise RuntimeError(f"ffmpeg failed with exit code {process.returncode}: {detail}")


__all__ = [
    "audio_filter",
    "ffmpeg_base_command",
    "ffmpeg_seconds",
    "filter_path",
    "run_ffmpeg",
    "safe_filename",
    "write_concat_file",
]
