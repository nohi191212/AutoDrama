from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.core.schemas import DirectorPrepOutput, ProjectState, ScriptBundle
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.services.director_service import DirectorService


EXPECTED_KEYS = {"story_core", "worldview", "visual_tone"}


def _legacy_payload() -> dict[str, object]:
    return {
        "story_core": "主角被陷害后用证据反击。",
        "worldview": "现代职场悬疑短剧，证据链推动剧情。",
        "visual_tone": "冷白办公光、低饱和色彩、克制写实表演。",
        "immutable_rules": ["旧字段应被忽略"],
        "character_locks": [{"name": "林舟"}],
        "scene_locks": [{"name": "雨夜办公室"}],
        "episodes": [
            {
                "episode_key": "episode_001",
                "shot_beats": [{"beat_index": 1, "title": "旧节拍"}],
            }
        ],
    }


def _state_with_legacy_director_prep() -> ProjectState:
    return ProjectState(
        project_id="director_prep_contract_smoke",
        title="导演前期契约 smoke",
        raw_script="林舟在雨夜办公室发现合同被调包。",
        script=ScriptBundle(
            raw_script="林舟在雨夜办公室发现合同被调包。",
            novel_full={"episode_001": "林舟在雨夜办公室发现合同被调包。"},
        ),
        metadata={"director_prep": _legacy_payload()},
    )


async def main() -> None:
    output = DirectorPrepOutput.model_validate(_legacy_payload())
    dump = output.model_dump(mode="json")
    assert set(dump) == EXPECTED_KEYS
    assert dump["visual_tone"]

    context = DirectorService.director_prep_context(
        _state_with_legacy_director_prep(),
        episode_keys=["episode_001"],
    )
    context_payload = json.loads(context)
    assert set(context_payload) == EXPECTED_KEYS
    assert "episodes" not in context_payload
    assert "shot_beats" not in context
    assert "character_locks" not in context
    assert "scene_locks" not in context

    provider = FakeTextProvider()
    fake_output = await provider.generate_json(
        "目标集数：1\n单集目标时长：30",
        DirectorPrepOutput,
        metadata={"node_name": "director_prep", "expected_keys": ["episode_001"]},
    )
    fake_dump = fake_output.model_dump(mode="json")
    assert set(fake_dump) == EXPECTED_KEYS

    prompt = (ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "director_prep.md").read_text(
        encoding="utf-8"
    )
    assert "target_shot_beats" not in prompt
    assert "每集建议导演节拍数" not in prompt
    assert "顶层字段只能包含" in prompt

    print("director prep contract smoke passed")


if __name__ == "__main__":
    asyncio.run(main())
