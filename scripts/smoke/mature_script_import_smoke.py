from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import main as cli_main  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_id = f"mature_import_smoke_{run_id}"
    tmp_root = ROOT_DIR / ".tmp" / "mature_script_import_smoke"
    tmp_root.mkdir(parents=True, exist_ok=True)
    output_root = tmp_root / "outputs"
    script_path = tmp_root / "狐妖_smoke.md"
    expanded_path = tmp_root / "狐妖_smoke_细化.md"
    config_path = tmp_root / f"config_{run_id}.yaml"

    script_path.write_text(
        "\n".join(
            [
                "《成熟剧本导入 Smoke》",
                "人物小传：",
                "江未晞：神话乐园园主，刚绑定系统。",
                "九韶：银发 AI 管家。",
                "第一集：",
                "1-1：古老殿宇-日-外",
                "人物：江未晞、九韶",
                "九韶（VO）：检测到适配宿主，灵魂绑定已完成。",
                "△江未晞睁眼，发现四周尽是残垣断壁，中央有个布满灰尘的石台。",
                "江未晞（震惊）：灵魂绑定系统？我这是在哪儿？",
                "九韶：我是你的乐园AI管家，九韶。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "app:",
                "  env: dev",
                "apikeys_file: null",
                "project:",
                f"  id: {project_id}",
                "  title: 成熟剧本导入 Smoke",
                f"  script_outline_file: \"{script_path.as_posix()}\"",
                "  episode_count: 1",
                "  episode_duration_seconds: 30",
                "  bgm_count: 0",
                "output:",
                f"  root_dir: \"{output_root.as_posix()}\"",
                "runtime:",
                "  request_timeout_seconds: 30",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    import_code = cli_main(
        [
            "import-script",
            "--config",
            str(config_path),
            "--project",
            project_id,
            "--script",
            str(script_path),
            "--detail-expand",
            "--provider",
            "fake",
            "--expanded-script-out",
            str(expanded_path),
            "--force",
        ]
    )
    require(import_code == 0, f"import-script exited with {import_code}")

    project_dir = output_root / project_id
    state_path = project_dir / "state.json"
    state = read_json(state_path)
    completed = set(state["completed_nodes"])
    require({"script_detail_expand", "script_outline", "script_novel"}.issubset(completed), completed)
    require(state["metadata"]["script_mode"] == "mature_script", state["metadata"])
    require(state["metadata"]["mature_script_detail_expanded"] is True, state["metadata"])
    require(expanded_path.exists(), "expanded script markdown was not written")

    novel_ref = state["script"]["novel_full"]["episode_001"]
    novel_path = project_dir / novel_ref
    require(novel_path.exists(), f"novel_full path missing: {novel_path}")
    novel_payload = read_json(novel_path)
    require("细节补强" in novel_payload["content"], "fake detail expansion was not imported")

    extract_code = cli_main(
        [
            "run",
            "pregen",
            "--config",
            str(config_path),
            "--project",
            project_id,
            "--only",
            "script_novel_extract",
            "--provider",
            "fake",
        ]
    )
    require(extract_code == 0, f"script_novel_extract exited with {extract_code}")

    state = read_json(state_path)
    require("script_novel_extract" in state["completed_nodes"], state["completed_nodes"])
    extract_ref = state["script"]["novel_extract"]["episode_001"]
    require((project_dir / extract_ref).exists(), f"novel_extract path missing: {extract_ref}")
    bgm_code = cli_main(
        [
            "run",
            "pregen",
            "--config",
            str(config_path),
            "--project",
            project_id,
            "--only",
            "bgm_design",
            "--provider",
            "fake",
        ]
    )
    require(bgm_code == 0, f"bgm_design exited with {bgm_code}")

    state = read_json(state_path)
    require("bgm_design" in state["completed_nodes"], state["completed_nodes"])
    require(state["bgms"] == {}, state["bgms"])
    bgm_output = read_json(project_dir / "assets" / "json" / "nodes" / "bgm_design.json")
    require(bgm_output["bgms"] == [], bgm_output)

    script_path.write_text(
        "\n".join(
            [
                "《成熟剧本导入 Smoke》",
                "人物小传：",
                "江未晞：神话乐园园主，刚绑定系统。",
                "九韶：银发 AI 管家。",
                "第一集：",
                "1-1：古老殿宇-日-外",
                "人物：江未晞、九韶",
                "九韶（VO）：检测到适配宿主，灵魂绑定已完成。",
                "△江未晞睁眼，先听见殿顶碎瓦落地的轻响，才看见中央蒙尘的石台。",
                "江未晞（压低声音）：这到底是什么地方？",
                "九韶：我是你的乐园AI管家，九韶。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    preserve_code = cli_main(
        [
            "import-script",
            "--config",
            str(config_path),
            "--project",
            project_id,
            "--script",
            str(script_path),
            "--detail-expand",
            "--provider",
            "fake",
            "--preserve-assets",
        ]
    )
    require(preserve_code == 0, f"preserve-assets import exited with {preserve_code}")

    state = read_json(state_path)
    completed = set(state["completed_nodes"])
    require("script_novel_extract" not in completed, state["completed_nodes"])
    require("bgm_design" in completed, state["completed_nodes"])
    require(state["metadata"]["mature_script_preserved_assets"] is True, state["metadata"])
    require("script_novel_extract" in state["metadata"]["mature_script_invalidated_nodes"], state["metadata"])
    require(state["script"]["novel_extract"]["episode_001"] is False, state["script"]["novel_extract"])

    reextract_code = cli_main(
        [
            "run",
            "pregen",
            "--config",
            str(config_path),
            "--project",
            project_id,
            "--only",
            "script_novel_extract",
            "--provider",
            "fake",
        ]
    )
    require(reextract_code == 0, f"reextract after preserve-assets exited with {reextract_code}")

    state = read_json(state_path)
    require("script_novel_extract" in state["completed_nodes"], state["completed_nodes"])
    print(f"project_dir={project_dir}")
    print(f"novel_full={novel_ref}")
    print(f"novel_extract={extract_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
