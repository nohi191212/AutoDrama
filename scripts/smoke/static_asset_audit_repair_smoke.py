from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.workflows.nodes.image_audit_nodes import (  # noqa: E402
    ImageAuditDecision,
    ImageAuditNodeBase,
    LayoutImageAuditNode,
    PropImageAuditNode,
    RoleboardImageAuditNode,
)


def main() -> int:
    first = ImageAuditNodeBase._compose_repair_prompt(
        current_prompt="base prompt",
        decision=ImageAuditDecision(
            approved=False,
            issues=["角色板出现额外主体"],
            revised_prompt="revised prompt",
        ),
    )
    assert "revised prompt" in first
    assert "角色板出现额外主体" in first
    assert first.count(ImageAuditNodeBase.REPAIR_FEEDBACK_MARKER) == 1

    second = ImageAuditNodeBase._compose_repair_prompt(
        current_prompt=first,
        decision=ImageAuditDecision(
            approved=False,
            issues=["侧面视图身份漂移"],
            revised_prompt=first,
        ),
    )
    assert "侧面视图身份漂移" in second
    assert "角色板出现额外主体" not in second
    assert second.count(ImageAuditNodeBase.REPAIR_FEEDBACK_MARKER) == 1

    assert RoleboardImageAuditNode.MAX_REPAIR_ATTEMPTS == 2
    assert PropImageAuditNode.MAX_REPAIR_ATTEMPTS == 2
    assert LayoutImageAuditNode.MAX_REPAIR_ATTEMPTS == 2
    print("static asset audit repair smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
