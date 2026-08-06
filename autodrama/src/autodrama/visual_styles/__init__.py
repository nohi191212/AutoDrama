from __future__ import annotations

import re
from pathlib import Path


PRESET_DIR = Path(__file__).resolve().parents[1] / "prompts" / "visual_styles"
PRESET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def available_visual_style_presets() -> list[str]:
    return sorted(
        path.stem
        for path in PRESET_DIR.glob("*.md")
        if path.is_file() and PRESET_NAME_PATTERN.fullmatch(path.stem)
    )


def load_visual_style(name: str) -> str:
    preset_name = str(name or "").strip()
    if not PRESET_NAME_PATTERN.fullmatch(preset_name):
        raise ValueError(
            "visual style preset name must use lowercase letters, digits, hyphens or underscores"
        )
    path = PRESET_DIR / f"{preset_name}.md"
    if not path.is_file():
        available = ", ".join(available_visual_style_presets()) or "(none)"
        raise ValueError(f"unknown visual style preset {preset_name!r}; available: {available}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"visual style preset {preset_name!r} is empty")
    return content


__all__ = ["available_visual_style_presets", "load_visual_style"]
