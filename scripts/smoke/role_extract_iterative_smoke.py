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
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def tuple_list(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append((str(item[0]), str(item[1])))
    return result


class SplitRoleExtractProvider:
    name = "split-role-extract"
    model = "split-role-extract-json"

    def __init__(self) -> None:
        self.fake = FakeTextProvider()
        self.primary_existing_by_call: list[list[tuple[str, str]]] = []
        self.functional_primary_by_call: list[list[tuple[str, str]]] = []
        self.functional_existing_by_call: list[list[tuple[str, str]]] = []
        self.prompts_by_node: dict[str, list[str]] = {
            "role_extract_primary": [],
            "role_extract_functional": [],
            "ambient_entity_extract": [],
        }
        self.primary_templates = [
            {
                "name": "沈烬",
                "role_tier": "primary",
                "aliases": ["少宗主"],
                "episode_keys": ["episode_001"],
                "source_chapters": ["第1章"],
                "brief": "被逐出宗门的少年剑修，在山门禁阵前寻找师父失踪线索。",
                "appearance_notes": ["少年剑修", "黑衣", "背负残剑"],
                "has_dialogue": True,
                "visual_reuse_required": True,
            },
            {
                "name": "云蘅",
                "role_tier": "primary",
                "aliases": ["灵药峰师姐"],
                "episode_keys": ["episode_001"],
                "source_chapters": ["第1章"],
                "brief": "灵药峰弟子，暗中把禁阵残片交给沈烬。",
                "appearance_notes": ["青衣", "药囊", "气质清冷"],
                "has_dialogue": True,
                "visual_reuse_required": True,
            },
            {
                "name": "玄霄真人",
                "role_tier": "primary",
                "aliases": ["师父"],
                "episode_keys": ["episode_001"],
                "source_chapters": ["第1章"],
                "brief": "沈烬失踪的师父，其线索牵动山门禁阵和后续冲突。",
                "appearance_notes": ["高阶剑修", "白发", "旧剑穗"],
                "has_dialogue": False,
                "visual_reuse_required": True,
            },
            {
                "name": "赤焰魔君",
                "role_tier": "primary",
                "aliases": ["魔君"],
                "episode_keys": ["episode_001"],
                "source_chapters": ["第1章"],
                "brief": "暗中设局的核心反派，持续影响沈烬与青霄宗的冲突。",
                "appearance_notes": ["赤色魔纹", "黑金法袍"],
                "has_dialogue": False,
                "visual_reuse_required": True,
            },
        ]
        self.functional_templates = [
            {
                "name": "山门守卫",
                "role_tier": "functional",
                "aliases": ["守山弟子"],
                "episode_keys": ["episode_001"],
                "source_chapters": ["第1章"],
                "brief": "守在青霄宗山门前的无名弟子，持令牌阻拦沈烬入山。",
                "appearance_notes": ["灰袍", "佩剑", "腰悬山门令牌"],
                "has_dialogue": False,
                "visual_reuse_required": False,
            }
        ]

    async def generate_json(
        self,
        prompt: str,
        schema,
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        node_name = str(metadata.get("node_name") or "")
        if node_name not in {"role_extract_primary", "role_extract_functional", "ambient_entity_extract"}:
            return await self.fake.generate_json(prompt, schema, temperature=temperature, metadata=metadata)

        self.prompts_by_node[node_name].append(prompt)
        if node_name == "ambient_entity_extract":
            return schema.model_validate(
                {
                    "entities": [
                        {
                            "name": "围观修士",
                            "entity_type": "crowd",
                            "episode_keys": ["episode_001"],
                            "description": "聚在青霄宗山门外观望冲突的低阶修士人群。",
                            "visual_notes": ["青灰道袍", "远景", "不可辨认具体面孔"],
                            "usage": "作为山门冲突场景背景，不生成独立角色资产。",
                        }
                    ]
                }
            )

        if node_name == "role_extract_primary":
            existing_roles = tuple_list(metadata.get("existing_primary_roles", []))
            self.primary_existing_by_call.append(existing_roles)
            existing = {name for name, _ in existing_roles}
            for item in self.primary_templates:
                if item["name"] not in existing:
                    return schema.model_validate({"roles": [item]})
            return schema.model_validate({"roles": []})

        primary_roles = tuple_list(metadata.get("primary_roles", []))
        functional_roles = tuple_list(metadata.get("functional_roles", []))
        self.functional_primary_by_call.append(primary_roles)
        self.functional_existing_by_call.append(functional_roles)
        existing = {name for name, _ in functional_roles}
        for item in self.functional_templates:
            if item["name"] not in existing:
                return schema.model_validate({"roles": [item]})
        return schema.model_validate({"roles": []})


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"role_extract_iterative_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Extract Iterative Smoke",
        raw_script=(
            "沈烬被逐出宗门，云蘅暗中相助，玄霄真人失踪，赤焰魔君设局。"
            "山门守卫阻拦沈烬，围观修士聚在山门外。"
        ),
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    provider = SplitRoleExtractProvider()
    router._fake = provider
    workflow = PregenWorkflow(repo=repo, router=router)
    state = await workflow.run(project_dir, until="ambient_entity_extract", force=True)

    require(provider.primary_existing_by_call[0] == [], "primary extract first call should receive no existing roles")
    require(
        provider.primary_existing_by_call[-1]
        == [
            ("沈烬", "被逐出宗门的少年剑修，在山门禁阵前寻找师父失踪线索。"),
            ("云蘅", "灵药峰弟子，暗中把禁阵残片交给沈烬。"),
            ("玄霄真人", "沈烬失踪的师父，其线索牵动山门禁阵和后续冲突。"),
            ("赤焰魔君", "暗中设局的核心反派，持续影响沈烬与青霄宗的冲突。"),
        ],
        f"unexpected primary existing tuple inputs: {provider.primary_existing_by_call[-1]}",
    )
    require(provider.functional_primary_by_call, "functional extract was not called")
    require(
        provider.functional_primary_by_call[0]
        == [
            ("沈烬", "被逐出宗门的少年剑修，在山门禁阵前寻找师父失踪线索。"),
            ("云蘅", "灵药峰弟子，暗中把禁阵残片交给沈烬。"),
            ("玄霄真人", "沈烬失踪的师父，其线索牵动山门禁阵和后续冲突。"),
            ("赤焰魔君", "暗中设局的核心反派，持续影响沈烬与青霄宗的冲突。"),
        ],
        f"functional extract did not receive separated primary roles: {provider.functional_primary_by_call[0]}",
    )
    require(provider.functional_existing_by_call[0] == [], "functional first call should receive no functional roles")
    require(
        provider.functional_existing_by_call[-1]
        == [("山门守卫", "守在青霄宗山门前的无名弟子，持令牌阻拦沈烬入山。")],
        f"functional existing role tuples mismatch: {provider.functional_existing_by_call[-1]}",
    )
    require("已抽取主要角色列表" in provider.prompts_by_node["role_extract_primary"][0], "primary prompt missing existing list")
    require("主要角色列表" in provider.prompts_by_node["role_extract_functional"][0], "functional prompt missing primary list")
    require("已抽取功能角色列表" in provider.prompts_by_node["role_extract_functional"][0], "functional prompt missing functional list")

    primary_path = project_dir / "assets" / "json" / "nodes" / "role_extract_primary.json"
    functional_path = project_dir / "assets" / "json" / "nodes" / "role_extract_functional.json"
    merged_path = project_dir / "assets" / "json" / "nodes" / "role_extract.json"
    require(primary_path.exists(), "role_extract_primary output missing")
    require(functional_path.exists(), "role_extract_functional output missing")
    require(merged_path.exists(), "role_extract output missing")

    role_extract = json.loads(merged_path.read_text(encoding="utf-8"))
    role_names = [item["name"] for item in role_extract["roles"]]
    require(
        role_names == ["沈烬", "云蘅", "玄霄真人", "赤焰魔君", "山门守卫"],
        f"unexpected final role order: {role_names}",
    )
    role_tiers = {item["name"]: item["role_tier"] for item in role_extract["roles"]}
    require(role_tiers["沈烬"] == "primary", "沈烬 should be primary")
    require(role_tiers["山门守卫"] == "functional", "山门守卫 should be functional")
    require("围观修士" not in role_names, "ambient crowd should not enter role_extract roles")
    require("importance" not in role_extract["roles"][0], "role_extract should not serialize legacy importance")
    ambient_path = project_dir / "assets" / "json" / "assets" / "ambient_entities.json"
    require(ambient_path.exists(), "ambient_entities.json output missing")
    ambient_entities = json.loads(ambient_path.read_text(encoding="utf-8"))
    require(
        [item["name"] for item in ambient_entities["entities"]] == ["围观修士"],
        f"unexpected ambient entities: {ambient_entities}",
    )
    require(state.budget.used_text_calls >= 8, "split role/ambient extraction calls were not counted as text calls")

    state_payload = json.loads((project_dir / "state.json").read_text(encoding="utf-8"))
    role_refs = state_payload.get("roles", {})
    require(set(role_refs) == set(role_names), f"unexpected state role refs: {role_refs}")
    require("role_extract" not in state_payload.get("metadata", {}), "state metadata should not contain role_extract")

    print("role_extract_iterative_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
