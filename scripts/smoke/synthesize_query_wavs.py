from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import random
import re
import sys
import time
import wave
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "autodrama" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.providers.volcengine.audio.seed_tts import VolcengineSeedTTSProvider  # noqa: E402


SECRET_PATTERNS = (
    re.compile(r"('X-Api-(?:Key|Access-Key)'\s*:\s*')([^']+)(')", re.IGNORECASE),
    re.compile(r'("X-Api-(?:Key|Access-Key)"\s*:\s*")([^"]+)(")', re.IGNORECASE),
)

CHILD_VOICE_KEYWORDS = (
    "儿童",
    "少儿",
    "童声",
    "小孩",
    "孩子",
    "小朋友",
    "少年",
    "少女",
    "男孩",
    "女孩",
    "佩奇",
    "熊二",
    "孙悟空",
    "海绵",
    "樱桃丸子",
    "少儿故事",
    "天才童声",
    "萌丫头",
    "xiaoxue",
    "shaonian",
    "shaoer",
    "tongsheng",
    "peiqi",
    "xionger",
    "sunwukong",
    "haimian",
    "yingtaowanzi",
    "mengyatou",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synthesize each line in .tmp/query.txt as one WAV with random official TTS voices."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", default=".tmp/query.txt")
    parser.add_argument("--output-dir", default=".tmp/query_wavs")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true", help="Keep existing non-empty wav files.")
    return parser.parse_args()


def sanitize_error(error: BaseException | str) -> str:
    text = str(error)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(r"\1<secret omitted>\3", text)
    return text[:1200]


def is_chinese_adult_voice(speaker: dict[str, Any]) -> bool:
    voice_type = str(speaker.get("voice_type") or "")
    language = str(speaker.get("language") or "")
    if not (voice_type.startswith("zh_") or voice_type.startswith("multi_zh") or "中文" in language):
        return False

    haystack_parts = [
        speaker.get("name"),
        speaker.get("voice_type"),
        speaker.get("scene"),
        speaker.get("language"),
        " ".join(str(item) for item in speaker.get("tags") or []),
        " ".join(str(item) for item in speaker.get("abilities") or []),
    ]
    haystack = " ".join(str(part or "") for part in haystack_parts).lower()
    return not any(keyword.lower() in haystack for keyword in CHILD_VOICE_KEYWORDS)


def selected_speakers() -> list[dict[str, Any]]:
    speakers = VolcengineSeedTTSProvider.available_speakers()
    seed_tts_2 = [
        speaker
        for speaker in speakers
        if speaker.get("resource_id") == "seed-tts-2.0" and is_chinese_adult_voice(speaker)
    ]
    if seed_tts_2:
        return seed_tts_2
    return [speaker for speaker in speakers if is_chinese_adult_voice(speaker)] or speakers


def result_audio_bytes(audio_data: str) -> bytes:
    payload = audio_data.split(";base64,", 1)[1] if audio_data.startswith("data:") and ";base64," in audio_data else audio_data
    return base64.b64decode(payload)


def write_silence_wav(path: Path, *, sample_rate: int = 24000, duration_seconds: float = 0.25) -> None:
    frame_count = max(1, int(sample_rate * duration_seconds))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frame_count)


def output_name(line_number: int) -> str:
    return f"line_{line_number:04d}.wav"


async def synthesize_one(
    *,
    provider: Any,
    line_number: int,
    text: str,
    speaker: dict[str, Any],
    output_path: Path,
    max_retries: int,
) -> dict[str, Any]:
    voice_type = str(speaker.get("voice_type") or "")
    resource_id = str(speaker.get("resource_id") or "seed-tts-2.0")
    record: dict[str, Any] = {
        "line_number": line_number,
        "file": output_path.as_posix(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_chars": len(text),
        "voice_type": voice_type,
        "voice_name": speaker.get("name"),
        "resource_id": resource_id,
    }

    if text == "":
        write_silence_wav(output_path)
        record.update({"status": "empty_text_silence", "attempts": 0})
        return record

    last_error = ""
    for attempt in range(1, max_retries + 2):
        try:
            result = await provider.synthesize_speech(
                voice=voice_type,
                text=text,
                metadata={
                    "resource_id": resource_id,
                    "response_format": "wav",
                    "sample_rate": 24000,
                    "emotion": "normal",
                    "instruction_mode": "none",
                    "uid": "autodrama-query-eval",
                },
            )
            if not result.audio_data:
                raise RuntimeError("TTS result did not contain audio_data")
            audio_bytes = result_audio_bytes(result.audio_data)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio_bytes)
            record.update(
                {
                    "status": "ok",
                    "attempts": attempt,
                    "audio_format": result.audio_format,
                    "sample_rate": result.audio_sample_rate,
                    "request_id": result.request_id,
                    "bytes": len(audio_bytes),
                    "usage": result.usage,
                }
            )
            return record
        except Exception as exc:  # noqa: BLE001 - continue batch and record provider failures.
            last_error = sanitize_error(exc)
            if attempt <= max_retries:
                await asyncio.sleep(min(2.0 * attempt, 5.0))

    record.update({"status": "failed", "attempts": max_retries + 1, "error": last_error})
    return record


async def run_batch(args: argparse.Namespace) -> int:
    config_path = (REPO_ROOT / args.config).resolve()
    input_path = (REPO_ROOT / args.input).resolve()
    output_dir = (REPO_ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.jsonl"
    summary_path = output_dir / "summary.json"

    lines = input_path.read_text(encoding="utf-8").splitlines()
    speakers = selected_speakers()
    if not speakers:
        raise RuntimeError("No TTS speakers are available")

    seed = args.seed if args.seed is not None else time.time_ns()
    rng = random.Random(seed)
    assignments = [rng.choice(speakers) for _ in lines]

    settings = load_settings(config_path)
    router = ProviderRouter(settings)
    provider = router.audio("speech")
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    manifest_lock = asyncio.Lock()
    counts: dict[str, int] = {}

    manifest_path.write_text("", encoding="utf-8")

    async def worker(index: int, text: str, speaker: dict[str, Any]) -> dict[str, Any]:
        line_number = index + 1
        output_path = output_dir / output_name(line_number)
        if args.resume and output_path.exists() and output_path.stat().st_size > 0:
            record = {
                "line_number": line_number,
                "file": output_path.as_posix(),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text_chars": len(text),
                "status": "existing",
                "bytes": output_path.stat().st_size,
            }
        else:
            async with semaphore:
                record = await synthesize_one(
                    provider=provider,
                    line_number=line_number,
                    text=text,
                    speaker=speaker,
                    output_path=output_path,
                    max_retries=max(0, args.max_retries),
                )

        async with manifest_lock:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
            with manifest_path.open("a", encoding="utf-8") as manifest_file:
                manifest_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f"[{line_number:04d}/{len(lines):04d}] {record['status']} "
                f"{Path(record['file']).name}",
                flush=True,
            )
        return record

    records = await asyncio.gather(
        *(worker(index, text, speaker) for index, (text, speaker) in enumerate(zip(lines, assignments, strict=True)))
    )
    summary = {
        "input": input_path.as_posix(),
        "output_dir": output_dir.as_posix(),
        "manifest": manifest_path.as_posix(),
        "line_count": len(lines),
        "speaker_pool_count": len(speakers),
        "random_seed": seed,
        "counts": counts,
        "failed_lines": [record["line_number"] for record in records if record["status"] == "failed"],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if not summary["failed_lines"] else 2


def main() -> int:
    args = parse_args()
    return asyncio.run(run_batch(args))


if __name__ == "__main__":
    raise SystemExit(main())
