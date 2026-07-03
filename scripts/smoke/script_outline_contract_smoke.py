from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ProjectState, ScriptBundle, ScriptOutlineOutput
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore


def validate_schema() -> None:
    fields = set(ScriptOutlineOutput.model_fields)
    expected = {"outline", "episode_outlines"}
    if fields != expected:
        raise AssertionError(f"ScriptOutlineOutput fields should be {expected}, got {fields}")


def validate_prompt() -> None:
    prompt = PromptStore().render(
        "script_outline",
        raw_script="第一章：林舟发现合同异常。第二章：苏晚递来证据。",
        episode_duration_seconds=30,
    )
    required_text = [
        "剧集概要",
        "划分后的单集大致目标时长",
        "分集键名格式",
        '"outline"',
        '"episode_outlines"',
    ]
    for text in required_text:
        if text not in prompt:
            raise AssertionError(f"script_outline prompt missing expected text: {text}")
    removed_text = [
        "标题：",
        "目标集数",
        "必须使用的分集键名",
        "`episode_count`",
        "`target_duration_seconds`",
    ]
    for text in removed_text:
        if text in prompt:
            raise AssertionError(f"script_outline prompt still contains removed text: {text}")


async def validate_fake_provider() -> None:
    output = await FakeTextProvider().generate_json(
        "{}",
        ScriptOutlineOutput,
        metadata={"node_name": "script_outline"},
    )
    dumped = output.model_dump(mode="json")
    if set(dumped) != {"outline", "episode_outlines"}:
        raise AssertionError(f"fake script_outline output has unexpected fields: {set(dumped)}")
    if not dumped["episode_outlines"]:
        raise AssertionError("fake script_outline output should include episode_outlines")


def validate_dynamic_episode_keys() -> None:
    state = ProjectState(
        project_id="script_outline_contract_smoke",
        title="Smoke",
        raw_script="原始故事",
        script=ScriptBundle(
            raw_script="原始故事",
            episode_outlines={
                "episode_002": "assets/json/scripts/outlines/episode_002.json",
                "episode_001": "assets/json/scripts/outlines/episode_001.json",
            },
        ),
        metadata={"episode_count": 50},
    )
    keys = ScriptService.state_episode_keys(state)
    if keys != ["episode_001", "episode_002"]:
        raise AssertionError(f"state episode keys should follow actual outline keys, got {keys}")


def main() -> None:
    validate_schema()
    validate_prompt()
    asyncio.run(validate_fake_provider())
    validate_dynamic_episode_keys()
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "script_outline_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("script_outline_contract_smoke: ok")


if __name__ == "__main__":
    main()
