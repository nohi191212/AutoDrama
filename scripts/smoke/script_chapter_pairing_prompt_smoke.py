from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.utils.prompts import PromptStore  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    raw_script = (ROOT_DIR / "inputs" / "仙尘劫第二卷.md").read_text(encoding="utf-8")
    chapter_count = len(re.findall(r"^\*\*第\d+章", raw_script, flags=re.MULTILINE))
    require(chapter_count == 100, f"Expected 100 source chapters, got {chapter_count}")

    prompts = PromptStore()
    episode_keys = ", ".join(f"episode_{index:03d}" for index in range(1, 51))
    outline_prompt = prompts.render(
        "script_outline",
        title="仙尘劫第二卷",
        raw_script=raw_script,
        episode_count=50,
        episode_duration_seconds=180,
        episode_keys=episode_keys,
        visual_style_label="真人电影质感",
        visual_style_prompt="真实摄影、电影布光、东方修仙质感。",
    )
    novel_prompt = prompts.render(
        "script_novel_episode",
        title="仙尘劫第二卷",
        outline="韩默进入坠仙秘境。",
        episode_outlines='{"episode_001": "源章节：第1章-第2章。传送入秘境并落地遇险。"}',
        current_episode_key="episode_001",
        current_episode_outline="源章节：第1章-第2章。传送入秘境并落地遇险。",
        previous_chapters="（暂无，当前是第一章。）",
        episode_count=50,
        episode_duration_seconds=180,
        target_char_count=10800,
        episode_keys=episode_keys,
        visual_style_label="真人电影质感",
        visual_style_prompt="真实摄影、电影布光、东方修仙质感。",
    )
    extract_prompt = prompts.render(
        "script_novel_extract",
        title="仙尘劫第二卷",
        novel_full='{"episode_001": "源章节：第1章-第2章。韩默踏入秘境，落地遇险。"}',
        previous_extract="（暂无，当前是第一批。）",
        extract_hints='{"episode_001": "源章节：第1章-第2章。传送入秘境并落地遇险。"}',
        batch_episode_keys=", ".join(f"episode_{index:03d}" for index in range(1, 6)),
        episode_count=50,
        episode_duration_seconds=180,
        visual_style_label="真人电影质感",
        visual_style_prompt="真实摄影、电影布光、东方修仙质感。",
    )
    require("连续 2 章合并为 1 集" in outline_prompt, "Outline prompt missing chapter-pairing rule")
    require("episode_001` 覆盖第1-2章" in outline_prompt, "Outline prompt missing episode_001 mapping")
    require("第99章-第100章" in outline_prompt, "Outline prompt missing final 100-chapter mapping example")
    require("当前集目标字数：10800 字左右" in novel_prompt, "Novel prompt missing target char count")
    require("已经写完的前序章节" in novel_prompt, "Novel prompt missing previous chapters context")
    require("novel_full` 写成连续小说正文" in novel_prompt, "Novel prompt missing novel_full rule")
    require("当前批次必须输出的分集键名" in extract_prompt, "Extract prompt missing batch output keys")
    require("保留每集的时间、人物、地点、关键事件" in extract_prompt, "Extract prompt missing core extraction rule")
    require("每 5 集" not in extract_prompt, "Extract prompt should receive the concrete batch, not a global batch rule")

    print("script_chapter_pairing_prompt_smoke=ok")
    print(f"chapter_count={chapter_count}")
    print("episode_count=50")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
