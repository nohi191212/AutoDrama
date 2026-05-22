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

from autodrama.core.schemas import ProjectState, ScriptBundle  # noqa: E402
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class RecordingTextProvider(FakeTextProvider):
    def __init__(self) -> None:
        self.last_prompt = ""

    async def generate_json(self, prompt: str, schema, **kwargs):
        self.last_prompt = prompt
        return await super().generate_json(prompt, schema, **kwargs)


class Router:
    def __init__(self, provider: RecordingTextProvider) -> None:
        self.provider = provider

    def text(self, kind: str) -> RecordingTextProvider:
        if kind != "prop":
            raise AssertionError(f"Unexpected text provider kind: {kind}")
        return self.provider


class Repo:
    @staticmethod
    def write_json(path: Path, data: Any) -> None:
        ProjectRepository.write_json(path, data)

    def save_node_output(self, project_dir: Path, node_name: str, data: Any) -> Path:
        path = project_dir / "assets" / "json" / "nodes" / f"{node_name}.json"
        self.write_json(path, data)
        return path


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


async def run_smoke() -> None:
    project_dir = ROOT_DIR / ".tmp" / "smoke" / "prop_design_state_shape"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    (project_dir / "assets" / "json" / "nodes").mkdir(parents=True, exist_ok=True)

    episode_key = "episode_001"
    novel_text = "林舟在雨夜办公室发现被调包的合同，又从苏晚那里拿到邮件截图作为证据。"
    novel_path = write_script_payload(project_dir, episode_key, novel_text)

    state = ProjectState(
        project_id="prop_design_state_shape",
        title="道具状态测试",
        raw_script="合同被调包。",
        script=ScriptBundle(
            raw_script="合同被调包。",
            episode_outlines={episode_key: False},
            novel_full={episode_key: novel_path},
            novel_extract={episode_key: False},
        ),
        metadata={"episode_count": 1},
    )

    provider = RecordingTextProvider()
    workflow = PregenWorkflow(repo=Repo(), router=Router(provider))
    state = await workflow._run_prop_design(project_dir, state)

    if novel_text not in provider.last_prompt:
        raise AssertionError("prop_design prompt did not receive novel_full content")
    if "分集小说全文" not in provider.last_prompt:
        raise AssertionError("prop_design prompt did not use the novel_full input label")

    prop = state.props.get("prop_被调包的合同")
    if prop is None:
        raise AssertionError(f"Expected generated prop missing: {sorted(state.props)}")
    if prop.episode_keys != [episode_key]:
        raise AssertionError(f"Unexpected prop episode_keys: {prop.episode_keys}")
    if not prop.design_path:
        raise AssertionError("Prop design_path was not stored in state")

    state_dump = state.model_dump(mode="json")
    dumped_prop = state_dump["props"][prop.id]
    for heavy_key in ("prompt", "provider", "model", "request_id", "usage", "asset_id"):
        if heavy_key in dumped_prop:
            raise AssertionError(f"state prop unexpectedly contains {heavy_key}: {dumped_prop}")

    design_path = project_dir / prop.design_path
    payload = json.loads(design_path.read_text(encoding="utf-8"))
    content = payload.get("content") or {}
    if not content.get("prompt"):
        raise AssertionError(f"Prop design JSON did not keep prompt: {payload}")
    if content.get("episode_keys") != [episode_key]:
        raise AssertionError(f"Prop design JSON has wrong episode_keys: {payload}")

    reloaded_state = ProjectState.model_validate(state_dump)
    reloaded_prop = reloaded_state.props[prop.id]
    if reloaded_prop.prompt:
        raise AssertionError("Reloaded state unexpectedly kept prop prompt")
    recovered_prompt = workflow._prop_prompt_for_generation(project_dir, reloaded_prop)
    if recovered_prompt != content["prompt"]:
        raise AssertionError("Prop prompt was not recovered from json/props")

    print("prop_design_state_shape_smoke=ok")
    print(f"prop_design_json={prop.design_path}")


def main() -> int:
    asyncio.run(run_smoke())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
