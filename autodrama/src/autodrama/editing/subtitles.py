from __future__ import annotations

from pathlib import Path
from typing import Any


def srt_time(value: float) -> str:
    milliseconds = int(round(max(0.0, value) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def ass_time(value: float) -> str:
    centiseconds = int(round(max(0.0, value) * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, cents = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{cents:02d}"


def subtitle_text(cue: Any) -> str:
    text = str(cue.text).strip()
    if getattr(cue, "role_name", None):
        return f"{cue.role_name}: {text}"
    return text


def ass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", r"\N")


def write_srt(path: Path, cues: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{srt_time(cue.start_time)} --> {srt_time(cue.end_time)}",
                    subtitle_text(cue),
                ]
            )
        )
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def write_ass(path: Path, plan: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    font_size = max(24, int(plan.height * 0.055))
    margin_v = max(28, int(plan.height * 0.075))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {plan.width}",
        f"PlayResY: {plan.height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Microsoft YaHei,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,2,60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in plan.subtitle_cues:
        text = ass_escape(subtitle_text(cue))
        lines.append(
            "Dialogue: "
            f"0,{ass_time(cue.start_time)},{ass_time(cue.end_time)},"
            f"Default,,0,0,0,,{text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


__all__ = [
    "ass_escape",
    "ass_time",
    "srt_time",
    "subtitle_text",
    "write_ass",
    "write_srt",
]
