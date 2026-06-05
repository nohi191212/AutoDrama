from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import numpy as np

try:
    from moviepy import VideoFileClip
except ImportError:  # pragma: no cover
    from moviepy.editor import VideoFileClip  # type: ignore


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider  # noqa: E402


DEFAULT_PROMPT = (
    "A four-second cinematic 720p video test. A small bronze bell sits on a clean stone table in a quiet studio. "
    "The camera slowly pushes in while a soft hand taps the bell once, making a clear gentle chime. "
    "Include audible room tone, a subtle tap sound, and the bell chime. No dialogue, no subtitles, no text, no watermark."
)


def read_u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def iter_boxes(data: bytes, start: int, end: int):
    offset = start
    while offset + 8 <= end:
        size = read_u32(data, offset)
        box_type = data[offset + 4 : offset + 8].decode("latin1", errors="replace")
        header = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            break
        yield box_type, offset, offset + size, offset + header
        offset += size


def find_child(data: bytes, start: int, end: int, wanted: str):
    for box_type, box_start, box_end, content_start in iter_boxes(data, start, end):
        if box_type == wanted:
            return box_start, box_end, content_start
    return None


def mp4_handlers(path: Path) -> list[str]:
    data = path.read_bytes()
    moov = find_child(data, 0, len(data), "moov")
    if not moov:
        return []
    _, moov_end, moov_content = moov
    handlers: list[str] = []
    for box_type, trak_start, trak_end, trak_content in iter_boxes(data, moov_content, moov_end):
        if box_type != "trak":
            continue
        mdia = find_child(data, trak_content, trak_end, "mdia")
        if not mdia:
            continue
        _, mdia_end, mdia_content = mdia
        hdlr = find_child(data, mdia_content, mdia_end, "hdlr")
        if not hdlr:
            continue
        _, hdlr_end, hdlr_content = hdlr
        if hdlr_content + 12 <= hdlr_end:
            handlers.append(data[hdlr_content + 8 : hdlr_content + 12].decode("latin1", errors="replace"))
    return handlers


def audio_level(path: Path) -> dict[str, Any]:
    with VideoFileClip(str(path)) as clip:
        if clip.audio is None:
            return {"has_audio": False}
        sample_rate = 16000
        duration = float(clip.audio.duration or clip.duration or 0)
        values = []
        for start in np.arange(0, duration, 1.0):
            end = min(float(start + 1.0), duration)
            samples = clip.audio.subclipped(float(start), end).to_soundarray(fps=sample_rate)
            if samples.size:
                values.append(samples.astype("float32"))
        if not values:
            return {"has_audio": True, "duration": duration, "samples": 0}
        audio = np.concatenate(values, axis=0)
        return {
            "has_audio": True,
            "duration": duration,
            "peak": float(np.max(np.abs(audio))),
            "rms": float(np.sqrt(np.mean(np.square(audio)))),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a real 4s 720p Seedance smoke video.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--output-dir", default=str(ROOT_DIR / ".tmp" / "seedance_4s_720p_smoke"))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--filename", default="seedance_4s_720p.mp4")
    return parser


async def download_video(url: str, output_path: Path, timeout_seconds: int) -> None:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url)
    response.raise_for_status()
    output_path.write_bytes(response.content)


async def main_async() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    provider_settings = settings.providers["volcengine"].model_copy(deep=True)
    provider_settings.options["generate_audio"] = True
    provider_settings.options["video_resolution"] = "720p"
    provider_settings.options["video_min_duration_seconds"] = 4
    provider_settings.options["video_max_duration_seconds"] = max(
        int(provider_settings.options.get("video_max_duration_seconds", 10)),
        4,
    )
    provider = VolcengineSeedanceVideoProvider(provider_settings, settings.runtime)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / args.filename
    result_json_path = output_dir / "seedance_4s_720p_result.json"
    report_path = output_dir / "seedance_4s_720p_report.json"

    result = await provider.generate_video(
        args.prompt,
        duration=4,
        wait=True,
        metadata={
            "resolution": "720p",
            "video_ratio": "16:9",
            "generate_audio": True,
            "return_last_frame": True,
        },
    )
    result_json_path.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    if not result.video_url:
        raise RuntimeError(f"Seedance task completed without video_url: {result.model_dump(mode='json')}")

    await download_video(result.video_url, output_path, int(settings.runtime.request_timeout_seconds))
    handlers = mp4_handlers(output_path)
    level = audio_level(output_path)
    report = {
        "video_path": str(output_path),
        "result_json_path": str(result_json_path),
        "task_id": result.task_id,
        "task_status": result.task_status,
        "request_id": result.request_id,
        "tracks": handlers,
        "has_video": "vide" in handlers,
        "has_audio_track": "soun" in handlers,
        "audio_level": level,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("seedance_4s_720p_video_smoke=ok")
    print(f"video_path={output_path}")
    print(f"report_path={report_path}")
    print(f"task_id={result.task_id or '-'} status={result.task_status or '-'}")
    print(f"tracks={','.join(handlers) or '-'}")
    print(f"audio_peak={level.get('peak', '-')}")
    print(f"audio_rms={level.get('rms', '-')}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
