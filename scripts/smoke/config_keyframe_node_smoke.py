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
    node = settings.nodes.get("clip_storyboard_keyframe_generation")
    if node is None:
        raise AssertionError(f"{path.name} missing clip_storyboard_keyframe_generation node")
    params = node.params
    if params.get("prompt_template") != "toapi_gpt_image_2":
        raise AssertionError(f"{path.name} keyframe prompt_template is not configured")
    if params.get("quality") != "high":
        raise AssertionError(f"{path.name} keyframe quality must be fixed to high")
    if int(params.get("clip_storyboard_keyframe_generation_concurrency") or 0) < 1:
        raise AssertionError(f"{path.name} keyframe concurrency is invalid")
    toapi_options = settings.providers["toapi"].options
    if int(toapi_options.get("toapi_reference_upload_max_attempts") or 0) < 2:
        raise AssertionError(f"{path.name} ToAPI reference upload retries are not configured")
    if float(toapi_options.get("toapi_reference_upload_retry_initial_delay_seconds") or 0) <= 0:
        raise AssertionError(f"{path.name} ToAPI reference upload retry delay is invalid")


def main() -> None:
    _check(ROOT / "config.yaml.example")
    _check(ROOT / "config.yaml")
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "config_keyframe_node_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("config_keyframe_node_smoke: ok")


if __name__ == "__main__":
    main()
