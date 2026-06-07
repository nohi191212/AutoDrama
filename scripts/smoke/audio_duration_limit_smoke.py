from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.services.audio_duration import (  # noqa: E402
    compact_tts_text,
    limit_audio_duration,
    probe_audio_duration_seconds,
)


def resolve_ffmpeg() -> str | None:
    candidates = [
        shutil.which("ffmpeg"),
        "D:/miniforge3/envs/autodrama/Library/bin/ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        print("audio_duration_limit_smoke=skipped reason=ffmpeg_unavailable")
        return 0

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "audio_duration_limit"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "long_tone.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=12",
            str(audio_path),
        ],
        check=True,
    )

    before = probe_audio_duration_seconds(audio_path, ffmpeg_path=ffmpeg)
    require(before is not None and before > 11.0, f"unexpected source duration: {before}")

    result = limit_audio_duration(audio_path, max_duration_seconds=10, ffmpeg_path=ffmpeg)
    after = probe_audio_duration_seconds(audio_path, ffmpeg_path=ffmpeg)
    require(result.trimmed, f"audio was not trimmed: {result}")
    require(result.original_duration_seconds is not None and result.original_duration_seconds > 11.0, str(result))
    require(result.duration_seconds is not None and result.duration_seconds <= 10.0, str(result))
    require(after is not None and after <= 10.25, f"unexpected trimmed file duration: {after}")

    compacted = compact_tts_text(
        "我是九韶，万象神话乐园的AI管家，负责辅佐宿主经营乐园、构筑密室、收集情绪能量。",
        max_chars=40,
    )
    require(len(compacted) <= 41, f"compacted text too long: {compacted}")

    print("audio_duration_limit_smoke=ok")
    print(f"before={before} after={after} compacted={compacted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
