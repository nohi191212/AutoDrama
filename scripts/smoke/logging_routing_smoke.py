from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.logging import get_logger, get_pregen_detail_logger, log_context, setup_logging  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    project_dir = ROOT_DIR / ".tmp" / "smoke" / f"logging_routing_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(project_dir)

    with log_context(node_name="ref_frame_generation", episode_key="episode_001", shot_id="episode_001_shot_001"):
        get_logger().info("shot route smoke")

    get_pregen_detail_logger().info(
        "detail route smoke",
        extra={
            "node_name": "script_outline",
            "episode_key": "episode_001",
            "shot_id": "episode_001_shot_001",
        },
    )

    pipeline_log = project_dir / "logs" / "pipeline.log"
    ref_node_log = project_dir / "logs" / "nodes" / "ref_frame_generation.log"
    script_node_log = project_dir / "logs" / "nodes" / "script_outline.log"
    shot_log = project_dir / "logs" / "shots" / "episode_001" / "episode_001_shot_001.log"
    legacy_detail_log = project_dir / "logs" / "pregen_detail.log"

    require(pipeline_log.exists(), "pipeline.log missing")
    require(ref_node_log.exists(), "ref_frame_generation node log missing")
    require(script_node_log.exists(), "script_outline detail node log missing")
    require(shot_log.exists(), "shot log missing")
    require(not legacy_detail_log.exists(), "legacy pregen_detail.log should not be created")
    require("shot route smoke" in ref_node_log.read_text(encoding="utf-8"), "node log did not capture pipeline message")
    require("detail route smoke" in script_node_log.read_text(encoding="utf-8"), "node log did not capture detail message")
    require("shot route smoke" in shot_log.read_text(encoding="utf-8"), "shot log did not capture pipeline message")
    require("detail route smoke" in shot_log.read_text(encoding="utf-8"), "shot log did not capture detail message")

    print("logging_routing_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
