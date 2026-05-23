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


class PrimaryExtractFailureProvider:
    name = "primary-extract-failure"
    model = "primary-extract-failure-json"

    def __init__(self) -> None:
        self.fake = FakeTextProvider()
        self.call_count = 0

    async def generate_json(
        self,
        prompt: str,
        schema,
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        if metadata.get("node_name") != "role_extract_primary":
            return await self.fake.generate_json(prompt, schema, temperature=temperature, metadata=metadata)

        self.call_count += 1
        if self.call_count == 1:
            return schema.model_validate(
                {
                    "roles": [
                        {
                            "name": "沈烬",
                            "role_tier": "primary",
                            "episode_keys": ["episode_001"],
                            "source_chapters": ["第1章"],
                            "brief": "被逐出宗门的少年剑修。",
                            "appearance_notes": ["黑衣", "残剑"],
                            "has_dialogue": True,
                            "visual_reuse_required": True,
                        }
                    ]
                }
            )
        if self.call_count == 2:
            return schema.model_validate(
                {
                    "roles": [
                        {
                            "name": "云蘅",
                            "role_tier": "primary",
                            "episode_keys": ["episode_001"],
                            "source_chapters": ["第1章"],
                            "brief": "暗中相助沈烬的灵药峰弟子。",
                            "appearance_notes": ["青衣", "药囊"],
                            "has_dialogue": True,
                            "visual_reuse_required": True,
                        }
                    ]
                }
            )
        return schema.model_validate(
            {
                "roles": [
                    {
                        "name": "韩默",
                        "role_tier": "primary",
                        "episode_keys": [],
                        "source_chapters": ["第2章"],
                        "brief": "模型错误返回的缺 episode_keys 角色。",
                        "appearance_notes": [],
                        "has_dialogue": False,
                        "visual_reuse_required": True,
                    }
                ]
            }
        )


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"role_extract_partial_persistence_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Extract Partial Persistence Smoke",
        raw_script="沈烬被逐出宗门，云蘅暗中相助。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    provider = PrimaryExtractFailureProvider()
    router._fake = provider
    workflow = PregenWorkflow(repo=repo, router=router)
    await workflow.run(project_dir, until="script_novel_extract", force=True)

    try:
        await workflow.run(project_dir, only="role_extract_primary", force=True)
    except ValueError as exc:
        require("must include episode_keys for 韩默" in str(exc), f"unexpected failure: {exc}")
    else:
        raise AssertionError("role_extract_primary should fail on missing episode_keys")

    output_path = project_dir / "assets" / "json" / "nodes" / "role_extract_primary.json"
    require(output_path.exists(), "partial role_extract_primary output was not persisted")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    role_names = [item["name"] for item in payload["roles"]]
    require(role_names == ["沈烬", "云蘅"], f"unexpected persisted roles: {role_names}")
    require(provider.call_count == 3, f"unexpected primary extract call count: {provider.call_count}")

    print("role_extract_partial_persistence_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
