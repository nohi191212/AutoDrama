from __future__ import annotations

import asyncio
import json
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
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardAssetNodeBase


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
    if "clip_segment" in SCRIPT_NODE_NAMES:
        raise AssertionError("clip_segment should run after role/prop/layout extraction, not in script nodes")
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
        role_index='林舟: 被陷害后反击的职场青年。\n苏晚: 协助林舟追查证据的数据分析师。',
        prop_index='邮件截图: 证明合同被调包的关键证据。',
        layout_index='雨夜办公室: 林舟发现证据的主要空间。',
    )
    if "Timing Standards" not in prompt or "8-15 秒" not in prompt:
        raise AssertionError("clip_segment prompt is missing timing requirements")
    if "不要为了满足总秒数机械计算 clip 数量" not in prompt:
        raise AssertionError("clip_segment prompt should treat episode duration as reference only")
    if "该集完整正文" not in prompt or "全剧集摘要" not in prompt or "当前集角色索引" not in prompt:
        raise AssertionError("clip_segment prompt is missing revised input labels")
    if "不要包含标题名" not in prompt or "人物：江未晞、九韶" not in prompt:
        raise AssertionError("clip_segment prompt must exclude titles and cast-list text")
    for removed_text in ("单集目标时长", "原始故事", "\n完整正文：", "分集摘要", "项目约束", "Required JSON schema"):
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
    index_dir = tmp_dir / "clip_segment_index_context"
    node_dir = index_dir / "assets" / "json" / "nodes"
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "role_finalize.json").write_text(
        json.dumps(
            {
                "final_roles": [
                    {"name": "林舟", "episode_keys": ["episode_001"], "brief": "被陷害后反击的职场青年。"},
                    {"name": "赵启", "episode_keys": ["episode_002"], "brief": "施压的反派。"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (node_dir / "prop_extract.json").write_text(
        json.dumps({"generated_prop_intro": {"邮件截图": "证明合同被调包的关键证据。"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (node_dir / "layout_extract.json").write_text(
        json.dumps({"layouts": [{"name": "雨夜办公室", "episode_keys": ["episode_001"], "brief": "发现证据的主要空间。"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    node.layout = SimpleNamespace(node_output_path=lambda project_dir, node_name: project_dir / "assets" / "json" / "nodes" / f"{node_name}.json")
    indexed_state = SimpleNamespace(roles={}, props={}, layouts={})
    if node.role_index_context(index_dir, indexed_state, "episode_001") != "林舟: 被陷害后反击的职场青年。":
        raise AssertionError("role index should be filtered to the active episode")
    if "邮件截图: 证明合同被调包的关键证据。" not in node.prop_index_context(index_dir, indexed_state, "episode_001"):
        raise AssertionError("prop index should include generated prop intro")
    if "雨夜办公室: 发现证据的主要空间。" not in node.layout_index_context(index_dir, indexed_state, "episode_001"):
        raise AssertionError("layout index should include current episode layout")

    episode_output_dir = tmp_dir / "clip_segment_episode_outputs"
    episode_node_dir = episode_output_dir / "assets" / "json" / "nodes" / "clip_segment"
    episode_node_dir.mkdir(parents=True, exist_ok=True)
    for episode_key, episode_text in (("episode_001", "第一集片段。"), ("episode_002", "第二集片段。")):
        (episode_node_dir / f"{episode_key}.json").write_text(
            json.dumps(
                {
                    "1": {
                        "text": episode_text,
                        "role_names": [],
                        "prop_names": [],
                        "layout_names": [],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    episode_layout = SimpleNamespace(
        node_episode_output_path=lambda project_dir, node_name, episode_key: (
            project_dir / "assets" / "json" / "nodes" / node_name / f"{episode_key}.json"
        ),
        node_output_path=lambda project_dir, node_name: project_dir / "assets" / "json" / "nodes" / f"{node_name}.json",
    )
    storyboard_node = StoryboardAssetNodeBase.__new__(StoryboardAssetNodeBase)
    storyboard_node.layout = episode_layout
    loaded = storyboard_node.clip_segments_by_episode(episode_output_dir)
    if list(loaded) != ["episode_001", "episode_002"]:
        raise AssertionError("clip_segment should load one output file per episode")
    if loaded["episode_002"]["1"].text != "第二集片段。":
        raise AssertionError("clip_segment episode output content was not loaded")

    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "clip_segment_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("clip_segment_contract_smoke: ok")


if __name__ == "__main__":
    main()
