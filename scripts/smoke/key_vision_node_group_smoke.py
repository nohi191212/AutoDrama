from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.cli import build_parser  # noqa: E402
from autodrama.workflows.pregen import (  # noqa: E402
    PREGEN_NODE_GROUPS,
    PregenWorkflow,
    _enabled_automatic_pregen_nodes,
)
from autodrama.workflows.nodes import IMAGE_AUDIT_NODE_NAMES  # noqa: E402


def main() -> int:
    automatic_nodes = [
        "key_vision_image_generation",
        *IMAGE_AUDIT_NODE_NAMES,
        "shot_manifest_generation",
    ]
    assert _enabled_automatic_pregen_nodes(
        automatic_nodes,
        image_audits_enabled=False,
    ) == ["key_vision_image_generation", "shot_manifest_generation"]
    assert _enabled_automatic_pregen_nodes(
        automatic_nodes,
        image_audits_enabled=True,
    ) == automatic_nodes

    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "pregen",
            "--config",
            "saodi.yaml",
            "--node_group",
            "key_vision",
            "--force",
        ]
    )
    assert args.workflow == "pregen"
    assert args.node_group == "key_vision"
    assert args.force is True
    assert PREGEN_NODE_GROUPS[args.node_group] == (
        "script_worldview_extract",
        "key_vision_prompt",
        "key_vision_image_generation",
        "key_vision_image_audit",
    )
    edit_args = parser.parse_args(
        [
            "run",
            "pregen",
            "--config",
            "saodi.yaml",
            "--node_group",
            "key_vision_edit",
            "--force",
        ]
    )
    assert edit_args.node_group == "key_vision_edit"
    assert edit_args.force is True
    assert PREGEN_NODE_GROUPS[edit_args.node_group] == (
        "key_vision_edit",
        "key_vision_image_audit",
    )
    alias_args = parser.parse_args(
        [
            "run",
            "pregen",
            "--config",
            "saodi.yaml",
            "--node-group",
            "key_vision",
        ]
    )
    assert alias_args.node_group == "key_vision"
    role_args = parser.parse_args(
        [
            "run",
            "pregen",
            "--config",
            "saodi.yaml",
            "--node-group",
            "role_extract",
            "--force",
        ]
    )
    assert role_args.node_group == "role_extract"
    assert role_args.force is True
    assert PREGEN_NODE_GROUPS[role_args.node_group] == (
        "role_extract_primary",
        "role_extract_functional",
        "role_finalize",
    )
    expected_groups = {
        "prop_layout_extract": (
            "prop_extract",
            "prop_finalize",
            "layout_extract",
            "layout_finalize",
            "layout_prop_boundary_review",
        ),
        "roleboard_gen": (
            "roleboard_prompt",
            "roleboard_image_generation",
            "roleboard_image_audit",
        ),
        "prop_gen": (
            "prop_prompt",
            "prop_image_generation",
            "prop_image_audit",
        ),
        "layout_gen": (
            "layout_prompt",
            "layout_image_generation",
            "layout_image_audit",
        ),
    }
    for group_name, node_names in expected_groups.items():
        group_args = parser.parse_args(
            [
                "run",
                "pregen",
                "--config",
                "saodi.yaml",
                "--node-group",
                group_name,
                "--force",
            ]
        )
        assert group_args.node_group == group_name
        assert PREGEN_NODE_GROUPS[group_name] == node_names

    completed_role_extract = type("State", (), {
        "completed_nodes": list(PREGEN_NODE_GROUPS["role_extract"]),
    })()
    PregenWorkflow._validate_pregen_group_prerequisites(
        completed_role_extract,
        node_group="roleboard_gen",
        only=None,
    )
    try:
        PregenWorkflow._validate_pregen_group_prerequisites(
            type("State", (), {"completed_nodes": []})(),
            node_group="roleboard_gen",
            only=None,
        )
    except ValueError as exc:
        assert "role_extract" in str(exc)
    else:
        raise AssertionError("roleboard_gen must require role_extract")
    print("pregen node group smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
