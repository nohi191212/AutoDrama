"""Exercise the directory-only prompt store and deterministic renderer contract."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.utils.prompts import PromptStore  # noqa: E402


def expect_error(error_type: type[Exception], callback) -> None:
    try:
        callback()
    except error_type:
        return
    raise AssertionError(f"Expected {error_type.__name__}")


def main() -> None:
    work_dir = ROOT / ".tmp" / "prompt_store_contract_smoke"
    shutil.rmtree(work_dir, ignore_errors=True)
    try:
        (work_dir / "valid").mkdir(parents=True)
        (work_dir / "valid" / "default.md").write_text("你好，{{ name }}。", encoding="utf-8", newline="\n")
        (work_dir / "root_only.md").write_text("旧扁平模板", encoding="utf-8", newline="\n")
        (work_dir / "invalid").mkdir()
        (work_dir / "invalid" / "default.md").write_text("{{ shot.id }}", encoding="utf-8", newline="\n")
        (work_dir / "empty_renderer").mkdir()
        (work_dir / "empty_renderer" / "default.md").write_text("任务", encoding="utf-8", newline="\n")
        (work_dir / "empty_renderer" / "render.py").write_text(
            "def render(model_output, context):\n    return '   '\n",
            encoding="utf-8",
            newline="\n",
        )

        store = PromptStore(work_dir)
        assert store.render("valid", name="小林") == "你好，小林。"
        assert store.render_context("valid", {"name": "小林", "unused": "不应传入"}) == "你好，小林。"
        expect_error(ValueError, lambda: store.render("valid"))
        expect_error(ValueError, lambda: store.render("valid", name="小林", unused="x"))
        expect_error(FileNotFoundError, lambda: store.render("root_only"))
        expect_error(ValueError, lambda: store.render("invalid"))
        expect_error(ValueError, lambda: store.render_final("empty_renderer", "有效内容"))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    print("prompt_store_contract_smoke: ok")


if __name__ == "__main__":
    main()
