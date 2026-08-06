from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from autodrama.core.schemas import KeyVisionPromptOutput
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.services.director_service import DirectorService
from autodrama.utils.prompts import PromptStore
from autodrama.visual_styles import load_visual_style


ROOT_DIR = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT_DIR / "autodrama" / "src" / "autodrama" / "prompts"


def main() -> int:
    state = SimpleNamespace(metadata={})
    portrait = DirectorService.key_vision_render_contract(state, "1024x1536")
    landscape = DirectorService.key_vision_render_contract(state, "3840x2160")
    continuity = DirectorService.key_vision_continuity_contract(state)
    director_brief = DirectorService.key_vision_director_brief(state)
    assert "portrait" in portrait
    assert "landscape" in landscape
    assert continuity == ""
    assert director_brief == ""

    template_text = (PROMPT_DIR / "key_vision_prompt" / "default.md").read_text(encoding="utf-8")
    for hardcoded_style in ("xuanhuan-v1", "玄幻", "very old identity"):
        assert hardcoded_style not in template_text
    style_prompt = load_visual_style("xuanhuan-v1")
    rendered = PromptStore(PROMPT_DIR).render(
        "key_vision_prompt",
        story_context="An elderly gatekeeper crosses one stone threshold while a witness stays behind.",
        global_visual_style=style_prompt,
        director_brief=director_brief,
        render_contract=portrait,
        continuity_contract=continuity,
    )
    assert "Eastern xuanhuan and wuxia stylized 3D CG" in rendered
    for marker in (
        "<crossing_camera_rule>",
        "<shot_contract_example non_binding=\"true\">",
        "<scene_style_contract_design>",
        "anatomical RIGHT maps to screen-LEFT",
        "[FINAL 2D SCREEN CHECK]",
        "`shot_contract`, then `scene_style_contract`, then `prompt`",
    ):
        assert marker in rendered, marker
    assert "{{" not in rendered and "}}" not in rendered
    output = KeyVisionPromptOutput(
        shot_contract="Locked camera and action proof.",
        scene_style_contract="Motivated light and scene-native materials.",
        prompt="Completed image prompt.",
    )
    assert output.shot_contract and output.scene_style_contract and output.prompt
    try:
        KeyVisionPromptOutput.model_validate({"prompt": "obsolete prompt-only response"})
    except ValidationError:
        pass
    else:
        raise AssertionError("prompt-only key vision output must be rejected")
    generated = asyncio.run(
        DirectorService(PromptStore(PROMPT_DIR)).key_vision_prompt(
            SimpleNamespace(
                project_id="key-vision-contract-smoke",
                metadata={"visual_style_prompt": style_prompt},
            ),
            FakeTextProvider(),
            story_context="One subject performs a consequential action in one continuous space.",
            image_canvas="1024x1536",
        )
    )
    assert generated.shot_contract
    assert generated.scene_style_contract
    assert generated.prompt
    print("key vision prompt contract smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
