from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ClipSegment, ClipSegmentOutput
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes import AVAILABLE_PREGEN_NODE_NAMES, PREGEN_NODE_NAMES
from autodrama.workflows.nodes.script_nodes import ClipSegmentNode, SCRIPT_NODE_NAMES


async def fake_provider_clip_segment() -> ClipSegmentOutput:
    return await FakeTextProvider().generate_json(
        "{}",
        ClipSegmentOutput,
        metadata={
            "node_name": "clip_segment",
            "episode_key": "episode_001",
            "episode_duration_seconds": 30,
            "segment_seconds": 15,
        },
    )


def main() -> None:
    if "clip_segment" not in SCRIPT_NODE_NAMES:
        raise AssertionError("clip_segment is missing from script nodes")
    for node_name in ("clip_segment",):
        if node_name not in PREGEN_NODE_NAMES or node_name not in AVAILABLE_PREGEN_NODE_NAMES:
            raise AssertionError(f"{node_name} is not available through pregen")
    removed_node_name = "minute" + "_segment"
    if removed_node_name in SCRIPT_NODE_NAMES or removed_node_name in AVAILABLE_PREGEN_NODE_NAMES:
        raise AssertionError("removed segment node should be fully removed")

    prompt = PromptStore().render(
        "clip_segment",
        title="测试项目",
        episode_key="episode_001",
        episode_duration_seconds=30,
        min_clip_seconds=8,
        max_clip_seconds=15,
        duration_reference_note="episode_duration_seconds 只用于节奏参考，不用于硬性计算 clip 数量。",
        novel_full_this_episode="林舟关掉屏幕，苏晚递来邮件截图。",
        novel_extract_all_episodes='{"episode_001": "林舟发现证据。", "episode_002": "苏晚追查证据来源。"}',
    )
    if "Timing Standards" not in prompt or "8-15 秒" not in prompt:
        raise AssertionError("clip_segment prompt is missing timing requirements")
    if "不要为了满足总秒数机械计算 clip 数量" not in prompt:
        raise AssertionError("clip_segment prompt should treat episode duration as reference only")
    if "该集完整正文" not in prompt or "全剧集摘要" not in prompt:
        raise AssertionError("clip_segment prompt is missing revised input labels")
    for removed_text in ("单集目标时长", "原始故事", "\n完整正文：", "分集摘要", "导演前期约束", "Required JSON schema"):
        if removed_text in prompt:
            raise AssertionError(f"clip_segment prompt still contains removed text: {removed_text}")

    output = ClipSegmentOutput(
        {
            "1": ClipSegment(
                text="林舟关掉屏幕，深呼吸后摘下眼镜，苏晚递来邮件截图。",
                role_names=["林舟", "苏晚", "林舟"],
                prop_names=["邮件截图"],
                layout_names=["雨夜办公室"],
            ),
            "2": ClipSegment(
                text="林舟盯着邮件截图确认时间戳，赵启在会议室门口催促。",
                role_names=["林舟", "赵启"],
                prop_names=["邮件截图"],
                layout_names=["会议室"],
            ),
        }
    )
    state = SimpleNamespace(metadata={"episode_count": 1, "episode_duration_seconds": 30})
    node = ClipSegmentNode.__new__(ClipSegmentNode)
    node.script_service = ScriptService(PromptStore())
    clips = node.validate_clip_segments(output, state, episode_key="episode_001")
    if list(clips) != ["1", "2"]:
        raise AssertionError("clip keys should be consecutive numeric strings")
    if clips["1"].role_names != ["林舟", "苏晚"]:
        raise AssertionError("clip role_names should be deduplicated")
    try:
        ClipSegmentOutput.model_validate(
            {
                "1": {
                    "text": "林舟关掉屏幕。",
                    "role_names": ["林舟"],
                    "prop_names": [],
                    "layout_names": ["雨夜办公室"],
                    "summary": "额外字段不应被接受",
                }
            }
        )
    except Exception:
        pass
    else:
        raise AssertionError("clip segment schema should reject extra fields")

    fake_output = asyncio.run(fake_provider_clip_segment())
    if list(fake_output.root) != ["1", "2"]:
        raise AssertionError("fake provider did not return single-episode clip map")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "clip_segment_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("clip_segment_contract_smoke: ok")


if __name__ == "__main__":
    main()
