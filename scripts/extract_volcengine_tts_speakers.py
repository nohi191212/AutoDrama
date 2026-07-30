from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT_DIR / "provider_docs" / "volcengine_tts.md"
OUTPUT_PATH = (
    ROOT_DIR
    / "autodrama"
    / "src"
    / "autodrama"
    / "providers"
    / "volcengine"
    / "audio"
    / "seed_tts_speakers.json"
)

VOICE_TYPE_PATTERN = re.compile(r"^(?:ICL_|saturn_|zh_|en_|multi_)[A-Za-z0-9_]+$")

EMOTION_ALIASES = {
    "开心": "happy",
    "愉悦": "happy",
    "悲伤": "sad",
    "生气": "angry",
    "愤怒": "angry",
    "惊讶": "surprised",
    "恐惧": "fear",
    "厌恶": "hate",
    "激动": "excited",
    "兴奋": "excited",
    "冷漠": "coldness",
    "中性": "neutral",
    "沮丧": "depressed",
    "撒娇": "lovey-dovey",
    "害羞": "shy",
    "安慰鼓励": "comfort",
    "咆哮/焦急": "tension",
    "焦急": "tension",
    "温柔": "tender",
    "讲故事": "storytelling",
    "自然讲述": "storytelling",
    "情感电台": "radio",
    "磁性": "magnetic",
    "广告营销": "advertising",
    "气泡音": "vocal-fry",
    "低语": "ASMR",
    "新闻播报": "news",
    "娱乐八卦": "entertainment",
    "方言": "dialect",
    "对话 / 闲聊": "chat",
    "对话/闲聊": "chat",
    "闲聊": "chat",
    "温暖": "warm",
    "深情": "affectionate",
    "权威": "authoritative",
    "ASMR": "ASMR",
}


def clean_cell(value: str) -> str:
    value = value.replace("\\", "").replace("**", "").replace("\u200b", "").strip()
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


def split_row(line: str) -> list[str]:
    value = line.rstrip().rstrip("\\").strip()
    if not value.startswith("|"):
        return []
    parts = [clean_cell(part) for part in value.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def split_list(value: str) -> list[str]:
    if not value:
        return []
    normalized = value.replace("，", "、").replace(",", "、")
    return [item.strip() for item in normalized.split("、") if item.strip()]


def normalize_emotions(value: str) -> list[str]:
    emotions: list[str] = []
    for item in split_list(value):
        normalized = EMOTION_ALIASES.get(item, item)
        if normalized and normalized not in emotions:
            emotions.append(normalized)
    return emotions


def gender_from_voice_type(voice_type: str) -> str | None:
    lowered = voice_type.lower()
    if "_female_" in lowered or lowered.startswith("en_female_") or lowered.startswith("multi_female_"):
        return "female"
    if "_male_" in lowered or lowered.startswith("en_male_") or lowered.startswith("multi_male_"):
        return "male"
    return None


def append_speaker(
    speakers: list[dict[str, Any]],
    seen: set[str],
    *,
    resource_id: str,
    model_family: str,
    scene: str,
    name: str,
    voice_type: str,
    language: str,
    abilities: list[str],
    supported_emotions: list[str],
    tags: list[str],
    corresponding_2_0_voice: str | None = None,
    supports_mix: bool | None = None,
) -> None:
    if voice_type in seen:
        return
    seen.add(voice_type)
    speakers.append(
        {
            "name": name,
            "voice_type": voice_type,
            "resource_id": resource_id,
            "model_family": model_family,
            "scene": scene,
            "language": language,
            "gender": gender_from_voice_type(voice_type),
            "abilities": abilities,
            "emotion_capable": bool(supported_emotions) or "情感变化" in abilities,
            "supported_emotions": supported_emotions,
            "tags": tags,
            "corresponding_2_0_voice": corresponding_2_0_voice,
            "supports_mix": supports_mix,
        }
    )


def extract_speakers(text: str) -> list[dict[str, Any]]:
    speakers: list[dict[str, Any]] = []
    seen: set[str] = set()
    section: str | None = None
    current_scene = ""

    for line in text.splitlines():
        if line.startswith("## "):
            heading = clean_cell(line[3:]).strip('" ')
            if heading == '豆包语音合成模型2.0" 音色列表':
                section = "tts2"
            elif heading == '端到端实时语音大模型 S2S-O版本和SC-2.0版本 "音色列表':
                section = None
            elif heading == '豆包语音合成模型1.0" 音色列表':
                section = "tts1"
            else:
                section = None
            current_scene = ""
            continue

        if section is None:
            continue

        row = split_row(line)
        if len(row) < 3:
            continue
        if any(cell.startswith("---") for cell in row):
            continue
        if row[0].startswith("场景") or row[1].startswith("音色名称"):
            continue

        raw_scene = row[0] if len(row) > 0 else ""
        if raw_scene and raw_scene != "^^":
            current_scene = raw_scene
        scene = current_scene
        name = row[1] if len(row) > 1 else ""
        voice_type = row[2] if len(row) > 2 else ""
        if not name or not VOICE_TYPE_PATTERN.match(voice_type):
            continue

        if section == "tts2":
            language = row[3] if len(row) > 3 else ""
            abilities = split_list(row[4] if len(row) > 4 else "")
            tags = split_list(row[5] if len(row) > 5 else "")
            append_speaker(
                speakers,
                seen,
                resource_id="seed-tts-2.0",
                model_family="豆包语音合成模型2.0",
                scene=scene,
                name=name,
                voice_type=voice_type,
                language=language,
                abilities=abilities,
                supported_emotions=[],
                tags=tags,
            )
            continue

        language = row[3] if len(row) > 3 else ""
        supported_emotions = normalize_emotions(row[4] if len(row) > 4 else "")
        tags = split_list(row[5] if len(row) > 5 else "")
        corresponding_2_0_voice = row[6] if len(row) > 6 and row[6] else None
        supports_mix_value = row[7] if len(row) > 7 else ""
        if supports_mix_value == "是":
            supports_mix = True
        elif supports_mix_value == "否":
            supports_mix = False
        else:
            supports_mix = None
        append_speaker(
            speakers,
            seen,
            resource_id="seed-tts-1.0",
            model_family="豆包语音合成模型1.0",
            scene=scene,
            name=name,
            voice_type=voice_type,
            language=language,
            abilities=[],
            supported_emotions=supported_emotions,
            tags=tags,
            corresponding_2_0_voice=corresponding_2_0_voice,
            supports_mix=supports_mix,
        )

    return speakers


def main() -> int:
    speakers = extract_speakers(DOC_PATH.read_text(encoding="utf-8"))
    if not speakers:
        raise RuntimeError(f"No Volcengine TTS speakers extracted from {DOC_PATH}")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(DOC_PATH.relative_to(ROOT_DIR)).replace("\\", "/"),
        "speaker_count": len(speakers),
        "speakers": speakers,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"speaker_count={len(speakers)}")
    print(f"output_path={OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
