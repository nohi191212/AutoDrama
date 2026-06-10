from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    RoleDuplicateAuditReviewOutput,
    RoleExtractItem,
    RoleExtractOutput,
)
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class DuplicateAuditTextProvider(FakeTextProvider):
    name = "duplicate-audit-smoke"
    model = "duplicate-audit-smoke-json"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        prompt: str,
        schema,
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        if schema is RoleDuplicateAuditReviewOutput or metadata.get("node_name") == "role_duplicate_audit":
            self.calls.append({"prompt": prompt, "metadata": dict(metadata)})
            return schema.model_validate(
                {
                    "duplicate_groups": [
                        {
                            "role_names": ["青袍老者", "清虚门青袍老者"],
                            "evidence": "全文中青袍老者与清虚门青袍老者均指守山传功的同一老人。",
                            "confidence": 0.94,
                        },
                        {
                            "role_names": ["不存在的角色", "苏晚"],
                            "evidence": "无效角色名应被忽略。",
                            "confidence": 0.2,
                        },
                    ]
                }
            )
        return await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)


class Router:
    def __init__(self, provider: DuplicateAuditTextProvider) -> None:
        self.provider = provider

    def text(self, purpose: str) -> DuplicateAuditTextProvider:
        require(purpose == "role", f"unexpected text purpose: {purpose}")
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 3
    repo = ProjectRepository(settings)
    provider = DuplicateAuditTextProvider()
    workflow = PregenWorkflow(repo=repo, router=Router(provider))
    project_id = f"role_duplicate_audit_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Duplicate Audit Smoke",
        raw_script="清虚门青袍老者传功，苏晚旁观。",
        project_id=project_id,
        episode_count=3,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    state.script.novel_full = {
        "episode_001": workflow.script_contents.write_content(
            project_dir,
            "novel_full",
            "episode_001",
            node_name="script_novel",
            content="第1章，青袍老者在山门前拦住少年，低声说清虚门旧约未断。",
        ),
        "episode_002": workflow.script_contents.write_content(
            project_dir,
            "novel_full",
            "episode_002",
            node_name="script_novel",
            content="第2章，清虚门青袍老者取出玉简，继续向众人解释旧约。",
        ),
        "episode_003": workflow.script_contents.write_content(
            project_dir,
            "novel_full",
            "episode_003",
            node_name="script_novel",
            content="第3章，清虚门青袍老者在殿前传功，苏晚记录每一句口诀。",
        ),
    }

    short_item = RoleExtractItem(
        name="青袍老者",
        role_tier="primary",
        aliases=["老者"],
        episode_keys=["episode_001"],
        source_chapters=["第1章"],
        brief="山门前出场的神秘老者。",
        appearance_notes=["青袍", "年长"],
        has_dialogue=True,
        visual_reuse_required=True,
    )
    keeper_item = RoleExtractItem(
        name="清虚门青袍老者",
        role_tier="functional",
        aliases=["清虚门老者"],
        episode_keys=["episode_002", "episode_003"],
        source_chapters=["第2章-第3章"],
        brief="清虚门中掌握旧约与传功线索的青袍老人。",
        appearance_notes=["青袍", "持玉简"],
        has_dialogue=True,
        visual_reuse_required=False,
    )
    other_item = RoleExtractItem(
        name="苏晚",
        role_tier="primary",
        aliases=[],
        episode_keys=["episode_003"],
        source_chapters=["第3章"],
        brief="记录口诀的旁观者。",
        appearance_notes=["青年女性"],
        has_dialogue=False,
        visual_reuse_required=True,
    )

    role_refs = {
        short_item.name: workflow.roleboard_prompts.save_extract_item(project_dir, short_item),
        keeper_item.name: workflow.roleboard_prompts.save_extract_item(project_dir, keeper_item),
        other_item.name: workflow.roleboard_prompts.save_extract_item(project_dir, other_item),
    }
    repo.save_node_output(project_dir, "role_extract_primary", RoleExtractOutput(roles=[short_item, other_item]))
    repo.save_node_output(project_dir, "role_extract_functional", RoleExtractOutput(roles=[keeper_item]))
    repo.save_node_output(project_dir, "role_extract", RoleExtractOutput(roles=[short_item, other_item, keeper_item]))
    state.metadata["role_refs"] = role_refs
    repo.save_state(project_dir, state)

    state = await workflow._run_role_duplicate_audit(project_dir, state)

    final_episode_keys = ["episode_001", "episode_002", "episode_003"]
    require(len(provider.calls) == 1, f"expected one duplicate audit call, got {len(provider.calls)}")
    require(provider.calls[0]["metadata"].get("role_count") == 3, f"unexpected role_count metadata: {provider.calls[0]}")
    require("完整小说正文" in provider.calls[0]["prompt"], "duplicate audit prompt missing novel_full block")
    require("当前角色列表" in provider.calls[0]["prompt"], "duplicate audit prompt missing role index block")
    require(state.budget.used_text_calls == 1, f"duplicate audit text calls were not counted: {state.budget.used_text_calls}")

    audit_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_duplicate_audit.json").read_text(encoding="utf-8"))
    require(audit_payload["checked_roles"] == 3, f"unexpected checked role count: {audit_payload}")
    require(audit_payload["remaining_role_names"] == ["苏晚", "清虚门青袍老者"], f"unexpected remaining roles: {audit_payload}")
    merge_item = audit_payload["merged_groups"][0]
    require(merge_item["kept_role_name"] == "清虚门青袍老者", f"keeper should be role with more episode keys: {merge_item}")
    require(merge_item["removed_role_names"] == ["青袍老者"], f"unexpected removed roles: {merge_item}")
    require(merge_item["final_episode_keys"] == final_episode_keys, f"episode keys were not unioned: {merge_item}")

    primary_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract_primary.json").read_text(encoding="utf-8"))
    require([item["name"] for item in primary_payload["roles"]] == ["苏晚"], f"primary output still contains duplicate: {primary_payload}")

    functional_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract_functional.json").read_text(encoding="utf-8"))
    require([item["name"] for item in functional_payload["roles"]] == ["清虚门青袍老者"], f"functional output mismatch: {functional_payload}")
    require(functional_payload["roles"][0]["episode_keys"] == final_episode_keys, f"functional keeper keys mismatch: {functional_payload}")
    require(functional_payload["roles"][0]["brief"] == keeper_item.brief, f"non-episode fields should come from keeper: {functional_payload}")

    extract_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract.json").read_text(encoding="utf-8"))
    extract_by_role = {item["name"]: item for item in extract_payload["roles"]}
    require(list(extract_by_role) == ["苏晚", "清虚门青袍老者"], f"role_extract order/content mismatch: {extract_payload}")
    require(extract_by_role["清虚门青袍老者"]["episode_keys"] == final_episode_keys, f"role_extract keeper keys mismatch: {extract_by_role}")
    require(extract_by_role["清虚门青袍老者"]["brief"] == keeper_item.brief, f"role_extract should keep keeper fields: {extract_by_role}")

    keeper_role_path = project_dir / state.metadata["role_refs"]["清虚门青袍老者"]
    keeper_role_payload = json.loads(keeper_role_path.read_text(encoding="utf-8"))
    require(keeper_role_payload["extract"]["episode_keys"] == final_episode_keys, f"keeper role JSON not updated: {keeper_role_payload}")
    require(keeper_role_payload["extract"]["brief"] == keeper_item.brief, f"keeper role JSON should preserve keeper fields: {keeper_role_payload}")
    require("青袍老者" not in state.metadata["role_refs"], f"removed role is still referenced: {state.metadata['role_refs']}")
    require(set(state.metadata["role_refs"]) == {"苏晚", "清虚门青袍老者"}, f"state role refs mismatch: {state.metadata['role_refs']}")
    require(state.metadata["role_duplicate_audit"]["merged_groups"] == 1, f"metadata mismatch: {state.metadata}")

    saved_state_payload = json.loads((project_dir / "state.json").read_text(encoding="utf-8"))
    require(set(saved_state_payload["roles"]) == {"苏晚", "清虚门青袍老者"}, f"saved state role refs mismatch: {saved_state_payload['roles']}")

    print("role_duplicate_audit_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
