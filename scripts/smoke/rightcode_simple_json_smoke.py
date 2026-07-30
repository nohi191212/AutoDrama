from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter


class SimpleJSONOutput(BaseModel):
    answer: Literal[2]


async def run_smoke(config: Path, *, node_name: str, no_stream: bool) -> None:
    settings = load_settings(config)
    router = ProviderRouter(settings)
    provider = router.text("script", node_name=node_name)
    if no_stream:
        target = getattr(provider, "_provider", provider)
        if hasattr(target, "stream"):
            setattr(target, "stream", False)
    print(
        "provider="
        f"{getattr(provider, 'name', 'unknown')} model={getattr(provider, 'model', '-')}"
        f" stream={getattr(getattr(provider, '_provider', provider), 'stream', '-')}"
    )
    output = await provider.generate_json(
        "问题：1+1等于几？只输出 JSON，answer 字段填整数。",
        SimpleJSONOutput,
        temperature=0,
        metadata={
            "node_name": f"{node_name}_simple_json_smoke",
            "project_id": "rightcode_simple_json_smoke",
        },
    )
    if output.answer != 2:
        raise AssertionError(f"unexpected answer: {output.answer!r}")
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "rightcode_simple_json_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("rightcode_simple_json_smoke: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="huyao.yaml")
    parser.add_argument("--node-name", default="script_outline")
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        run_smoke(
            Path(args.config),
            node_name=args.node_name,
            no_stream=args.no_stream,
        )
    )


if __name__ == "__main__":
    main()
