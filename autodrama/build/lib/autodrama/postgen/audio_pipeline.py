from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from pydub import AudioSegment

from autodrama.config import RuntimeSettings, VoiceAlignmentSettings
from autodrama.editing.ffmpeg import ffmpeg_base_command, run_ffmpeg


async def _run_process(command: list[str], *, cwd: Path, label: str) -> None:
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
        detail = (process.stderr or process.stdout or "").strip()
        raise RuntimeError(f"{label} failed with exit code {process.returncode}: {detail[-4000:]}")


def _resolve_model_path(path: Path, *, config_dir: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    return candidate.resolve()


async def extract_audio(
    video_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *ffmpeg_base_command(ffmpeg_path),
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    await run_ffmpeg(command, cwd=output_path.parent)


async def separate_stems(
    audio_path: Path,
    *,
    output_dir: Path,
    settings: VoiceAlignmentSettings,
    runtime: RuntimeSettings,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    python_path = runtime.python.get("postgen") or runtime.python.get("default") or sys.executable
    command = [
        python_path,
        "-m",
        "demucs",
        "--two-stems=vocals",
        "-n",
        settings.demucs_model,
        "-d",
        settings.demucs_device,
        "-o",
        str(output_dir),
        str(audio_path),
    ]
    await _run_process(command, cwd=output_dir, label="Demucs separation")

    stem_dir = output_dir / settings.demucs_model / audio_path.stem
    source_vocals = stem_dir / "vocals.wav"
    source_background = stem_dir / "no_vocals.wav"
    if not source_vocals.exists() or not source_background.exists():
        raise FileNotFoundError(f"Demucs did not produce expected stems under {stem_dir}")
    vocals = output_dir / "vocal_raw.wav"
    background = output_dir / "bgm_sfx.wav"
    shutil.copyfile(source_vocals, vocals)
    shutil.copyfile(source_background, background)
    return vocals, background


def _diarize_sync(audio_path: Path, settings: VoiceAlignmentSettings) -> list[dict[str, Any]]:
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError(
            "pyannote.audio is not installed; install the postgen audio dependencies before enabling voice alignment"
        ) from exc

    token = os.getenv(settings.huggingface_token_env)
    if not token:
        raise RuntimeError(f"Missing Hugging Face token in {settings.huggingface_token_env}")
    try:
        pipeline = Pipeline.from_pretrained(settings.diarization_model, token=token)
    except TypeError:
        pipeline = Pipeline.from_pretrained(settings.diarization_model, use_auth_token=token)

    if settings.diarization_model and settings.demucs_device == "cuda":
        try:
            import torch

            pipeline.to(torch.device("cuda"))
        except Exception:
            pass

    kwargs: dict[str, Any] = {}
    if settings.min_speakers is not None:
        kwargs["min_speakers"] = settings.min_speakers
    if settings.max_speakers is not None:
        kwargs["max_speakers"] = settings.max_speakers
    diarization = pipeline(str(audio_path), **kwargs)
    annotation = getattr(diarization, "speaker_diarization", diarization)
    segments: list[dict[str, Any]] = []
    for turn, _track, speaker in annotation.itertracks(yield_label=True):
        start = round(float(turn.start), 3)
        end = round(float(turn.end), 3)
        if end <= start:
            continue
        segments.append({"start": start, "end": end, "speaker": str(speaker)})
    return sorted(segments, key=lambda item: (item["start"], item["end"], item["speaker"]))


async def diarize_audio(audio_path: Path, settings: VoiceAlignmentSettings) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_diarize_sync, audio_path, settings)


async def convert_and_rebuild_vocals(
    vocal_path: Path,
    segments: list[dict[str, Any]],
    *,
    output_path: Path,
    work_dir: Path,
    settings: VoiceAlignmentSettings,
    config_dir: Path,
    ffmpeg_path: str,
) -> list[dict[str, Any]]:
    if not settings.rvc_command:
        raise RuntimeError(
            "voice_alignment.rvc_command is empty; configure the installed RVC inference command with "
            "{input}, {output}, {model}, and optional {speaker} placeholders"
        )

    original = AudioSegment.from_file(vocal_path)
    canvas = AudioSegment.silent(duration=len(original), frame_rate=original.frame_rate).set_channels(original.channels)
    work_dir.mkdir(parents=True, exist_ok=True)
    converted_items: list[dict[str, Any]] = []
    padding = settings.segment_padding_ms

    for index, segment in enumerate(segments, start=1):
        speaker = str(segment["speaker"])
        model_ref = settings.speaker_rvc_models.get(speaker)
        if model_ref is None:
            if settings.fail_on_unmapped_speaker:
                raise RuntimeError(f"No RVC model configured for diarized speaker {speaker}")
            model_path = None
        else:
            model_path = _resolve_model_path(model_ref, config_dir=config_dir)
            if not model_path.exists():
                raise FileNotFoundError(f"RVC model for {speaker} is missing: {model_path}")

        core_start_ms = max(0, int(round(float(segment["start"]) * 1000)))
        core_end_ms = min(len(original), int(round(float(segment["end"]) * 1000)))
        start_ms = max(0, core_start_ms - padding)
        end_ms = min(len(original), core_end_ms + padding)
        input_slice = work_dir / f"{index:04d}_{speaker}_input.wav"
        output_slice = work_dir / f"{index:04d}_{speaker}_target.wav"
        original[start_ms:end_ms].export(input_slice, format="wav")

        if model_path is None:
            shutil.copyfile(input_slice, output_slice)
        else:
            replacements = {
                "input": str(input_slice),
                "output": str(output_slice),
                "model": str(model_path),
                "speaker": speaker,
            }
            command = [part.format_map(replacements) for part in settings.rvc_command]
            await _run_process(command, cwd=work_dir, label=f"RVC conversion for {speaker}")
        if not output_slice.exists():
            raise FileNotFoundError(f"RVC command did not create output: {output_slice}")

        converted = AudioSegment.from_file(output_slice)
        expected_ms = end_ms - start_ms
        if len(converted) > expected_ms:
            converted = converted[:expected_ms]
        elif len(converted) < expected_ms:
            converted += AudioSegment.silent(duration=expected_ms - len(converted), frame_rate=converted.frame_rate)
        left_context_ms = core_start_ms - start_ms
        core_duration_ms = core_end_ms - core_start_ms
        converted = converted[left_context_ms : left_context_ms + core_duration_ms]
        fade_ms = min(settings.crossfade_ms, max(0, len(converted) // 3))
        if fade_ms:
            converted = converted.fade_in(fade_ms).fade_out(fade_ms)
        canvas = canvas.overlay(converted, position=core_start_ms)
        converted_items.append(
            {
                "index": index,
                "speaker": speaker,
                "start": segment["start"],
                "end": segment["end"],
                "model": str(model_path) if model_path else None,
                "slice": str(output_slice),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.export(output_path, format="wav")
    return converted_items


async def mix_and_mux(
    video_path: Path,
    vocal_path: Path,
    background_path: Path,
    output_path: Path,
    *,
    settings: VoiceAlignmentSettings,
    ffmpeg_path: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[1:a]volume={settings.vocals_gain_db}dB,aresample=48000[v];"
        f"[2:a]volume={settings.background_gain_db}dB,aresample=48000[b];"
        "[v][b]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.97,apad[aout]"
    )
    command = [
        *ffmpeg_base_command(ffmpeg_path),
        "-i",
        str(video_path),
        "-i",
        str(vocal_path),
        "-i",
        str(background_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await run_ffmpeg(command, cwd=output_path.parent)


__all__ = [
    "convert_and_rebuild_vocals",
    "diarize_audio",
    "extract_audio",
    "mix_and_mux",
    "separate_stems",
]
