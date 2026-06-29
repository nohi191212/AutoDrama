from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    ScriptBundle,
    StoryboardPromptClip,
    StoryboardSheetGenerationItem,
)
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardKeyframeGenerationNode  # noqa: E402


class FakeLayout:
    def existing_project_file(self, project_dir: Path, path: str | Path | None) -> str | None:
        if not path:
            return None
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = project_dir / resolved
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            return None
        return str(resolved.relative_to(project_dir)).replace("\\", "/")


def _write_placeholder(project_dir: Path, relative_path: str) -> str:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"placeholder image bytes\n")
    return relative_path


def main() -> None:
    project_dir = ROOT / ".tmp" / "storyboard_keyframe_reference_refs_smoke"
    storyboard_path = _write_placeholder(project_dir, "assets/images/storyboards/clip_001_storyboard.png")
    roleboard_path = _write_placeholder(project_dir, "assets/images/roles/role_alex_base.png")
    layout_path = _write_placeholder(project_dir, "assets/images/layouts/layout_lab.png")
    prop_path = _write_placeholder(project_dir, "assets/images/props/prop_keycard.png")

    state = ProjectState(
        project_id="storyboard_keyframe_reference_refs_smoke",
        title="Smoke",
        raw_script="script",
        script=ScriptBundle(raw_script="script"),
        roles={
            "role_alex": Role(
                id="role_alex",
                name="Alex",
                intro="Lead",
                appearances={
                    "base": RoleAppearance(
                        id="role_alex_base",
                        role_id="role_alex",
                        asset_id="role_alex_base",
                        asset_path=roleboard_path,
                    )
                },
            )
        },
        layouts={
            "layout_lab": Layout(
                id="layout_lab",
                name="Lab",
                desc="Glass lab",
                prompt="Glass lab",
                asset_id="layout_lab",
                asset_path=layout_path,
            )
        },
        props={
            "prop_keycard": Prop(
                id="prop_keycard",
                name="Keycard",
                desc="Access keycard",
                asset_id="prop_keycard",
                asset_path=prop_path,
            )
        },
    )
    clip = StoryboardPromptClip(
        clip_id="clip_001",
        clip_title="Entry",
        clip_text="Alex enters the lab with a keycard.",
        duration_seconds=12,
        role_ids=["role_alex"],
        layout_ids=["layout_lab"],
        prop_ids=["prop_keycard"],
        camera_shots=[{"shot": "push in"}],
        panel_plan={"P12": "Alex raises the keycard inside the lab."},
        video_prompt="Alex enters the lab with a keycard.",
    )
    storyboard_sheet = StoryboardSheetGenerationItem(
        episode_key="episode_001",
        clip_id="clip_001",
        asset_id="clip_001_storyboard",
        prompt="storyboard",
        asset_path=storyboard_path,
        provider="fake",
        model="fake-image",
    )

    node = object.__new__(StoryboardKeyframeGenerationNode)
    node.layout = FakeLayout()

    refs = node._keyframe_reference_refs(
        project_dir,
        state,
        clip,
        storyboard_sheet,
        frame_role="end",
        panel_ref="P12",
        limit=8,
    )
    asset_types = [ref.metadata.get("asset_type") for ref in refs]
    if asset_types != ["storyboard", "roleboard", "layout", "prop"]:
        raise AssertionError(f"unexpected keyframe reference order: {asset_types}")
    if any(ref.metadata.get("reference_for") != "storyboard_keyframe" for ref in refs):
        raise AssertionError("all keyframe refs should be marked for storyboard_keyframe")
    if any(ref.metadata.get("panel_ref") != "P12" for ref in refs):
        raise AssertionError("all keyframe refs should record the target panel")

    limited_refs = node._keyframe_reference_refs(
        project_dir,
        state,
        clip,
        storyboard_sheet,
        frame_role="end",
        panel_ref="P12",
        limit=2,
    )
    limited_asset_types = [ref.metadata.get("asset_type") for ref in limited_refs]
    if limited_asset_types != ["storyboard", "roleboard"]:
        raise AssertionError(f"reference limit should preserve highest priority refs: {limited_asset_types}")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "storyboard_keyframe_reference_refs_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("storyboard_keyframe_reference_refs_smoke: ok")


if __name__ == "__main__":
    main()
