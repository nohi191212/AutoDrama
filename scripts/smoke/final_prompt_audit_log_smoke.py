"""Verify provider-boundary prompt logs are byte-for-byte request prompts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.providers.router import BoundProviderProxy
from autodrama.utils.prompts import PromptAuditLogger


class Payload(BaseModel):
    value: str


class RecordingProvider:
    name = "recording"
    model = "recording-text"

    def __init__(self) -> None:
        self.received: list[str] = []

    async def generate_json(self, prompt, schema, *, temperature=0.7, metadata=None, refs=None):
        del schema, temperature, metadata, refs
        self.received.append(prompt)
        return Payload(value="ok")


async def run() -> None:
    project_dir = ROOT / ".tmp" / "final_prompt_audit_log"
    provider = RecordingProvider()
    proxy = BoundProviderProxy(provider, None, prompt_audit=PromptAuditLogger(project_dir))
    first = "第一版\n\n保留段落。"
    second = "安全改写后的最终版本\n\n保留段落。"
    await proxy.generate_json(first, Payload, metadata={"node_name": "shot_keyframe_image_generation", "shot_id": "shot_a"})
    await proxy.generate_json(second, Payload, metadata={"node_name": "shot_keyframe_image_generation", "shot_id": "shot_a", "safety_prompt_rewrite_attempt": 1})
    root = project_dir / "logs" / "prompts" / "shot_keyframe"
    if provider.received != [first, second]:
        raise AssertionError("recording provider did not receive the expected final prompts")
    if (root / "shot_a.prompt.txt").read_text(encoding="utf-8") != second:
        raise AssertionError("primary prompt log is not the final provider prompt")
    if (root / "history" / "shot_a.attempt-01.prompt.txt").read_text(encoding="utf-8") != first:
        raise AssertionError("first prompt history is missing")
    if (root / "history" / "shot_a.attempt-02.prompt.txt").read_text(encoding="utf-8") != second:
        raise AssertionError("rewritten prompt history is missing")


def main() -> None:
    asyncio.run(run())
    print("final_prompt_audit_log_smoke: ok")


if __name__ == "__main__":
    main()
