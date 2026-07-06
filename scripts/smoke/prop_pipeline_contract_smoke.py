from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.config import load_settings
from autodrama.core.schemas import ProjectState, ScriptBundle
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.workflows.pregen import PregenWorkflow


async def main() -> None:
    load_settings(ROOT / "config.yaml")
    load_settings(ROOT / "config.yaml.example")
    settings = load_settings(ROOT / "config.yaml")
    settings.output.root_dir = (ROOT / ".tmp" / "prop_pipeline_smoke").resolve()
    settings.project.id = "prop_pipeline_smoke"
    settings.project.episode_count = 1

    repo = ProjectRepository(settings)
    project_dir = settings.output.root_dir / "prop_pipeline_smoke"
    project_dir.mkdir(parents=True, exist_ok=True)
    for subdir in repo.layout.required_subdirs(project_dir):
        subdir.mkdir(parents=True, exist_ok=True)

    episode_key = "episode_001"
    story_text = (
        "雨夜办公室里，林舟把被调包的合同平放在桌上，关键页颜色明显偏浅。"
        "赵启夺过合同撕坏边角，合同_破损的状态在镜头里被苏晚拍下。"
        "随后苏晚把邮件截图投到电脑屏幕上，附件时间线成为反击证据。"
        "远处只被台词提到的祖传玉佩没有出现在镜头中。"
    )
    script_contents = ScriptContentRepository(repo, repo.layout)
    novel_ref = script_contents.write_content(
        project_dir,
        "novel_full",
        episode_key,
        node_name="script_novel",
        content=story_text,
    )
    state = ProjectState(
        project_id="prop_pipeline_smoke",
        title="道具链路 smoke",
        raw_script=story_text,
        script=ScriptBundle(
            raw_script=story_text,
            episode_outlines={episode_key: False},
            novel_full={episode_key: novel_ref},
            novel_extract={episode_key: False},
        ),
        metadata={
            "episode_count": 1,
            "visual_tone": "现代悬疑短剧质感，冷白办公灯、雨夜反光、证据物特写清楚。",
        },
    )
    repo.save_state(project_dir, state)

    workflow = PregenWorkflow(
        repo=repo,
        router=ProviderRouter(settings, provider_override="fake"),
    )
    for node_name in ("prop_extract", "prop_finalize", "prop_prompt", "prop_image_generation"):
        await workflow.run(project_dir, only=node_name, force=True)

    extract_path = repo.layout.node_output_path(project_dir, "prop_extract")
    dedupe_path = repo.layout.node_output_path(project_dir, "prop_finalize")
    prompt_path = repo.layout.node_output_path(project_dir, "prop_prompt")
    image_path = repo.layout.node_output_path(project_dir, "prop_image_generation")
    for path in (extract_path, dedupe_path, prompt_path, image_path):
        if not path.exists():
            raise AssertionError(f"missing expected prop node output: {path}")

    final_state = repo.load_state(project_dir)
    global_props = [prop for prop in final_state.props.values() if not prop.owner_role_id]
    if len(global_props) < 3:
        raise AssertionError(f"expected at least 3 generated props, got {len(global_props)}")
    missing_images = [prop.id for prop in global_props if not prop.asset_path or not (project_dir / prop.asset_path).exists()]
    if missing_images:
        raise AssertionError(f"prop images were not written: {missing_images}")

    variant_props = [prop for prop in global_props if "_" in prop.name]
    if not variant_props:
        raise AssertionError("expected at least one state variant prop")


if __name__ == "__main__":
    asyncio.run(main())
