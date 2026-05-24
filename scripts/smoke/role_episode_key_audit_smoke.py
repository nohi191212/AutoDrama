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
from autodrama.core.ids import normalize_id  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Role,
    RoleDesignItem,
    RoleDesignOutput,
    RoleEpisodeKeyAuditReviewOutput,
    RoleExtractItem,
    RoleExtractOutput,
)
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class AuditTextProvider(FakeTextProvider):
    name = "audit-smoke"
    model = "audit-smoke-json"

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
        if schema is RoleEpisodeKeyAuditReviewOutput or metadata.get("node_name") == "role_episode_key_audit":
            role_name = str(metadata.get("role_name") or "林舟")
            self.calls.append({"prompt": prompt, "metadata": dict(metadata)})
            if role_name == "陈默":
                return schema.model_validate(
                    {
                        "role_name": role_name,
                        "missing_episode_keys": [],
                        "missing_source_chapters": ["第2章"],
                        "evidence": "只返回章节线索时不应触发写回；模型必须直接给 missing_episode_keys。",
                        "confidence": 0.7,
                    }
                )
            return schema.model_validate(
                {
                    "role_name": role_name,
                    "missing_episode_keys": ["episode_002", "episode_001", "episode_999"],
                    "missing_source_chapters": ["第2章", "第1章"],
                    "evidence": "episode_002 中林舟继续追查合同调包；episode_001 已存在，episode_999 不属于全集。",
                    "confidence": 0.93,
                }
            )
        return await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)


class Router:
    def __init__(self, provider: AuditTextProvider) -> None:
        self.provider = provider

    def text(self, purpose: str) -> AuditTextProvider:
        require(purpose == "role", f"unexpected text purpose: {purpose}")
        return self.provider


