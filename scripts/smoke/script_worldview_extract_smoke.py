from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from autodrama.core.schemas import ProjectState, ScriptBundle
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.script_nodes import ScriptWorldviewExtractNode


ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "autodrama" / "src" / "autodrama" / "prompts"


class FakeRepo:
    def save_node_output(self, project_dir: Path, node_name: str, output):
        return project_dir / f"{node_name}.json"


class FakeLayout:
    @staticmethod
    def project_relative(project_dir: Path, path: Path) -> str:
        return str(path.relative_to(project_dir)).replace("\\", "/")


class FakeRouter:
    def __init__(self) -> None:
        self.provider = FakeTextProvider()

    def text(self, purpose: str, *, node_name: str):
        assert purpose == "script"
        assert node_name == "script_worldview_extract"
        return self.provider


def main() -> int:
    raw_script = "第一幕：群山中的宗门以灵脉修炼，城镇由门派和商会共同治理。"
    rendered = PromptStore(PROMPTS).render("script_worldview_extract", raw_script=raw_script)
    assert raw_script in rendered
    assert "pure ASCII English text" in rendered
    assert "Return only one valid JSON object" in rendered

    state = ProjectState(
        project_id="script-worldview-smoke",
        title="Smoke",
        raw_script=raw_script,
        current_node="key_vision_prompt",
        completed_nodes=[
            "script_worldview_extract",
            "key_vision_prompt",
            "key_vision_image_generation",
            "key_vision_image_audit",
        ],
        script=ScriptBundle(raw_script=raw_script),
        metadata={
            "script_type": "Old plot-specific prompt",
            "key_vision_prompt": {"prompt": "old"},
            "key_vision_prompt_path": "old.json",
            "key_vision_audit_feedback": [{"issues": ["old character action"]}],
        },
    )
    node = ScriptWorldviewExtractNode(
        repo=FakeRepo(),
        layout=FakeLayout(),
        router=FakeRouter(),
        script_service=ScriptService(PromptStore(PROMPTS)),
        script_contents=SimpleNamespace(),
        logger=logging.getLogger("script-worldview-smoke"),
    )
    asyncio.run(node.run(ROOT / ".tmp" / "script-worldview-smoke", state))
    script_type = str(state.metadata.get("script_type") or "")
    assert script_type
    assert script_type.isascii()
    assert any(char.isalpha() for char in script_type)
    assert state.metadata["script_worldview_extract_path"] == "script_worldview_extract.json"
    assert state.completed_nodes == ["script_worldview_extract"]
    assert state.current_node == "script_worldview_extract"
    assert "key_vision_prompt" not in state.metadata
    assert "key_vision_audit_feedback" not in state.metadata
    print("script worldview extract smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
