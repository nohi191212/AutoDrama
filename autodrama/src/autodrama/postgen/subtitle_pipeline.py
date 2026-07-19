from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

from autodrama.config import SubtitleSettings
from autodrama.editing.ffmpeg import ffmpeg_base_command, filter_path, run_ffmpeg
from autodrama.editing.subtitles import write_ass, write_srt
from autodrama.postgen.schemas import PostgenSubtitleCue


def _load_whisperx_sync(audio_path: Path, settings: SubtitleSettings) -> dict[str, Any]:
    try:
        import whisperx
    except ImportError as exc:
        raise RuntimeError(
            "whisperx is not installed; install the postgen audio dependencies before enabling subtitles"
        ) from exc

    audio = whisperx.load_audio(str(audio_path))
    model = whisperx.load_model(
        settings.model,
        settings.device,
        compute_type=settings.compute_type,
        language=settings.language or None,
    )
    result = model.transcribe(audio, batch_size=settings.batch_size)
    language = str(result.get("language") or settings.language)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=settings.device)
    aligned = whisperx.align(
        result.get("segments", []),
        align_model,
        metadata,
        audio,
        settings.device,
        return_char_alignments=False,
    )
    aligned["language"] = language
    return aligned


async def transcribe(audio_path: Path, settings: SubtitleSettings) -> dict[str, Any]:
    if settings.backend == "sidecar":
        sidecar_path = audio_path.with_suffix(".whisperx.json")
        if not sidecar_path.exists():
            raise FileNotFoundError(f"WhisperX sidecar transcript is missing: {sidecar_path}")
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    return await asyncio.to_thread(_load_whisperx_sync, audio_path, settings)


def _clean_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", text)
    return text


def _chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for token in re.split(r"(?<=[，。！？；：、,.!?;:])", text):
        token = token.strip()
        if not token:
            continue
        while len(token) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(token[:max_chars])
            token = token[max_chars:]
        if current and len(current) + len(token) > max_chars:
            pieces.append(current)
            current = token
        else:
            current += token
    if current:
        pieces.append(current)
    return pieces or [text]


def _join_tokens(tokens: list[str]) -> str:
    text = ""
    for token in tokens:
        token = _clean_text(token)
        if not token:
            continue
        needs_space = bool(text and text[-1].isascii() and text[-1].isalnum() and token[0].isascii() and token[0].isalnum())
        text += (" " if needs_space else "") + token
    return text


def _word_level_cues(segment: dict[str, Any], settings: SubtitleSettings) -> list[tuple[float, float, str]]:
    words = [item for item in segment.get("words", []) if isinstance(item, dict)]
    timed = [
        item
        for item in words
        if item.get("start") is not None and item.get("end") is not None and _clean_text(str(item.get("word") or ""))
    ]
    if not timed:
        return []
    max_chars = settings.max_chars_per_line * settings.max_lines
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in timed:
        candidate = _join_tokens([str(value.get("word") or "") for value in [*current, item]])
        if current and len(candidate) > max_chars:
            groups.append(current)
            current = [item]
        else:
            current.append(item)
        if current and re.search(r"[。！？.!?]$", str(item.get("word") or "")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [
        (
            float(group[0]["start"]),
            float(group[-1]["end"]),
            _join_tokens([str(item.get("word") or "") for item in group]),
        )
        for group in groups
    ]


def build_cues(result: dict[str, Any], settings: SubtitleSettings) -> list[PostgenSubtitleCue]:
    cues: list[PostgenSubtitleCue] = []
    for segment in result.get("segments", []):
        if not isinstance(segment, dict):
            continue
        text = _clean_text(str(segment.get("text") or ""))
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        if not text or end <= start:
            continue
        word_cues = _word_level_cues(segment, settings)
        if word_cues:
            for word_start, word_end, word_text in word_cues:
                rendered = "\n".join(_chunks(word_text, settings.max_chars_per_line)[: settings.max_lines])
                cues.append(
                    PostgenSubtitleCue(
                        index=len(cues) + 1,
                        start_time=round(word_start, 3),
                        end_time=round(max(word_start + 0.08, word_end), 3),
                        text=rendered,
                    )
                )
            continue
        chunks = _chunks(text, settings.max_chars_per_line * settings.max_lines)
        total_chars = max(1, sum(len(chunk) for chunk in chunks))
        cursor = start
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index == len(chunks) - 1:
                chunk_end = end
            else:
                chunk_end = cursor + (end - start) * len(chunk) / total_chars
            line_parts = _chunks(chunk, settings.max_chars_per_line)
            rendered = "\n".join(line_parts[: settings.max_lines])
            cues.append(
                PostgenSubtitleCue(
                    index=len(cues) + 1,
                    start_time=round(cursor, 3),
                    end_time=round(max(cursor + 0.08, chunk_end), 3),
                    text=rendered,
                )
            )
            cursor = chunk_end
    return cues


def write_subtitle_files(
    cues: list[PostgenSubtitleCue],
    *,
    srt_path: Path,
    ass_path: Path,
    width: int,
    height: int,
    settings: SubtitleSettings,
) -> None:
    write_srt(srt_path, cues)
    plan = SimpleNamespace(width=width, height=height, subtitle_cues=cues)
    write_ass(
        ass_path,
        plan,
        font_name=settings.font_name,
        font_size=settings.font_size,
        margin_v=settings.margin_v,
    )


async def burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *ffmpeg_base_command(ffmpeg_path),
        "-i",
        str(video_path),
        "-vf",
        f"subtitles={filter_path(ass_path)}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await run_ffmpeg(command, cwd=output_path.parent)


__all__ = ["build_cues", "burn_subtitles", "transcribe", "write_subtitle_files"]
