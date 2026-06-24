from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings


def _check(path: Path) -> None:
    settings = load_settings(path)
    node = settings.nodes.get("storyboard_keyframe_generation")
    if node is None:
        raise AssertionError(f"{path.name} missing storyboard_keyframe_generation node")
    params = node.params
    if params.get("prompt_template") != "toapi_gpt_image_2":
        raise AssertionError(f"{path.name} keyframe prompt_template is not configured")
    if int(params.get("concurrency") or 0) < 1:
        raise AssertionError(f"{path.name} keyframe concurrency is invalid")


def main() -> None:
    _check(ROOT / "config.yaml.example")
    _check(ROOT / "huyao.yaml")
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "config_keyframe_node_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("config_keyframe_node_smoke: ok")


if __name__ == "__main__":
    main()
