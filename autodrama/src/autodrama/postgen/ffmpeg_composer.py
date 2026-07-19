from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

from autodrama.editing import ffmpeg as ffmpeg_tools
from autodrama.postgen.edit_plan_validator import estimate_timeline_duration, resolve_project_path
from autodrama.postgen.schemas import PostgenCompositionItem, PostgenEditPlan, PostgenTimelineItem


def _project_relative(project_dir: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_dir.resolve())).replace("\\", "/")


def _ffprobe_candidates(ffmpeg_path: str) -> list[str]:
    candidates: list[str] = []
    ffmpeg = Path(ffmpeg_path)
    candidates.append(str(ffmpeg.with_name("ffprobe" + ffmpeg.suffix)))
    candidates.append("ffprobe")
    return list(dict.fromkeys(candidates))


async def probe_video_duration_seconds(path: Path, *, ffmpeg_path: str = "ffmpeg") -> float | None:
    for candidate in _ffprobe_candidates(ffmpeg_path):
        process = await asyncio.to_thread(
            subprocess.run,
            [
                candidate,
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
        if process.returncode != 0:
            continue
        try:
            return float((process.stdout or "").strip())
        except ValueError:
            continue
    return None


async def probe_has_audio(path: Path, *, ffmpeg_path: str = "ffmpeg") -> bool:
    for candidate in _ffprobe_candidates(ffmpeg_path):
        process = await asyncio.to_thread(
            subprocess.run,
            [candidate, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode == 0:
            return bool((process.stdout or "").strip())
    return False


class PostgenFfmpegComposer:
    def __init__(self, *, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    async def compose(
        self,
        project_dir: Path,
        plan: PostgenEditPlan,
        *,
        tmp_dir: Path,
        validated_plan_path: Path,
    ) -> PostgenCompositionItem:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        sources = {clip.shot_id: clip for clip in plan.source_clips}
        normalized_paths: list[Path] = []
        for index, item in enumerate(plan.timeline, start=1):
            source_clip = sources[item.shot_id]
            source_path = resolve_project_path(project_dir, source_clip.source_path)
            if not source_path.exists():
                raise FileNotFoundError(f"postgen source video missing for {item.shot_id}: {source_path}")
            cut_path = tmp_dir / "cuts" / f"{index:03d}_{ffmpeg_tools.safe_filename(item.clip_id)}.mp4"
            await self._normalize_cut(project_dir, plan, item, source_path, cut_path)
            normalized_paths.append(cut_path)

        concat_path = tmp_dir / "concat.txt"
        base_output_path = tmp_dir / "video_concat.mp4"
        ffmpeg_tools.write_concat_file(concat_path, normalized_paths)
        await self._concat(concat_path, base_output_path)

        output_path = resolve_project_path(project_dir, plan.output.path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await self._finalize_video(base_output_path, output_path)
        actual_duration = await probe_video_duration_seconds(output_path, ffmpeg_path=self.ffmpeg_path)
        expected_duration = estimate_timeline_duration(plan)
        return PostgenCompositionItem(
            episode_key=plan.episode_key,
            output_video_path=_project_relative(project_dir, output_path),
            validated_plan_path=_project_relative(project_dir, validated_plan_path),
            estimated_duration_seconds=expected_duration,
            actual_duration_seconds=round(actual_duration, 3) if actual_duration is not None else None,
            clip_count=len(plan.timeline),
            ffmpeg_path=self.ffmpeg_path,
        )

    async def _normalize_cut(
        self,
        project_dir: Path,
        plan: PostgenEditPlan,
        item: PostgenTimelineItem,
        source_path: Path,
        output_path: Path,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_duration = item.source_out - item.source_in
        output_duration = source_duration / item.speed
        vf = (
            f"trim=start={ffmpeg_tools.ffmpeg_seconds(item.source_in)}:"
            f"duration={ffmpeg_tools.ffmpeg_seconds(source_duration)},"
            f"setpts=(PTS-STARTPTS)/{item.speed:.6f},"
            f"scale={plan.output.width}:{plan.output.height}:force_original_aspect_ratio=increase,"
            f"crop={plan.output.width}:{plan.output.height},setsar=1,"
            f"fps={plan.output.fps},format=yuv420p"
        )
        has_audio = plan.output.audio and await probe_has_audio(source_path, ffmpeg_path=self.ffmpeg_path)
        command = [*ffmpeg_tools.ffmpeg_base_command(self.ffmpeg_path), "-i", str(source_path)]
        if has_audio:
            af = (
                f"atrim=start={ffmpeg_tools.ffmpeg_seconds(item.source_in)}:"
                f"duration={ffmpeg_tools.ffmpeg_seconds(source_duration)},"
                f"asetpts=PTS-STARTPTS,atempo={item.speed:.6f},"
                "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            )
            command.extend(["-vf", vf, "-af", af, "-map", "0:v:0", "-map", "0:a:0"])
        else:
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    ffmpeg_tools.ffmpeg_seconds(output_duration),
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf",
                    vf,
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                ]
            )
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        await ffmpeg_tools.run_ffmpeg(command, cwd=project_dir)

    async def _concat(self, concat_path: Path, output_path: Path) -> None:
        command = [
            *ffmpeg_tools.ffmpeg_base_command(self.ffmpeg_path),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(output_path),
        ]
        await ffmpeg_tools.run_ffmpeg(command, cwd=concat_path.parent)

    async def _finalize_video(self, source_path: Path, output_path: Path) -> None:
        command = [
            *ffmpeg_tools.ffmpeg_base_command(self.ffmpeg_path),
            "-i",
            str(source_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        await ffmpeg_tools.run_ffmpeg(command, cwd=output_path.parent)


__all__ = ["PostgenFfmpegComposer", "probe_has_audio", "probe_video_duration_seconds"]
