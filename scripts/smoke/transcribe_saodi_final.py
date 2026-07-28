from __future__ import annotations

import json
from pathlib import Path

from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parents[2]
VIDEO = (
    ROOT
    / "outputs"
    / "saodi_bashinian_terra_image2"
    / "outputs"
    / "videos"
    / "episode_001_postgen_all33.mp4"
)
OUTPUT = ROOT / ".tmp" / "audit_saodi_bashinian_terra_image2" / "final_asr_tiny.json"


def main() -> None:
    model = WhisperModel(
        "Systran/faster-whisper-tiny",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )
    segments, info = model.transcribe(
        str(VIDEO),
        language="zh",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    rows = [
        {
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": segment.text.strip(),
        }
        for segment in segments
    ]
    payload = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
