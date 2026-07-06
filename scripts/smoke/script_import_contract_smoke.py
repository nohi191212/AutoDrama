from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ScriptImportOutput
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.utils.prompts import PromptStore


FORBIDDEN_PROMPT_TEXT = [
    "{{title}}",
    "{{project_id}}",
    "{{source_script_file}}",
    "{{episode_key}}",
    "{{episode_count}}",
    "{{episode_duration_seconds}}",
    "项目 ID",
    "原始文件路径",
    "当前 episode_key",
]


async def fake_provider_output(prompt: str) -> ScriptImportOutput:
    return await FakeTextProvider().generate_json(
        prompt,
        ScriptImportOutput,
        metadata={"node_name": "script_import"},
    )


def main() -> None:
    schema = ScriptImportOutput.model_json_schema()
    properties = set(schema.get("properties") or {})
    expected = {"outline", "episode_outlines", "roles", "props", "layouts", "notes"}
    if properties != expected:
        raise AssertionError(f"script_import schema fields should be {expected!r}, got {properties!r}")

    prompt = PromptStore().render(
        "script_import",
        raw_script="江未晞在破败殿宇醒来，遇见AI管家九韶，并看到九尾狐密室机关。",
    )
    for text in FORBIDDEN_PROMPT_TEXT:
        if text in prompt:
            raise AssertionError(f"script_import prompt contains forbidden metadata text: {text}")
    if "{{raw_script}}" in prompt:
        raise AssertionError("script_import prompt did not render raw_script")
    for text in ("episode_outlines", "roles", "props", "layouts"):
        if text not in prompt:
            raise AssertionError(f"script_import prompt missing required output field: {text}")

    output = asyncio.run(fake_provider_output(prompt))
    if not output.outline.strip():
        raise AssertionError("fake script_import output missing outline")
    if not output.episode_outlines:
        raise AssertionError("fake script_import output missing episode_outlines")
    if not output.roles or not output.props or not output.layouts:
        raise AssertionError("fake script_import output should include roles, props, and layouts")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "script_import_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("script_import_contract_smoke: ok")


if __name__ == "__main__":
    main()
