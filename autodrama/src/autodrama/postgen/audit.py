from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import subprocess
from typing import Any

from autodrama.config import AuditSettings
from autodrama.editing.ffmpeg import ffmpeg_base_command, run_ffmpeg
from autodrama.postgen.schemas import (
    PostgenFinalAuditReport,
    PostgenSourceAuditReport,
    PostgenSourceClip,
)
from autodrama.providers.base import AssetRef, TextLLM


async def extract_contact_sheet(
    video_path: Path,
    output_path: Path,
    *,
    duration_seconds: float,
    frame_count: int,
    ffmpeg_path: str,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = max(1, frame_count)
    columns = min(4, count)
    rows = int(math.ceil(count / columns))
    fps = count / max(0.2, duration_seconds)
    vf = f"fps={fps:.8f},scale=320:-2,tile={columns}x{rows}:nb_frames={count}:padding=4:margin=4"
    command = [
        *ffmpeg_base_command(ffmpeg_path),
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(output_path),
    ]
    await run_ffmpeg(command, cwd=output_path.parent)
    return output_path


async def extract_audit_audio(video_path: Path, output_path: Path, *, ffmpeg_path: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *ffmpeg_base_command(ffmpeg_path),
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        str(output_path),
    ]
    await run_ffmpeg(command, cwd=output_path.parent)
    return output_path


async def extract_audit_video(video_path: Path, output_path: Path, *, ffmpeg_path: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *ffmpeg_base_command(ffmpeg_path),
        "-i",
        str(video_path),
        "-vf",
        "scale=360:-2,fps=8",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "32",
        "-c:a",
        "aac",
        "-b:a",
        "48k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await run_ffmpeg(command, cwd=output_path.parent)
    return output_path


async def probe_media(path: Path, *, ffmpeg_path: str) -> dict[str, Any]:
    ffmpeg = Path(ffmpeg_path)
    candidates = [str(ffmpeg.with_name("ffprobe" + ffmpeg.suffix)), "ffprobe"]
    for ffprobe in dict.fromkeys(candidates):
        process = await asyncio.to_thread(
            subprocess.run,
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode == 0:
            try:
                payload = json.loads(process.stdout)
            except ValueError:
                continue
            streams = payload.get("streams", [])
            return {
                "duration_seconds": float(payload.get("format", {}).get("duration") or 0.0),
                "format": payload.get("format", {}).get("format_name"),
                "video": [
                    {
                        "codec": item.get("codec_name"),
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "fps": item.get("avg_frame_rate"),
                    }
                    for item in streams
                    if item.get("codec_type") == "video"
                ],
                "audio": [
                    {
                        "codec": item.get("codec_name"),
                        "sample_rate": item.get("sample_rate"),
                        "channels": item.get("channels"),
                    }
                    for item in streams
                    if item.get("codec_type") == "audio"
                ],
            }
    return {}


async def audit_source_clips(
    provider: TextLLM,
    *,
    episode_key: str,
    clips: list[PostgenSourceClip],
    project_dir: Path,
    work_dir: Path,
    settings: AuditSettings,
    ffmpeg_path: str,
) -> PostgenSourceAuditReport:
    refs: list[AssetRef] = []
    clip_payload: list[dict[str, Any]] = []
    per_clip_frames = max(3, settings.frame_count // max(1, len(clips)))
    for clip in clips:
        source_path = project_dir / clip.source_path if not Path(clip.source_path).is_absolute() else Path(clip.source_path)
        sheet = await extract_contact_sheet(
            source_path,
            work_dir / f"{clip.shot_id}_contact.jpg",
            duration_seconds=clip.duration_seconds,
            frame_count=per_clip_frames,
            ffmpeg_path=ffmpeg_path,
        )
        refs.append(AssetRef(id=clip.shot_id, type="image", path=str(sheet)))
        clip_payload.append(
            {
                "shot_id": clip.shot_id,
                "duration_seconds": clip.duration_seconds,
                "dialogue_lines": clip.dialogue_lines,
                "content": clip.content,
                "contact_sheet_ref": clip.shot_id,
            }
        )

    prompt = (
        "你是短剧源素材审计员。每张联系表按时间从左到右、从上到下展示对应镜头。"
        "检查人物/道具/场景连续性、AI生成伪影、黑帧、画面突变和镜头头尾可剪区间。"
        "此阶段没有提供连续音频，不要对音质或口型同步下结论；这些项目由最终成片审计负责。"
        "只基于可见证据，不臆测；时间必须落在对应素材时长内。"
        "verdict=pass 表示可直接用，trim 表示需按 usable_start/usable_end 裁剪，reject 表示整段不可用。"
        "返回结构化 JSON，并为每个 shot_id 恰好返回一项。\n\n"
        f"素材：{json.dumps(clip_payload, ensure_ascii=False, indent=2)}"
    )
    return await provider.generate_json(
        prompt,
        PostgenSourceAuditReport,
        temperature=0.1,
        refs=refs,
        metadata={
            "node_name": "postgen_source_audit",
            "episode_key": episode_key,
            "max_output_tokens": settings.max_output_tokens,
        },
    )


async def audit_final_video(
    provider: TextLLM,
    *,
    episode_key: str,
    video_path: Path,
    transcript: dict[str, Any] | None,
    work_dir: Path,
    settings: AuditSettings,
    ffmpeg_path: str,
) -> PostgenFinalAuditReport:
    metrics = await probe_media(video_path, ffmpeg_path=ffmpeg_path)
    duration = float(metrics.get("duration_seconds") or 1.0)
    contact = await extract_contact_sheet(
        video_path,
        work_dir / "final_contact.jpg",
        duration_seconds=duration,
        frame_count=settings.frame_count,
        ffmpeg_path=ffmpeg_path,
    )
    refs = [AssetRef(id="final_contact_sheet", type="image", path=str(contact))]
    if settings.include_audio:
        audit_video = await extract_audit_video(video_path, work_dir / "final_audit_video.mp4", ffmpeg_path=ffmpeg_path)
        refs.append(AssetRef(id="final_audit_video", type="video", path=str(audit_video)))

    transcript_segments = []
    if transcript:
        transcript_segments = transcript.get("segments", [])
    prompt = (
        "你是最终成片质检审计员。联系表按时间从左到右、从上到下展示成片。"
        + ("另附含音频的完整低码率审计视频，可用于检查连续运动、口型和声音。" if settings.include_audio else "本次未附连续视频，不要判断音质或口型同步。")
        "综合检查剪辑节奏和连续性、画面AI伪影、音色跨角色一致性、爆音/电音/断裂、口型同步、"
        "字幕错字与遮挡、音画时长、黑帧和安全风险。不要把联系表格线当作画面缺陷。"
        "fatal/error 问题应导致 fail；只有轻微 warning 可返回 warn；无实质问题返回 pass。"
        "repair_actions 必须是可执行的后期修复建议。只返回结构化 JSON。\n\n"
        f"媒体探测：{json.dumps(metrics, ensure_ascii=False)}\n"
        f"ASR时间轴：{json.dumps(transcript_segments, ensure_ascii=False)}"
    )
    return await provider.generate_json(
        prompt,
        PostgenFinalAuditReport,
        temperature=0.1,
        refs=refs,
        metadata={
            "node_name": "postgen_final_audit",
            "episode_key": episode_key,
            "max_output_tokens": settings.max_output_tokens,
        },
    )


__all__ = [
    "audit_final_video",
    "audit_source_clips",
    "extract_audit_audio",
    "extract_audit_video",
    "extract_contact_sheet",
    "probe_media",
]
