from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.schemas import (  # noqa: E402
    RoleAppearanceDesignItem,
    RoleDesignItem,
    RoleDesignOutput,
    RoleExtractItem,
    RoleVoiceItem,
)
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def voice(role_name: str, sample_text: str) -> RoleVoiceItem:
    return RoleVoiceItem(
        role_name=role_name,
        emotion="normal",
        desc="年轻男性，语气沉稳。",
        sample_text=sample_text,
    )


def appearance(role_name: str, desc: str) -> RoleAppearanceDesignItem:
    return RoleAppearanceDesignItem(
        role_name=role_name,
        name="base",
        desc=desc,
        prompt="角色设定图，干净背景，无字幕水印。",
    )


def role_item(
    *,
    name: str,
    intro: str,
    aliases: list[str],
    appearance_desc: str,
    sample_text: str,
) -> RoleDesignItem:
    return RoleDesignItem(
        name=name,
        intro=intro,
        aliases=aliases,
        appearances=[appearance(name, appearance_desc)],
        voices=[voice(name, sample_text)],
    )


def expect_value_error(func, expected: str) -> None:
    try:
        func()
    except ValueError as exc:
        require(expected in str(exc), f"Expected error containing {expected!r}, got {exc!r}")
    else:
        raise AssertionError("Expected ValueError")


def main() -> None:
    workflow = object.__new__(PregenWorkflow)
    han = RoleExtractItem(name="韩默", aliases=["韩小子"])
    zhou = RoleExtractItem(name="周瑾", aliases=["周道友", "周少爷"])
    extract_roles = [han, zhou]

    mismatched_output = RoleDesignOutput(
        roles=[
            role_item(
                name="周瑾",
                intro="周瑾，清河郡修仙世家子弟。",
                aliases=["周道友"],
                appearance_desc="周瑾，身形偏瘦，身穿竹青锦袍。",
                sample_text="在下周瑾，清河周家子弟。",
            )
        ]
    )
    expect_value_error(
        lambda: workflow._select_role_design_item(mismatched_output, han),
        "refusing to relabel",
    )

    mislabeled_item = role_item(
        name="韩默",
        intro="周瑾，清河郡修仙世家子弟。",
        aliases=["韩小子", "周道友"],
        appearance_desc="周瑾，身形偏瘦，身穿竹青锦袍。",
        sample_text="在下周瑾，清河周家子弟。",
    )
    expect_value_error(
        lambda: workflow._validate_role_design_item_identity(mislabeled_item, han, extract_roles),
        "another role",
    )

    valid_item = role_item(
        name="韩默",
        intro="韩默，十九岁散修炼丹师，清瘦坚韧。",
        aliases=["韩小子"],
        appearance_desc="韩默，清瘦青年，常穿灰布短褐。",
        sample_text="我叫韩默，只想在这秘境里活下去。",
    )
    workflow._validate_role_design_item_identity(valid_item, han, extract_roles)

    print("role_design_identity_guard_smoke=ok")


if __name__ == "__main__":
    main()
