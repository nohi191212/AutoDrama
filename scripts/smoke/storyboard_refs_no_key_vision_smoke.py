from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    ScriptBundle,
    StoryboardPromptShot,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import build_storyboard_asset_node_runners  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_fake_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "storyboard_refs_no_key_vision" / "outputs"
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    runners = build_storyboard_asset_node_runners(workflow)
    node = runners["storyboard_generation"]

    project_dir = settings.output.root_dir / "storyboard_refs_no_key_vision"
    layout_path = Path("assets/images/layouts/layout_test.png")
    role_path = Path("assets/images/roles/role_test.png")
    prop_path = Path("assets/images/props/prop_test.png")
    key_vision_path = Path("assets/images/key_visions/key_vision_original.png")
    for relative_path in (layout_path, role_path, prop_path, key_vision_path):
        write_fake_png(project_dir / relative_path)

    state = ProjectState(
        project_id="storyboard_refs_no_key_vision",
        title="Smoke",
        raw_script="Smoke script",
        script=ScriptBundle(raw_script="Smoke script"),
        layouts={
            "layout_test": Layout(
                id="layout_test",
                name="测试场景",
                desc="测试场景",
                prompt="测试场景",
                asset_path=str(layout_path).replace("\\", "/"),
                asset_url="https://example.invalid/layout.png",
            )
        },
        roles={
            "role_test": Role(
                id="role_test",
                name="测试角色",
                intro="测试角色",
                appearances={
                    "base": RoleAppearance(
                        id="role_test_appearance_base",
                        role_id="role_test",
                        name="base",
                        asset_path=str(role_path).replace("\\", "/"),
                        asset_url="https://example.invalid/role.png",
                    )
                },
            )
        },
        props={
            "prop_test": Prop(
                id="prop_test",
                name="测试道具",
                desc="测试道具",
                asset_path=str(prop_path).replace("\\", "/"),
                asset_url="https://example.invalid/prop.png",
            )
        },
        metadata={
            "key_vision_asset": {
                "asset_path": str(key_vision_path).replace("\\", "/"),
                "asset_url": "https://example.invalid/key-vision.png",
            },
            "key_vision_asset_path": str(key_vision_path).replace("\\", "/"),
            "key_vision_asset_url": "https://example.invalid/key-vision.png",
        },
    )
    shot = StoryboardPromptShot(
        shot_id="episode_001_shot_001",
        duration_seconds=15,
        role_ids=["role_test"],
        layout_ids=["layout_test"],
        prop_ids=["prop_test"],
        video_prompt="0-1秒：测试镜头。",
    )

    refs = node.storyboard_reference_refs(project_dir, state, shot, limit=10)
    asset_types = [str(ref.metadata.get("asset_type") or "") for ref in refs]
    require(asset_types == ["layout", "roleboard", "prop"], f"Unexpected storyboard refs: {asset_types}")
    require("key_vision" not in asset_types, "storyboard refs must not include key_vision")

    prompt = node.storyboard_image_prompt("episode_001", shot)
    require("禁止使用主视觉图/key_vision" in prompt, "storyboard image prompt missing key_vision prohibition")

    print("storyboard_refs_no_key_vision_smoke=ok")
    print(f"asset_types={asset_types}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
