from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ScriptDetailExpandOutput
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.utils.prompts import PromptStore


FORBIDDEN_PROMPT_TEXT = [
    "{{title}}",
    "{{episode_key}}",
    "{{episode_count}}",
    "{{episode_duration_seconds}}",
    "{{max_expand_ratio}}",
    "项目 ID",
    "原始文件路径",
    "最大扩写比例",
]


async def fake_provider_output(prompt: str) -> ScriptDetailExpandOutput:
    return await FakeTextProvider().generate_json(
        prompt,
        ScriptDetailExpandOutput,
        metadata={"node_name": "script_detail_expand", "episode_key": "episode_001"},
    )


def main() -> None:
    schema = ScriptDetailExpandOutput.model_json_schema()
    properties = set(schema.get("properties") or {})
    if properties != {"expanded_script"}:
        raise AssertionError(f"script_detail_expand schema should only expose expanded_script, got {properties!r}")

    prompt = PromptStore().render(
        "script_detail_expand",
        raw_script="第一集：\n1-1：破殿-夜-内\n人物：江未晞\n△江未晞醒来，看见石台上出现光。",
        episode_outline="江未晞在破殿醒来，并看到石台上凝聚出AI管家九韶。",
    )
    for text in FORBIDDEN_PROMPT_TEXT:
        if text in prompt:
            raise AssertionError(f"script_detail_expand prompt contains forbidden metadata text: {text}")
    if "{{raw_script}}" in prompt or "{{episode_outline}}" in prompt:
        raise AssertionError("script_detail_expand prompt did not render raw_script or episode_outline")
    if "当集剧情概要" not in prompt:
        raise AssertionError("script_detail_expand prompt must include episode outline input")
    if "expanded_script" not in prompt:
        raise AssertionError("script_detail_expand prompt must request expanded_script")

    output = asyncio.run(fake_provider_output(prompt))
    dumped = output.model_dump(mode="json")
    if set(dumped) != {"expanded_script"}:
        raise AssertionError(f"fake output should only contain expanded_script, got {dumped!r}")
    if "细节补强" not in output.expanded_script:
        raise AssertionError("fake provider did not expand script detail")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "script_detail_expand_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("script_detail_expand_contract_smoke: ok")


if __name__ == "__main__":
    main()
