from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings

IMAGE_NODES = {
    "design_key_vision_image",
    "roleboard_generation",
    "storyboard_sheet_generation",
    "storyboard_keyframe_generation",
    "prop_image_generation",
    "layout_image_generation",
}
FORBIDDEN_PARAMS = {"aspect_ratio", "resolution", "n", "response_format"}
EXPECTED_MODEL = "aibox:gpt-image-2-guan"


def _check(path: Path) -> None:
    settings = load_settings(path)
    missing = sorted(IMAGE_NODES.difference(settings.nodes))
    if missing:
        raise AssertionError(f"{path.name} missing image node(s): {missing}")
    for node_name in sorted(IMAGE_NODES):
        node = settings.nodes[node_name]
        if node.model != EXPECTED_MODEL:
            raise AssertionError(f"{path.name} nodes.{node_name}.model={node.model!r}, expected {EXPECTED_MODEL!r}")
        leaked = sorted(FORBIDDEN_PARAMS.intersection(node.params))
        if leaked:
            raise AssertionError(f"{path.name} nodes.{node_name}.params leaked unsupported field(s): {leaked}")


def main() -> None:
    checked = []
    for config_name in ("config.yaml", "config.yaml.example"):
        path = ROOT / config_name
        if path.exists():
            _check(path)
            checked.append(config_name)
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "aibox_image_nodes_guan_smoke.ok").write_text("\n".join(checked) + "\n", encoding="utf-8")
    print(f"aibox_image_nodes_guan_smoke: ok ({', '.join(checked)})")


if __name__ == "__main__":
    main()