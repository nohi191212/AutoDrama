from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.visual_styles import (  # noqa: E402
    PRESET_DIR,
    available_visual_style_presets,
    load_visual_style,
)


def main() -> int:
    assert not list(PRESET_DIR.glob("*.yaml"))
    presets = available_visual_style_presets()
    assert presets == sorted(
        [
            "live-action-film-v1",
            "xuanhuan-v1",
            "xuanhuan-v2",
            "yushou-ink-animation-v1",
        ]
    )
    for name in presets:
        path = PRESET_DIR / f"{name}.md"
        raw = path.read_text(encoding="utf-8").strip()
        assert load_visual_style(name) == raw
        assert "schema_version:" not in raw
        assert not raw.startswith("#")

    expected = {
        "saodi.yaml": "xuanhuan-v1",
        "config.saodi_bashinian.yaml": "xuanhuan-v1",
        "config.saodi_bashinian_terra_image2.yaml": "xuanhuan-v1",
        "config.chonghui_jiuba.yaml": "live-action-film-v1",
        "config.yushou_xianchao.yaml": "yushou-ink-animation-v1",
    }
    for config_name, style_name in expected.items():
        settings = load_settings(ROOT / config_name)
        assert settings.generation.visual_style == style_name
        assert load_visual_style(style_name)

    print("global visual style smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
