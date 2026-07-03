from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ProjectState, ScriptBundle, ScriptNovelEpisodeOutput
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore


async def main() -> None:
    schema = ScriptNovelEpisodeOutput.model_json_schema()
    if sorted(schema.get("properties", {})) != ["novel_full"]:
        raise AssertionError(f"script_novel_episode schema must only expose novel_full; got {schema!r}")
    if schema.get("required") != ["novel_full"]:
        raise AssertionError(f"script_novel_episode must require novel_full; got {schema!r}")
    if schema.get("additionalProperties") is not False:
        raise AssertionError(f"script_novel_episode must forbid extra fields; got {schema!r}")

    try:
        ScriptNovelEpisodeOutput.model_validate(
            {
                "episode_key": "episode_001",
                "target_char_count": 1800,
                "novel_full": "正文",
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("script_novel_episode must reject episode_key and target_char_count")

    try:
        ScriptNovelEpisodeOutput.model_validate({"novel_text": "正文"})
    except ValidationError:
        pass
    else:
        raise AssertionError("script_novel_episode must reject legacy novel_text")

    state = ProjectState(
        project_id="script_novel_episode_schema_smoke",
        title="契约烟测",
        raw_script="原始剧本",
        script=ScriptBundle(raw_script="原始剧本", outline="总大纲"),
        metadata={"episode_count": 1, "episode_duration_seconds": 30},
    )
    service = ScriptService(PromptStore())
    output = await service.script_novel_episode(
        state,
        FakeTextProvider(),
        episode_key="episode_001",
        episode_outlines={"episode_001": "当前集分集大纲"},
        current_episode_outline="当前集分集大纲",
        previous_chapters="（暂无，当前是第一章。）",
    )
    if not output.novel_full.strip():
        raise AssertionError("script_novel_episode must produce novel_full")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "script_novel_episode_schema_smoke.ok").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("script_novel_episode_schema_smoke: ok")


if __name__ == "__main__":
    asyncio.run(main())
