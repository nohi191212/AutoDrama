from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import NodeModelSettings, Settings, load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    StoryboardPromptClip,
    StoryboardPromptEpisode,
    StoryboardPromptOutput,
    StoryboardSheetGenerationItem,
)
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.providers.base import ImageGenerationResult  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import (  # noqa: E402
    StoryboardGenerationNode,
    StoryboardPromptNode,
)
from autodrama.workflows.selection import parse_clip_selectors  # noqa: E402


def make_node(settings: Settings) -> StoryboardGenerationNode:
    repo = ProjectRepository(settings)
    return StoryboardGenerationNode(
        workflow=SimpleNamespace(),
        repo=repo,
        layout=repo.layout,
        router=None,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
    )


def make_prompt_node(settings: Settings) -> StoryboardPromptNode:
    repo = ProjectRepository(settings)
    return StoryboardPromptNode(
        workflow=SimpleNamespace(),
        repo=repo,
        layout=repo.layout,
        router=None,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
    )


def make_clip() -> StoryboardPromptClip:
    shot_numbers = [1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 4]
    panel_plan = {
        f"P{index:02d}": f"P{index:02d}（Camera Shot {shot_number}）：测试画面。"
        for index, shot_number in enumerate(shot_numbers, start=1)
    }
    return StoryboardPromptClip(
        clip_id="episode_001_clip_001",
        duration_seconds=12,
        camera_shots=[
            {"camera_shot_id": f"Camera Shot {index}"}
            for index in range(1, 5)
        ],
        panel_plan=panel_plan,
        video_prompt="VIDEO_PROMPT_ONLY_SENTINEL",
    )


async def verify_selected_generation(node: StoryboardGenerationNode, clip: StoryboardPromptClip) -> None:
    clips = [clip.model_copy(update={"clip_id": f"episode_001_clip_{index:03d}"}) for index in range(1, 6)]
    selected: list[str] = []

    async def generate_storyboard_sheet(**kwargs):
        selected_clip = kwargs["shot"]
        selected.append(selected_clip.clip_id)
        return StoryboardSheetGenerationItem(
            episode_key="episode_001",
            clip_id=selected_clip.clip_id,
            asset_id=f"{selected_clip.clip_id}_storyboard",
            prompt="test",
            duration_seconds=selected_clip.duration_seconds,
            panel_count=12,
            grid="4x3",
            sheet_aspect_ratio="16:9",
            panel_aspect_ratio="4:3",
            size="1600x900",
            asset_path=f"assets/images/storyboards/{selected_clip.clip_id}_storyboard.png",
            provider="fake",
            model="fake-image",
        )

    node.workflow = SimpleNamespace(
        _hydrate_roles_from_design_files=lambda *_args, **_kwargs: None,
        _force_pregen=True,
        _active_clip_selectors={"1", "2", "3"},
    )
    node.router = SimpleNamespace(
        image=lambda *_args, **_kwargs: SimpleNamespace(
            name="fake",
            model="fake-image",
            max_reference_images=0,
            supports_reference_images=False,
            model_binding=SimpleNamespace(params={"clip_storyboard_image_generation_concurrency": 2}),
        )
    )
    node.logger = SimpleNamespace(info=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
    node.load_clip_storyboard_prompt_output = lambda _project_dir: StoryboardPromptOutput(
        storyboards=[StoryboardPromptEpisode(episode_key="episode_001", clips=clips)]
    )
    node.target_episode_keys = lambda _state: ["episode_001"]
    node.expected_episode_keys = lambda _state: ["episode_001"]
    node.load_existing_output = lambda _project_dir: {}
    node.generate_storyboard_sheet = generate_storyboard_sheet
    selection_project = ROOT / ".tmp" / "clip_storyboard_sheet_selection_smoke"
    selection_project.mkdir(parents=True, exist_ok=True)
    await node.run(selection_project, SimpleNamespace(project_id="selection-smoke"))
    expected = [f"episode_001_clip_{index:03d}" for index in range(1, 4)]
    if selected != expected:
        raise AssertionError(f"image generation selected wrong clips: {selected}; expected {expected}")


async def verify_verbatim_image_prompt(node: StoryboardGenerationNode, clip: StoryboardPromptClip) -> None:
    received_prompts: list[str] = []

    class Provider:
        name = "fake"
        model = "fake-image"

        async def generate_image(self, prompt, refs=None, *, metadata=None, size=None):
            received_prompts.append(prompt)
            return ImageGenerationResult(provider=self.name, model=self.model)

    class MediaStore:
        async def write_first_generated_image(self, project_dir, output_path, result):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 800), "white").save(output_path)
            return output_path.relative_to(project_dir).as_posix()

    node.media_store = MediaStore()
    project_dir = ROOT / ".tmp" / "clip_storyboard_verbatim_prompt_smoke"
    item = await node.generate_storyboard_sheet(
        provider=Provider(),
        project_dir=project_dir,
        state=SimpleNamespace(project_id="verbatim-smoke"),
        episode=StoryboardPromptEpisode(episode_key="episode_001", clips=[clip]),
        shot=clip,
        max_refs=0,
        supports_refs=False,
    )
    expected = clip.storyboard_image_prompt
    if received_prompts != [expected]:
        raise AssertionError("image generation did not submit storyboard_image_prompt verbatim")
    if item.prompt != expected:
        raise AssertionError("image generation output did not preserve the submitted prompt")


