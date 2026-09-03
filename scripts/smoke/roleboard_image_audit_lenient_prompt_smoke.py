from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.utils.prompts import PromptStore  # noqa: E402


def main() -> int:
    prompt = PromptStore(SRC / "autodrama" / "prompts").render(
        "roleboard_image_audit",
        asset_name="动态角色名 / 动态造型名",
        expectation="动态提供的角色身份板预期",
        current_prompt="动态提供的生成提示词",
        reference_context="动态提供的参考图片说明",
    )

    required = (
        "宽松的生产兜底审查",
        "拿不准时优先通过",
        "标准美型",
        "轻微手脚问题",
        "一句简短中文说明",
    )
    for text in required:
        assert text in prompt, text

    for forbidden in ("叶凡", "柳菡烟", "李德海", "蹲下", "看向远方"):
        assert forbidden not in prompt, forbidden

    print("roleboard_image_audit_lenient_prompt_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