def install_role_json(
    *,
    repo: ProjectRepository,
    workflow: PregenWorkflow,
    project_dir: Path,
    extract_item: RoleExtractItem,
) -> tuple[str, Role, RoleDesignItem, str, Path]:
    role_id = normalize_id("role", extract_item.name)
    role = Role(
        id=role_id,
        name=extract_item.name,
        intro=extract_item.brief or "测试角色。",
        design_path=workflow.role_designs.item_relative_path(project_dir, role_id),
        role_tier=extract_item.role_tier,
        aliases=list(extract_item.aliases),
        episode_keys=list(extract_item.episode_keys),
        source_chapters=list(extract_item.source_chapters),
    )
    design_item = RoleDesignItem(
        name=extract_item.name,
        intro=extract_item.brief or "测试角色。",
        aliases=list(extract_item.aliases),
        role_tier=extract_item.role_tier,
        episode_keys=list(extract_item.episode_keys),
        source_chapters=list(extract_item.source_chapters),
    )
    role_json_ref = workflow.role_designs.save_extract_item(project_dir, extract_item)
    role_json_path = project_dir / role_json_ref
    role_payload = json.loads(role_json_path.read_text(encoding="utf-8"))
    role_payload["design"] = design_item.model_dump(mode="json")
    role_payload["state_role"] = role.model_dump(mode="json")
    repo.write_json(role_json_path, role_payload)
    return role_id, role, design_item, role_json_ref, role_json_path


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 2
    repo = ProjectRepository(settings)
    provider = AuditTextProvider()
    workflow = PregenWorkflow(repo=repo, router=Router(provider))
    project_id = f"role_episode_key_audit_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Episode Key Audit Smoke",
        raw_script="林舟追查合同调包。陈默只在章节线索中被误报。",
        project_id=project_id,
        episode_count=2,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    state.script.novel_full = {
        "episode_001": workflow.script_contents.write_content(
            project_dir,
            "novel_full",
            "episode_001",
            node_name="script_novel",
            content="第1章，雨夜办公室里，林舟发现合同关键页被调包。陈默只在旧备注中出现。",
        ),
        "episode_002": workflow.script_contents.write_content(
            project_dir,
            "novel_full",
            "episode_002",
            node_name="script_novel",
            content="第2章，林舟在会议前继续追查合同调包，苏晚递来新证据。",
        ),
    }

    lin_original_episode_keys = ["episode_001", "episode_999"]
    lin_original_source_chapters = ["第1章"]
    lin_item = RoleExtractItem(
        name="林舟",
        role_tier="primary",
        aliases=["阿舟"],
        episode_keys=lin_original_episode_keys,
        source_chapters=lin_original_source_chapters,
        brief="追查合同调包的青年。",
        appearance_notes=["短发", "疲惫但冷静"],
        has_dialogue=True,
        visual_reuse_required=True,
    )
    chen_original_episode_keys = ["episode_001"]
    chen_original_source_chapters = ["第1章"]
    chen_item = RoleExtractItem(
        name="陈默",
        role_tier="functional",
        aliases=[],
        episode_keys=chen_original_episode_keys,
        source_chapters=chen_original_source_chapters,
        brief="用于验证 source_chapters-only 不会写回的角色。",
        appearance_notes=["沉默"],
        has_dialogue=False,
        visual_reuse_required=False,
    )

    lin_role_id, lin_role, lin_design, lin_role_json_ref, lin_role_json_path = install_role_json(
        repo=repo,
        workflow=workflow,
        project_dir=project_dir,
        extract_item=lin_item,
    )
    chen_role_id, chen_role, chen_design, chen_role_json_ref, chen_role_json_path = install_role_json(
        repo=repo,
        workflow=workflow,
        project_dir=project_dir,
        extract_item=chen_item,
    )

    repo.save_node_output(project_dir, "role_extract_primary", RoleExtractOutput(roles=[lin_item]))
    repo.save_node_output(project_dir, "role_extract_functional", RoleExtractOutput(roles=[chen_item]))
    repo.save_node_output(project_dir, "role_extract", RoleExtractOutput(roles=[lin_item, chen_item]))
    repo.save_node_output(project_dir, "role_design", RoleDesignOutput(roles=[lin_design, chen_design]))
    state.roles[lin_role_id] = lin_role
    state.roles[chen_role_id] = chen_role
    state.metadata["role_refs"] = {
        lin_item.name: lin_role_json_ref,
        chen_item.name: chen_role_json_ref,
    }
    repo.save_state(project_dir, state)

    state = await workflow._run_role_episode_key_audit(project_dir, state)

    lin_final_episode_keys = ["episode_001", "episode_999", "episode_002"]
    lin_final_source_chapters = ["第1章", "第2章"]
    require(len(provider.calls) == 2, f"expected two audit calls, got {len(provider.calls)}")
    require({call["metadata"].get("role_name") for call in provider.calls} == {"林舟", "陈默"}, "audit metadata should carry role names")
    require("当前角色 JSON" in provider.calls[0]["prompt"], "audit prompt missing role JSON block")
    require("完整小说正文" in provider.calls[0]["prompt"], "audit prompt missing novel_full block")
    require(state.budget.used_text_calls == 2, f"audit text calls were not counted: {state.budget.used_text_calls}")

    audit_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_episode_key_audit.json").read_text(encoding="utf-8"))
    audit_by_role = {item["role_name"]: item for item in audit_payload["audited_roles"]}
    lin_audit = audit_by_role["林舟"]
    chen_audit = audit_by_role["陈默"]
    require(audit_payload["concurrency"] == 30, f"unexpected concurrency: {audit_payload}")
    require(lin_audit["missing_episode_keys"] == ["episode_002", "episode_001", "episode_999"], f"unexpected missing keys: {lin_audit}")
    require(lin_audit["added_episode_keys"] == ["episode_002"], f"unexpected added keys: {lin_audit}")
    require(set(lin_audit["ignored_episode_keys"]) == {"episode_001", "episode_999"}, f"unexpected ignored keys: {lin_audit}")
    require(lin_audit["final_episode_keys"] == lin_final_episode_keys, f"final audit keys should only append: {lin_audit}")
    require(lin_audit["added_source_chapters"] == ["第2章"], f"source chapters should only accompany added keys: {lin_audit}")
    require(chen_audit["added_episode_keys"] == [], f"source-only audit must not add episode keys: {chen_audit}")
    require(chen_audit["added_source_chapters"] == [], f"source-only audit must not add chapters: {chen_audit}")
    require(chen_audit["final_source_chapters"] == chen_original_source_chapters, f"source-only audit changed final chapters: {chen_audit}")

    primary_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract_primary.json").read_text(encoding="utf-8"))
    primary_item = primary_payload["roles"][0]
    require(primary_item["episode_keys"] == lin_final_episode_keys, f"role_extract_primary did not append audit key: {primary_item}")
    require(primary_item["source_chapters"] == lin_final_source_chapters, f"role_extract_primary source chapters mismatch: {primary_item}")

    functional_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract_functional.json").read_text(encoding="utf-8"))
    functional_item = functional_payload["roles"][0]
    require(functional_item["episode_keys"] == chen_original_episode_keys, f"source-only role episode keys changed: {functional_item}")
    require(functional_item["source_chapters"] == chen_original_source_chapters, f"source-only role chapters changed: {functional_item}")

    extract_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract.json").read_text(encoding="utf-8"))
    extract_by_role = {item["name"]: item for item in extract_payload["roles"]}
    require(extract_by_role["林舟"]["episode_keys"] == lin_final_episode_keys, f"role_extract did not append audit key: {extract_by_role['林舟']}")
    require(extract_by_role["林舟"]["source_chapters"] == lin_final_source_chapters, f"role_extract source chapters mismatch: {extract_by_role['林舟']}")
    require(extract_by_role["陈默"]["episode_keys"] == chen_original_episode_keys, f"role_extract source-only keys changed: {extract_by_role['陈默']}")
    require(extract_by_role["陈默"]["source_chapters"] == chen_original_source_chapters, f"role_extract source-only chapters changed: {extract_by_role['陈默']}")

    design_payload = json.loads((project_dir / "assets" / "json" / "nodes" / "role_design.json").read_text(encoding="utf-8"))
    design_by_role = {item["name"]: item for item in design_payload["roles"]}
    require(design_by_role["林舟"]["episode_keys"] == lin_final_episode_keys, f"role_design did not append audit key: {design_by_role['林舟']}")
    require(design_by_role["陈默"]["episode_keys"] == chen_original_episode_keys, f"role_design source-only keys changed: {design_by_role['陈默']}")

    lin_role_payload = json.loads(lin_role_json_path.read_text(encoding="utf-8"))
    for section_name in ("extract", "design", "state_role"):
        section = lin_role_payload[section_name]
        require(section["episode_keys"] == lin_final_episode_keys, f"{section_name} did not append audit key: {section}")
        require(section["source_chapters"] == lin_final_source_chapters, f"{section_name} source chapters mismatch: {section}")

    chen_role_payload = json.loads(chen_role_json_path.read_text(encoding="utf-8"))
    for section_name in ("extract", "design", "state_role"):
        section = chen_role_payload[section_name]
        require(section["episode_keys"] == chen_original_episode_keys, f"{section_name} source-only keys changed: {section}")
        require(section["source_chapters"] == chen_original_source_chapters, f"{section_name} source-only chapters changed: {section}")

    require(state.roles[lin_role_id].episode_keys == lin_final_episode_keys, f"state role keys mismatch: {state.roles[lin_role_id]}")
    require(state.roles[lin_role_id].source_chapters == lin_final_source_chapters, f"state source chapters mismatch: {state.roles[lin_role_id]}")
    require(state.roles[chen_role_id].episode_keys == chen_original_episode_keys, f"state source-only keys changed: {state.roles[chen_role_id]}")
    require(state.roles[chen_role_id].source_chapters == chen_original_source_chapters, f"state source-only chapters changed: {state.roles[chen_role_id]}")
    require(state.metadata["role_episode_key_audit"]["updated_roles"] == 1, f"unexpected audit metadata: {state.metadata}")

    print("role_episode_key_audit_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