def main() -> None:
    configured_settings = load_settings(ROOT / "config.yaml")
    configured_node = make_node(configured_settings)
    prompt_node = make_prompt_node(configured_settings)
    clip = make_clip()
    try:
        configured_node.required_storyboard_image_prompt(clip)
    except ValueError as exc:
        if "rerun pregen --only clip_storyboard_prompt" not in str(exc):
            raise AssertionError(f"legacy prompt error lacks rerun guidance: {exc}") from exc
    else:
        raise AssertionError("image generation accepted a legacy clip without storyboard_image_prompt")
    prompt = prompt_node.compose_storyboard_image_prompt("episode_001", clip)
    clip.storyboard_image_prompt = prompt
    if configured_node.required_storyboard_image_prompt(clip) != prompt:
        raise AssertionError("image generation node did not consume the prompt-node image prompt verbatim")
    required_prompt_text = [
        "16:9",
        "3840x2160（4K）",
        "4:3",
        "左侧为黑色两位面板号",
        "右侧为红色两位 Camera Shot 号",
        "03-04（两格之间的竖直分隔线中央）",
        "蓝色箭头表示镜头运动",
        "紫色波纹箭头表示声音来源或传播",
    ]
    missing = [value for value in required_prompt_text if value not in prompt]
    if missing:
        raise AssertionError(f"storyboard image prompt is missing configured layout rules: {missing}")
    required_panel_text = ["【逐格画面】", "01 | 01：测试画面。", "12 | 04：测试画面。"]
    missing_panels = [value for value in required_panel_text if value not in prompt]
    if missing_panels:
        raise AssertionError(f"storyboard image prompt is missing panel-only content: {missing_panels}")
    forbidden_prompt_text = [
        "1:1 方形",
        "必须留出一条清晰的文字说明带",
        "整张图底部保留一条全局颜色图例",
        "当前 clip 视频提示词",
        "clip_storyboard_image_generation",
        "episode_001",
        clip.video_prompt,
        "最终图会由系统并排覆盖",
        "生图阶段不要自行绘制数字",
        "画面、分隔线和少量运动箭头全部使用黑色或灰色",
    ]
    present = [value for value in forbidden_prompt_text if value in prompt]
    if present:
        raise AssertionError(f"storyboard image prompt retains obsolete layout rules: {present}")

    smoke_settings = Settings(
        nodes={
            "clip_storyboard_image_generation": NodeModelSettings(
                model="fake:image",
                params={"size": "1600x900", "storyboard_panel_aspect_ratio": "4:3"},
            )
        }
    )
    node = make_node(smoke_settings)
    project_dir = ROOT / ".tmp" / "clip_storyboard_sheet_contract_smoke"
    relative_path = Path("assets/images/storyboards/episode_001_clip_001_storyboard.png")
    image_path = project_dir / relative_path
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 800), "white").save(image_path)

    metadata = node._postprocess_storyboard_sheet(
        project_dir=project_dir,
        asset_path=relative_path.as_posix(),
    )
    postprocess = metadata["storyboard_postprocess"]
    if postprocess["final_size"] != [1600, 900]:
        raise AssertionError(f"unexpected storyboard size: {postprocess['final_size']}")
    if postprocess["annotations"] != "generated_by_image_model":
        raise AssertionError(f"unexpected annotation owner: {postprocess['annotations']}")
    with Image.open(image_path) as image:
        if image.size != (1600, 900):
            raise AssertionError(f"postprocessed image size mismatch: {image.size}")
        red_pixels = sum(
            1
            for red, green, blue, *_alpha in image.convert("RGBA").getdata()
            if red > 160 and green < 80 and blue < 80
        )
        if red_pixels != 0:
            raise AssertionError("postprocessing must not draw shot numbers or cut marks")

    selectors = parse_clip_selectors("1-3")
    if selectors != ["1", "2", "3"]:
        raise AssertionError(f"unexpected parsed clip selectors: {selectors}")
    if not node.clip_matches_active_selectors(
        episode_key="episode_001",
        clip=clip,
        clip_index=1,
        selectors=set(selectors),
    ):
        raise AssertionError("clip selector 1-3 did not match the first clip")
    if node.generation_concurrency(SimpleNamespace(model_binding=SimpleNamespace(params={
        "clip_storyboard_image_generation_concurrency": 4
    }))) != 4:
        raise AssertionError("storyboard image concurrency did not resolve to 4")
    asyncio.run(verify_verbatim_image_prompt(node, clip))
    asyncio.run(verify_selected_generation(node, clip))

    (ROOT / ".tmp" / "clip_storyboard_sheet_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("clip_storyboard_sheet_contract_smoke: ok")


if __name__ == "__main__":
    main()
