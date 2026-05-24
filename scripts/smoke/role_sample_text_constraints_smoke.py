from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require_error(sample_text: str) -> None:
    try:
        PregenWorkflow._validate_voice_sample_text("sample_text", sample_text)
    except ValueError:
        return
    raise AssertionError(f"Expected sample_text to be rejected: {sample_text}")


def main() -> int:
    PregenWorkflow._validate_voice_sample_text(
        "sample_text",
        "我是林舟，一名调查员。我会保持冷静，把眼前的问题查清楚。",
    )
    require_error("我是林舟。（压低声音）我会查清楚。")
    require_error("我是林舟。内心独白：我不能让他们看出破绽。")

    print("role_sample_text_constraints_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
