from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    prompt = (
        "爱死机写实CG风格，高保真三维角色设定图。角色为约20岁年轻男性，"
        "皮肤质感真实，有明显的苍白和尸化痕迹，腹部有一处撕裂伤口，"
        "伤口边缘呈紫黑色，一丛细密的紫色根须从伤口伸出，微微蠕动。"
        "身穿灰白色宽袖长袍，多处破损，衣摆有泥土和暗紫色血渍。"
        "右侧物品：一柄残破的断剑剑柄，旁边放置一只成年男性手掌作为比例参照。"
    )
    safe_prompt = PregenWorkflow._role_appearance_image_prompt(prompt)

    for forbidden in (
        "血",
        "尸化",
        "尸体",
        "伤口",
        "撕裂",
        "蠕动",
        "内脏",
        "断肢",
        "一只成年男性手掌",
    ):
        require(forbidden not in safe_prompt, f"Unsafe role prompt still contains: {forbidden}")

    require("旧污渍" in safe_prompt or "泥污" in safe_prompt, "Blood-like clothing marks were not softened")
    require("不规则破损" in safe_prompt or "衣料破口" in safe_prompt, "Graphic wound wording was not softened")
    require("异化纹理" in safe_prompt, "Corpse-like wording was not softened")
    require("简化手掌轮廓" in safe_prompt, "Hand proportion reference was not softened")
    require("克制的角色设定图" in safe_prompt, "Safety style note missing")

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "role_appearance_prompt_safety"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "safe_prompt.txt").write_text(safe_prompt, encoding="utf-8")

    print("role_appearance_prompt_safety_smoke=ok")
    print(f"output_path={output_dir / 'safe_prompt.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
