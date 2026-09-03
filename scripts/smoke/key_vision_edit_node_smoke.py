from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StaticAssetGenerationItem, StaticAssetGenerationOutput  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.nodes.director_nodes import build_director_node_runners  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


PROJECT_ROOT = ROOT / ".tmp" / "key-vision-edit-node-smoke"


def main() -> int:
    if PROJECT_ROOT.exists():
        shutil.rmtree(PROJECT_ROOT)
    settings = load_settings(str(ROOT / "saodi.yaml"))
    settings.output.root_dir = PROJECT_ROOT
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config(project_id="edit_smoke")
    state = repo.load_state(project_dir)
    state.metadata["key_vision_audit_feedback"] = [
        {
            "attempt": 1,
            "issues": ["人物与建筑比例不协调，前景脚部透视过强。"],
            "rationale": "只需修复可见比例和透视，不改变场景。",
        }
    ]
    repo.save_state(project_dir, state)

    image_path = repo.layout.image_asset_path(project_dir, "key_visions", "key_vision_original")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"fake-source-image")
    repo.save_node_output(
        project_dir,
        "key_vision_image_generation",
        StaticAssetGenerationOutput(
            generated_assets=[
                StaticAssetGenerationItem(
                    asset_id="key_vision_original",
                    asset_type="key_vision",
                    owner_id=state.project_id,
                    name="主视觉原图",
                    prompt="Original key vision prompt.",
                    asset_path=repo.layout.project_relative(project_dir, image_path),
                    provider="fake",
                    model="fake-image",
                )
            ]
        ),
    )

    workflow = PregenWorkflow(repo=repo, router=ProviderRouter(settings, provider_override="fake"))
    state = asyncio.run(build_director_node_runners(workflow)["key_vision_edit"].run(project_dir, state))

    edited_path = project_dir / str(state.metadata["key_vision_asset_path"])
    assert edited_path.is_file()
    assert edited_path.name == "key_vision_original_edit.png"
    assert repo.layout.node_output_path(project_dir, "key_vision_edit").is_file()
    current_output = StaticAssetGenerationOutput.model_validate_json(
        repo.layout.node_output_path(project_dir, "key_vision_image_generation").read_text(encoding="utf-8")
    )
    assert current_output.generated_assets[0].asset_path == state.metadata["key_vision_asset_path"]
    assert "人物与建筑比例不协调" in current_output.generated_assets[0].prompt
    print("key vision edit node smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
