from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.prompt_evolution.sol_client import SolVisionClient  # noqa: E402


class TransportResult(BaseModel):
    ok: bool
    note: str


async def run() -> None:
    client = SolVisionClient.from_config(ROOT / "config.saodi_bashinian.yaml")
    client.reasoning_effort = "low"
    client.max_poll_seconds = 300
    result = await client.generate_json(
        "Return ok=true and note='background transport works'.",
        TransportResult,
    )
    output = ROOT / ".tmp" / "gpt-image-2-xuanhuan-relative-loop-20260805" / "sol_transport_smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.model_dump(), indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    asyncio.run(run())
