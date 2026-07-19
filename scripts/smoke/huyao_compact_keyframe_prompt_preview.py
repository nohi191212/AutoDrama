from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    ProjectState,
    StoryboardPromptOutput,
    StoryboardSheetGenerationItem,
)
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import (  # noqa: E402
    StoryboardKeyframeGenerationNode,
)


def main() -> None:
    project_dir = ROOT / "outputs" / "huyao"
    state = ProjectState.model_validate_json((project_dir / "state.json").read_text(encoding="utf-8"))
    storyboard = StoryboardPromptOutput.model_validate_json(
        (project_dir / "assets/json/nodes/clip_storyboard_prompt.json").read_text(encoding="utf-8")
    )

    node = object.__new__(StoryboardKeyframeGenerationNode)
    node.workflow = SimpleNamespace(prompts=PromptStore())
    node.repo = SimpleNamespace(settings=SimpleNamespace(nodes={}))
    node.asset_service = SimpleNamespace(
        visual_tone=lambda current_state: current_state.metadata["visual_style_prompt"]
    )
    provider = SimpleNamespace(
        name="aibox",
        model="gpt-image-2-guan",
        model_binding=SimpleNamespace(params={"prompt_template": "toapi_gpt_image_2"}),
    )

    previews: list[dict[str, object]] = []
    episode = storyboard.storyboards[0]
    for clip_index, clip in enumerate(episode.clips[:4]):
        frame_roles = [("start", "P01"), ("end", "P12")] if clip_index == 0 else [("end", "P12")]
        sheet = StoryboardSheetGenerationItem(
            episode_key=episode.episode_key,
            clip_id=clip.clip_id,
            asset_id=f"{clip.clip_id}_storyboard",
            prompt="storyboard",
            provider="fake",
            model="fake",
        )
        for frame_role, panel_ref in frame_roles:
            prompt, _ = node._render_keyframe_prompt(
                provider=provider,
                state=state,
                episode_key=episode.episode_key,
                clip=clip,
                storyboard_sheet=sheet,
                frame_role=frame_role,
                panel_ref=panel_ref,
            )
            if "Camera shots:" in prompt or "Panel plan:" in prompt or "Video prompt:" in prompt:
                raise AssertionError("compact keyframe prompt leaked legacy multi-shot sections")
            previews.append(
                {
                    "clip_id": clip.clip_id,
                    "frame_role": frame_role,
                    "panel_ref": panel_ref,
                    "prompt_chars": len(prompt),
                    "prompt": prompt,
                }
            )

    output_path = ROOT / ".tmp" / "huyao_compact_keyframe_prompt_preview.json"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(json.dumps(previews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{key: item[key] for key in ("clip_id", "frame_role", "prompt_chars")} for item in previews], ensure_ascii=False))


if __name__ == "__main__":
    main()
