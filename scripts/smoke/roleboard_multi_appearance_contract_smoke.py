from __future__ import annotations

from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.core.ids import normalize_id
from autodrama.core.schemas import Role, RoleAppearance, RoleExtractItem, RoleboardPromptItem
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.roleboard_prompt_repo import RoleboardPromptRepository


def main() -> None:
    item = RoleExtractItem.model_validate(
        {
            "name": "林舟",
            "role_tier": "primary",
            "aliases": ["男主"],
            "episode_keys": ["episode_001"],
            "source_chapters": ["第1章"],
            "brief": "被陷害后反击的职场青年。",
            "appearance_notes": ["青年男性", "短发", "偏瘦"],
            "appearance_assets": [
                {
                    "name": "base",
                    "asset_role": "base",
                    "episode_keys": ["episode_001"],
                    "appearance_desc": "青年男性，短发，偏瘦，深色通勤装。",
                    "clothing": "深色通勤装",
                    "visual_features": "短发，偏瘦，冷静疲惫",
                },
                {
                    "name": "雨夜办公室",
                    "asset_role": "variant",
                    "reference_asset_name": "base",
                    "episode_keys": ["episode_001"],
                    "appearance_desc": "同一林舟，深色衬衫外套微湿。",
                    "clothing": "被雨水打湿的深色衬衫和外套",
                    "visual_features": "保持同一脸、短发和偏瘦身形",
                },
            ],
            "has_dialogue": True,
            "visual_reuse_required": True,
        }
    )
    if len(item.appearance_assets) != 2:
        raise AssertionError("appearance_assets schema did not retain two items")

    settings = load_settings(ROOT / "config.yaml")
    repo = ProjectRepository(settings)
    prompts = RoleboardPromptRepository(repo, repo.layout)
    project_dir = ROOT / ".tmp" / "roleboard_multi_appearance_contract"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    role_id = normalize_id("role", item.name)
    base_prompt = RoleboardPromptItem(
        role_id=role_id,
        role_name=item.name,
        appearance_id=normalize_id(f"{role_id}_appearance", "base"),
        appearance_name="base",
        asset_role="base",
        episode_keys=["episode_001"],
        source_chapters=["第1章"],
        role_brief=item.brief,
        appearance_desc="青年男性，短发，偏瘦，深色通勤装。",
        clothing="深色通勤装",
        visual_features="短发，偏瘦，冷静疲惫",
        roleboard_prompt="base prompt",
    )
    variant_prompt = RoleboardPromptItem(
        role_id=role_id,
        role_name=item.name,
        appearance_id=normalize_id(f"{role_id}_appearance", "雨夜办公室"),
        appearance_name="雨夜办公室",
        asset_role="variant",
        reference_asset_name="base",
        episode_keys=["episode_001"],
        source_chapters=["第1章"],
        role_brief=item.brief,
        appearance_desc="同一林舟，深色衬衫外套微湿。",
        clothing="被雨水打湿的深色衬衫和外套",
        visual_features="保持同一脸、短发和偏瘦身形",
        roleboard_prompt="variant prompt",
    )
    role = Role(id=role_id, name=item.name, intro=item.brief or item.name)
    role.appearances["base"] = RoleAppearance(
        id=base_prompt.appearance_id,
        role_id=role_id,
        name="base",
        roleboard_prompt="base prompt",
    )
    role.appearances["雨夜办公室"] = RoleAppearance(
        id=variant_prompt.appearance_id,
        role_id=role_id,
        name="雨夜办公室",
        asset_role="variant",
        reference_asset_name="base",
        roleboard_prompt="variant prompt",
    )

    prompts.save_prompt_items(project_dir, extract_item=item, prompt_items=[base_prompt, variant_prompt], role=role)
    loaded = prompts.load_items(prompts.item_path(project_dir, role_id))
    if [prompt.appearance_name for prompt in loaded] != ["base", "雨夜办公室"]:
        raise AssertionError("multi-appearance roleboard prompts did not round-trip")
    if loaded[1].reference_asset_name != "base":
        raise AssertionError("variant reference_asset_name was not retained")

    print("roleboard multi-appearance contract smoke passed")


if __name__ == "__main__":
    main()
