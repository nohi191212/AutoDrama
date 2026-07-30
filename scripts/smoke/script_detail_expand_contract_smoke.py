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

    raw_script = "第一集：\n1-1：破殿-夜-内\n人物：江未晞\n△江未晞醒来，看见石台上出现光。"
    prompt = PromptStore().render(
        "script_detail_expand",
        raw_script=raw_script,
    )
    if raw_script not in prompt:
        raise AssertionError("script_detail_expand prompt did not include the supplied content")
    if "{{" in prompt or "}}" in prompt:
        raise AssertionError("script_detail_expand prompt contains unresolved template variables")

    output = asyncio.run(fake_provider_output(prompt))
    dumped = output.model_dump(mode="json")
    if set(dumped) != {"expanded_script"}:
        raise AssertionError(f"fake output should only contain expanded_script, got {dumped!r}")
    if not output.expanded_script.strip():
        raise AssertionError("fake provider returned empty expanded_script")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "script_detail_expand_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("script_detail_expand_contract_smoke: ok")


if __name__ == "__main__":
    main()
