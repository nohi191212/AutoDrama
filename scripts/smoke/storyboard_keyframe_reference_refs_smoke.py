from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Prop,
    PropAsset,
    Role,
    RoleAppearance,
    ScriptBundle,
    StoryboardPromptClip,
    StoryboardSheetGenerationItem,
)
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardKeyframeGenerationNode  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402


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
    key_vision_path = _write_placeholder(project_dir, "assets/images/key_visions/key_vision_original.png")
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
                intro="Access keycard",
                assets={
                    "base": PropAsset(
                        id="prop_keycard__base",
                        prop_id="prop_keycard",
                        name="base",
                        desc="Access keycard",
                        asset_id="prop_keycard__base",
                        asset_path=prop_path,
                    )
                },
            )
        },
    )
    state.metadata.update(
        {
            "visual_style_prompt": "统一的超精细写实 CGI 动画风格，冷青色电影布光与稳定材质表现。",
            "key_vision_asset_id": "key_vision_original",
            "key_vision_asset_path": key_vision_path,
            "key_vision_name": "主视觉原图",
        }
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
    if asset_types != ["storyboard", "key_vision", "roleboard", "layout", "prop"]:
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
    if limited_asset_types != ["storyboard", "key_vision"]:
        raise AssertionError(f"reference limit should preserve highest priority refs: {limited_asset_types}")
    try:
        node._keyframe_reference_refs(
            project_dir,
            state,
            clip,
            storyboard_sheet,
            frame_role="end",
            panel_ref="P12",
            limit=1,
        )
    except ValueError as exc:
        if "at least 2 reference image slots" not in str(exc):
            raise
    else:
        raise AssertionError("keyframe generation must reject providers that cannot carry both style anchors")

    node.workflow = SimpleNamespace(prompts=PromptStore())
    node.repo = SimpleNamespace(settings=SimpleNamespace(nodes={}))
    node.asset_service = SimpleNamespace(
        visual_tone=lambda _state: state.metadata["visual_style_prompt"]
    )
    provider = SimpleNamespace(
        name="aibox",
        model="gpt-image-2-guan",
        model_binding=SimpleNamespace(params={"prompt_template": "toapi_gpt_image_2"}),
    )
    prompt, prompt_template = node._render_keyframe_prompt(
        provider=provider,
        state=state,
        episode_key="episode_001",
        clip=clip,
        storyboard_sheet=storyboard_sheet,
        frame_role="end",
        panel_ref="P12",
    )
    if prompt_template != "storyboard_keyframe/toapi_gpt_image_2":
        raise AssertionError(f"unexpected keyframe prompt template: {prompt_template}")
    if state.metadata["visual_style_prompt"] not in prompt:
        raise AssertionError("keyframe prompt must include the authoritative project visual style")
    if "image_2: project key vision" not in prompt:
        raise AssertionError("keyframe prompt must assign image_2 as the shared style anchor")
    if "cinematic live-action frame" in prompt:
        raise AssertionError("keyframe prompt must not hard-code a live-action rendering style")
    if "never inherit its pencil-sketch medium" not in prompt:
        raise AssertionError("keyframe prompt must prevent storyboard sketch style leakage")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "storyboard_keyframe_reference_refs_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("storyboard_keyframe_reference_refs_smoke: ok")


if __name__ == "__main__":
    main()


