from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys

from autodrama.logging import get_logger


DEFAULT_MAX_GENERATED_AUDIO_DURATION_SECONDS = 10.0
DEFAULT_ROLE_VOICE_PREVIEW_TEXT_CHARS = 40


@dataclass(frozen=True)
class AudioDurationLimitResult:
    duration_seconds: float | None = None
    original_duration_seconds: float | None = None
    max_duration_seconds: float | None = None
    trimmed: bool = False
    skipped_reason: str | None = None


def provider_max_generated_audio_duration_seconds(provider: object, *, default: float = DEFAULT_MAX_GENERATED_AUDIO_DURATION_SECONDS) -> float:
    options = getattr(getattr(provider, "settings", None), "options", None)
    if isinstance(options, dict):
        for key in ("max_generated_audio_duration_seconds", "tts_max_duration_seconds"):
            if key in options:
                return _positive_float(options.get(key), default=default)
    return default


def provider_role_voice_preview_text_chars(provider: object, *, default: int = DEFAULT_ROLE_VOICE_PREVIEW_TEXT_CHARS) -> int:
    options = getattr(getattr(provider, "settings", None), "options", None)
    if isinstance(options, dict):
        for key in ("role_voice_preview_text_chars", "max_role_voice_preview_text_chars"):
            if key in options:
                return max(1, int(_positive_float(options.get(key), default=default)))
    return default


def compact_tts_text(text: str | None, *, max_chars: int) -> str:
    value = " ".join(str(text or "").split()).strip()
    if len(value) <= max_chars:
        return value

    scan_from = min(max_chars, len(value)) - 1
    scan_to = max(0, int(max_chars * 0.55))
    for index in range(scan_from, scan_to - 1, -1):
        if value[index] in "。！？!?；;":
            return value[: index + 1].strip()

    compacted = value[:max_chars].rstrip("，,；;：:、 ")
    if compacted and compacted[-1] not in "。！？!?":
        compacted += "。"
    return compacted


def probe_audio_duration_seconds(path: Path, *, ffmpeg_path: str = "ffmpeg") -> float | None:
    if not path.exists() or not path.is_file():
        return None
    for ffprobe_path in _ffprobe_candidates(ffmpeg_path):
        try:
            process = subprocess.run(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            continue
        if process.returncode != 0:
            continue
        try:
            duration = float((process.stdout or "").strip())
        except ValueError:
            continue
        if duration > 0:
            return round(duration, 3)
    return None


def limit_audio_duration(
    path: Path,
    *,
    max_duration_seconds: float = DEFAULT_MAX_GENERATED_AUDIO_DURATION_SECONDS,
    ffmpeg_path: str = "ffmpeg",
) -> AudioDurationLimitResult:
    max_duration_seconds = _positive_float(max_duration_seconds, default=DEFAULT_MAX_GENERATED_AUDIO_DURATION_SECONDS)
    duration = probe_audio_duration_seconds(path, ffmpeg_path=ffmpeg_path)
    if duration is None:
        return AudioDurationLimitResult(
            max_duration_seconds=max_duration_seconds,
            skipped_reason="duration_probe_unavailable",
        )
    if duration <= max_duration_seconds:
        return AudioDurationLimitResult(
            duration_seconds=duration,
            original_duration_seconds=duration,
            max_duration_seconds=max_duration_seconds,
        )

    ffmpeg = _resolve_ffmpeg_path(ffmpeg_path)
    if ffmpeg is None:
        return AudioDurationLimitResult(
            duration_seconds=duration,
            original_duration_seconds=duration,
            max_duration_seconds=max_duration_seconds,
            skipped_reason="ffmpeg_unavailable",
        )

    temp_path = path.with_name(f".{path.stem}.trimmed{path.suffix}")
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-t",
                f"{max_duration_seconds:.3f}",
                str(temp_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return AudioDurationLimitResult(
            duration_seconds=duration,
            original_duration_seconds=duration,
            max_duration_seconds=max_duration_seconds,
            skipped_reason="ffmpeg_unavailable",
        )

    if process.returncode != 0 or not temp_path.exists():
        detail = (process.stderr or process.stdout or "").strip()
        if len(detail) > 500:
            detail = detail[-500:]
        get_logger().warning("audio duration trim failed for %s: %s", path, detail or f"exit={process.returncode}")
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return AudioDurationLimitResult(
            duration_seconds=duration,
            original_duration_seconds=duration,
            max_duration_seconds=max_duration_seconds,
            skipped_reason="trim_failed",
        )

    temp_path.replace(path)
    trimmed_duration = probe_audio_duration_seconds(path, ffmpeg_path=ffmpeg_path) or max_duration_seconds
    return AudioDurationLimitResult(
        duration_seconds=min(round(trimmed_duration, 3), max_duration_seconds),
        original_duration_seconds=duration,
        max_duration_seconds=max_duration_seconds,
        trimmed=True,
    )


def audio_duration_limit_metadata(result: AudioDurationLimitResult) -> dict[str, object]:
    payload: dict[str, object] = {}
    if result.duration_seconds is not None:
        payload["duration_seconds"] = result.duration_seconds
    if result.original_duration_seconds is not None:
        payload["original_duration_seconds"] = result.original_duration_seconds
    if result.max_duration_seconds is not None:
        payload["max_duration_seconds"] = result.max_duration_seconds
    if result.trimmed:
        payload["duration_limited"] = True
    if result.skipped_reason:
        payload["duration_limit_skipped_reason"] = result.skipped_reason
    return payload


def _positive_float(value: object, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _resolve_ffmpeg_path(ffmpeg_path: str) -> str | None:
    for candidate in _tool_candidates("ffmpeg", ffmpeg_path):
        resolved = shutil.which(candidate) if not Path(candidate).is_absolute() else candidate
        if resolved and Path(resolved).exists():
            return resolved
    return None


def _ffprobe_candidates(ffmpeg_path: str) -> list[str]:
    candidates: list[str] = []
    resolved_ffmpeg = _resolve_ffmpeg_path(ffmpeg_path)
    if resolved_ffmpeg:
        ffmpeg = Path(resolved_ffmpeg)
        candidates.append(str(ffmpeg.with_name("ffprobe" + ffmpeg.suffix)))
    candidates.extend(_tool_candidates("ffprobe", "ffprobe"))
    return _dedupe(candidates)


def _tool_candidates(tool_name: str, configured: str) -> list[str]:
    suffix = ".exe" if sys.platform.startswith("win") else ""
    candidates = [configured or tool_name, tool_name + suffix, tool_name]
    executable = Path(sys.executable)
    if executable.exists():
        env_dir = executable.parent
        if sys.platform.startswith("win"):
            candidates.append(str(env_dir / "Library" / "bin" / f"{tool_name}.exe"))
        candidates.append(str(env_dir / "bin" / tool_name))
    return _dedupe(candidates)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


__all__ = [
    "AudioDurationLimitResult",
    "DEFAULT_MAX_GENERATED_AUDIO_DURATION_SECONDS",
    "DEFAULT_ROLE_VOICE_PREVIEW_TEXT_CHARS",
    "audio_duration_limit_metadata",
    "compact_tts_text",
    "limit_audio_duration",
    "probe_audio_duration_seconds",
    "provider_max_generated_audio_duration_seconds",
    "provider_role_voice_preview_text_chars",
]
