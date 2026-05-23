from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.providers.local.mock.fake import FakeImageProvider, FakeTextProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class PropScopedTextProvider:
    name = "prop-scoped"
    model = "prop-scoped-json"

    def __init__(self) -> None:
        self.fake = FakeTextProvider()
        self.prop_design_calls: list[str] = []

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
        if node_name == "prop_extract":
            return schema.model_validate(
                {
                    "props": [
                        {
                            "name": "第一集合约",
                            "status": "normal",
                            "episode_keys": ["episode_001"],
                            "source_chapters": ["第1章"],
                            "brief": "第一集出现的证据合同。",
                            "appearance_notes": ["浅色纸张", "红色批注"],
                        },
                        {
                            "name": "第二集硬盘",
                            "status": "normal",
                            "episode_keys": ["episode_002"],
                            "source_chapters": ["第2章"],
                            "brief": "第二集出现的数据证据。",
                            "appearance_notes": ["黑色移动硬盘", "银色划痕"],
                        },
                    ]
                }
            )
        if node_name == "prop_design":
            prop_name = str(metadata["prop_name"])
            episode_keys = [str(key) for key in metadata.get("episode_keys", [])]
            self.prop_design_calls.append(prop_name)
            return schema.model_validate(
                {
                    "props": [
                        {
                            "name": prop_name,
                            "desc": f"{prop_name}的道具设计，关键视觉特征清晰。",
                            "prompt": f"真人电影质感，无人物道具图，{prop_name}放在干净桌面上，材质细节清晰。",
                            "status": str(metadata.get("prop_status") or "normal"),
                            "episode_keys": episode_keys,
                        }
                    ]
                }
            )
        return await self.fake.generate_json(prompt, schema, temperature=temperature, metadata=metadata)


class RecordingImageProvider(FakeImageProvider):
    def __init__(self) -> None:
        self.generated_asset_ids: list[str] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        metadata = metadata or {}
        self.generated_asset_ids.append(str(metadata.get("asset_id")))
        return await super().generate_image(prompt, refs=refs, size=size, metadata=metadata)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_script_payload(project_dir: Path, episode_key: str, content: str) -> str:
    path = project_dir / "assets" / "json" / "scripts" / "novel_full" / f"{episode_key}.json"
    ProjectRepository.write_json(
        path,
        {
            "node_name": "script_novel",
            "episode_key": episode_key,
            "content": content,
        },
    )
    return str(path.relative_to(project_dir)).replace("\\", "/")


async def main_async() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 2
    settings.project.episode_duration_seconds = 30

    project_dir = settings.output.root_dir / "prop_episode_scoping"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    repo = ProjectRepository(settings)
    project_dir = repo.create_project(
        title="Prop Episode Scoping",
        raw_script="第一集出现合同，第二集出现硬盘。",
        project_id="prop_episode_scoping",
        episode_count=2,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    state.script.novel_full = {
        "episode_001": write_script_payload(project_dir, "episode_001", "第一集，主角发现第一集合约。"),
        "episode_002": write_script_payload(project_dir, "episode_002", "第二集，主角找到第二集硬盘。"),
    }
    repo.save_state(project_dir, state)

    router = ProviderRouter(settings, provider_override="fake")
    text_provider = PropScopedTextProvider()
    image_provider = RecordingImageProvider()
    router._fake = text_provider
    router._fake_image = image_provider

    workflow = PregenWorkflow(repo=repo, router=router)
    await workflow.run(project_dir, only="prop_extract", force=True)
    first_prop_path = project_dir / "assets" / "json" / "props" / "prop_第一集合约.json"
    first_extract_payload = json.loads(first_prop_path.read_text(encoding="utf-8"))
    require(first_extract_payload["node_name"] == "prop_extract", "prop_extract did not write per-prop extract JSON")
    require(first_extract_payload["content"]["name"] == "第一集合约", "per-prop extract JSON content mismatch")

    state = await workflow.run(project_dir, only="prop_design", force=True)
    require(set(text_provider.prop_design_calls) == {"第一集合约", "第二集硬盘"}, "initial prop_design did not design all props")
    require(set(state.props) == {"prop_第一集合约", "prop_第二集硬盘"}, f"unexpected props: {sorted(state.props)}")
    first_design_payload = json.loads(first_prop_path.read_text(encoding="utf-8"))
    require(first_design_payload["node_name"] == "prop_design", "prop_design did not update per-prop JSON")
    require(first_design_payload["content"]["prompt"], "per-prop design JSON missing prompt")
    require(first_design_payload["extract_content"]["name"] == "第一集合约", "prop_design did not preserve extract_content")

    text_provider.prop_design_calls = []
    state = await workflow.run(project_dir, only="prop_design", episode_keys=["episode_002"], force=True)
    require(text_provider.prop_design_calls == ["第二集硬盘"], f"unexpected scoped prop_design calls: {text_provider.prop_design_calls}")
    require("prop_第一集合约" in state.props, "episode-scoped prop_design dropped non-target prop")
    require(state.metadata["prop_design_active_episode_keys"] == ["episode_002"], "prop active episode metadata mismatch")
    require(state.metadata["prop_design_target_prop_names"] == ["第二集硬盘"], "prop target metadata mismatch")
    design_output = json.loads((project_dir / "assets" / "json" / "nodes" / "prop_design.json").read_text(encoding="utf-8"))
    require({item["name"] for item in design_output["props"]} == {"第一集合约", "第二集硬盘"}, "scoped prop_design output did not preserve props")

    image_provider.generated_asset_ids = []
    await workflow.run(project_dir, only="prop_generation", episode_keys=["episode_002"], force=True)
    require(image_provider.generated_asset_ids == ["prop_第二集硬盘"], f"unexpected scoped prop_generation calls: {image_provider.generated_asset_ids}")

    image_provider.generated_asset_ids = []
    await workflow.run(project_dir, only="prop_image_generation", episode_keys=["episode_001"], force=True)
    require(image_provider.generated_asset_ids == ["prop_第一集合约"], "legacy prop_image_generation alias did not scope to episode_001")

    print("prop_episode_scoping_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
