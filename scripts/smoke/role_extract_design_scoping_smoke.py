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


class RoleScopedTextProvider:
    name = "role-scoped"
    model = "role-scoped-json"

    def __init__(self) -> None:
        self.fake = FakeTextProvider()
        self.role_design_prompts: dict[str, str] = {}

    async def generate_json(
        self,
        prompt: str,
        schema,
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        node_name = metadata.get("node_name")
        if node_name not in {"role_extract", "role_design"}:
            return await self.fake.generate_json(prompt, schema, temperature=temperature, metadata=metadata)

        if node_name == "role_extract":
            return schema.model_validate(
                {
                    "roles": [
                        {
                            "name": "林舟",
                            "aliases": ["男主"],
                            "importance": "lead",
                            "episode_keys": ["episode_001"],
                            "source_chapters": ["第1章"],
                            "brief": "二十八岁职场青年，合同事件中的反击者。",
                            "appearance_notes": ["短发", "身形偏瘦"],
                        },
                        {
                            "name": "苏晚",
                            "aliases": ["女主"],
                            "importance": "main",
                            "episode_keys": ["episode_002"],
                            "source_chapters": ["第2章"],
                            "brief": "二十六岁数据分析师，提供证据线索。",
                            "appearance_notes": ["气质清冷", "身形修长"],
                        },
                    ]
                }
            )

        role_name = str(metadata["role_name"])
        self.role_design_prompts[role_name] = prompt
        episode_keys = list(metadata.get("episode_keys") or [])
        source_chapters = ["第1章"] if role_name == "林舟" else ["第2章"]
        voice_type = "zh_male_m191_uranus_bigtts" if role_name == "林舟" else "zh_female_xiaohe_uranus_bigtts"
        voice_name = "云舟 2.0" if role_name == "林舟" else "小何 2.0"
        gender_desc = "中国男性职场青年" if role_name == "林舟" else "中国女性数据分析师"
        return schema.model_validate(
            {
                "roles": [
                    {
                        "name": role_name,
                        "intro": f"{role_name}是{gender_desc}，在对应章节中承担明确剧情功能。",
                        "personality": "冷静、敏锐、克制",
                        "aliases": ["男主"] if role_name == "林舟" else ["女主"],
                        "importance": "lead" if role_name == "林舟" else "main",
                        "episode_keys": episode_keys,
                        "source_chapters": source_chapters,
                        "relationships": [],
                        "appearances": [
                            {
                                "role_name": role_name,
                                "name": "base",
                                "desc": f"{role_name}的稳定基础形象，五官清晰，气质克制。",
                                "prompt": (
                                    f"真人电影质感，左侧为{role_name}的{gender_desc}全身形象，干净背景；"
                                    "右侧为主要随身物品设计图，展示人物持握比例关系，无字幕、水印或文字标识。"
                                ),
                                "role_bound_props": [],
                                "intro_video_prompt": (
                                    f"{role_name}站在洁净、亮度适中的虚空圆台上，圆台缓慢转动，"
                                    "做几个符合身份的常见动作；背景干净抽象，无其他人物、字幕或水印。"
                                ),
                            }
                        ],
                        "voices": [
                            {
                                "role_name": role_name,
                                "emotion": "normal",
                                "voice_name": voice_name,
                                "voice_type": voice_type,
                                "voice_resource_id": "seed-tts-2.0",
                                "voice_selection_reason": "音色年龄感和人物气质匹配。",
                                "desc": "青年声音，克制清晰，语速中等。",
                                "sample_text": f"我是{role_name}，我习惯先看清眼前的细节。越是紧张的时候，我越会让自己保持冷静。",
                            }
                        ],
                    }
                ]
            }
        )


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 2
    repo = ProjectRepository(settings)
    project_id = f"role_scope_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Scope Smoke",
        raw_script="林舟在第一章发现合同异常，苏晚在第二章提供证据。",
        project_id=project_id,
        episode_count=2,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    role_provider = RoleScopedTextProvider()
    router._fake = role_provider
    workflow = PregenWorkflow(repo=repo, router=router)
    state = await workflow.run(project_dir, until="role_design", force=True)

    require(set(role_provider.role_design_prompts) == {"林舟", "苏晚"}, "role_design did not run once per role")
    lin_prompt = role_provider.role_design_prompts["林舟"]
    su_prompt = role_provider.role_design_prompts["苏晚"]
    require("episode_001，雨夜办公室" in lin_prompt, "林舟 prompt missing episode_001 full text")
    require("episode_002，雨夜办公室" not in lin_prompt, "林舟 prompt leaked episode_002 full text")
    require("episode_002，雨夜办公室" in su_prompt, "苏晚 prompt missing episode_002 full text")
    require("episode_001，雨夜办公室" not in su_prompt, "苏晚 prompt leaked episode_001 full text")

    role_extract = json.loads((project_dir / "assets" / "json" / "nodes" / "role_extract.json").read_text(encoding="utf-8"))
    require(role_extract["roles"][0]["source_chapters"] == ["第1章"], "role_extract missing source_chapters")
    require(state.roles["role_林舟"].episode_keys == ["episode_001"], "林舟 episode_keys mismatch")
    require(state.roles["role_苏晚"].episode_keys == ["episode_002"], "苏晚 episode_keys mismatch")
    require(state.roles["role_林舟"].source_chapters == ["第1章"], "林舟 source_chapters mismatch")
    require(state.metadata["role_design_generation_mode"] == "per_role_recursive", "role_design mode mismatch")

    role_provider.role_design_prompts = {}
    state = await workflow.run(
        project_dir,
        only="role_design",
        episode_keys=["episode_002"],
        force=True,
    )
    require(set(role_provider.role_design_prompts) == {"苏晚"}, "episode-scoped role_design reran unexpected roles")
    require("role_林舟" in state.roles, "episode-scoped role_design dropped non-target existing role")
    require("role_苏晚" in state.roles, "episode-scoped role_design missing target role")
    require(state.metadata["role_design_active_episode_keys"] == ["episode_002"], "active episode metadata mismatch")
    require(state.metadata["role_design_target_role_names"] == ["苏晚"], "target role metadata mismatch")
    scoped_design = json.loads((project_dir / "assets" / "json" / "nodes" / "role_design.json").read_text(encoding="utf-8"))
    scoped_role_names = {item["name"] for item in scoped_design["roles"]}
    require(scoped_role_names == {"林舟", "苏晚"}, "episode-scoped role_design node output did not preserve roles")

    print("role_extract_design_scoping_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
