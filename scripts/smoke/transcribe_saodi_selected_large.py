from __future__ import annotations

import json
from pathlib import Path

from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = (
    ROOT
    / "outputs"
    / "saodi_bashinian_terra_image2"
    / "assets"
    / "videos"
    / "shots"
)
OUTPUT = ROOT / ".tmp" / "audit_saodi_bashinian_terra_image2" / "selected_asr_large.json"
TARGETS = [
    "episode_001_clip_002_shot_012_video.mp4",
    "episode_001_clip_002_shot_015_video.mp4",
    "episode_001_clip_003_shot_004_video.mp4",
]


def main() -> None:
    model = WhisperModel(
        "Systran/faster-whisper-large-v3",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )
    payload = {}
    for name in TARGETS:
        path = SHOT_DIR / name
        segments, info = model.transcribe(
            str(path),
            language="zh",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        payload[name] = {
            "duration": info.duration,
            "segments": [
                {
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "text": segment.text.strip(),
                }
                for segment in segments
            ],
        }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
