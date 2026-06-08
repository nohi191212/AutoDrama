from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import StaticAssetGenerationOutput  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.nodes.director_nodes import (  # noqa: E402
    KEY_VISION_ASSET_ID,
    KEY_VISION_NAME,
)
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def run_smoke() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "key_vision_flow"
    settings.project.episode_count = 1
    settings.project.episode_duration_seconds = 30
    settings.project.bgm_count = 0
    settings.generation.visual_style_prompt = "smoke global visual style: cinematic CG, blue rain light"

    repo = ProjectRepository(settings)
    project_dir = repo.create_project(
        title="Key Vision Flow Smoke",
        raw_script=(
            "林舟在雨夜办公室发现合同被调包，苏晚递来旧邮件截图，"
            "赵启在会议室里试图压下证据，林舟决定公开反击。"
        ),
        project_id="key_vision_flow_smoke",
    )
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    state = await workflow.run(project_dir, until="design_key_vision_image", force=True)

    require("director_prep" in state.completed_nodes, "director_prep did not complete")
    require("design_key_vision_prompt" in state.completed_nodes, "design_key_vision_prompt did not complete")
    require("design_key_vision_image" in state.completed_nodes, "design_key_vision_image did not complete")
    require(
        state.metadata.get("visual_style_prompt") == settings.generation.visual_style_prompt,
        "visual_style_prompt was not synced into state metadata",
    )

    prompt_path = project_dir / "assets" / "json" / "nodes" / "design_key_vision_prompt.json"
    require(prompt_path.exists(), "design_key_vision_prompt output is missing")
    prompt_payload = json.loads(prompt_path.read_text(encoding="utf-8"))
    require(set(prompt_payload) == {"prompt"}, f"Prompt output should only contain prompt: {prompt_payload}")
    require(str(prompt_payload["prompt"]).strip(), "Prompt output is empty")

    image_output_path = project_dir / "assets" / "json" / "nodes" / "design_key_vision_image.json"
    require(image_output_path.exists(), "design_key_vision_image output is missing")
    image_output = StaticAssetGenerationOutput.model_validate_json(image_output_path.read_text(encoding="utf-8"))
    require(len(image_output.generated_assets) == 1, "Key vision image output should contain exactly one asset")
    item = image_output.generated_assets[0]
    require(item.asset_id == KEY_VISION_ASSET_ID, f"Unexpected key vision asset_id: {item.asset_id}")
    require(item.name == KEY_VISION_NAME, f"Unexpected key vision name: {item.name}")
    require(item.asset_type == "key_vision", f"Unexpected key vision asset_type: {item.asset_type}")
    require(item.asset_path is not None, "Key vision asset_path is missing")

    image_path = project_dir / item.asset_path
    require(image_path.exists(), f"Generated key vision image file is missing: {item.asset_path}")
    require(KEY_VISION_ASSET_ID in image_path.read_bytes().decode("utf-8"), "Fake image did not use key vision asset id")

    print("key_vision_flow_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"prompt_path={prompt_path.relative_to(project_dir)}")
    print(f"asset_path={item.asset_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_smoke()))
