from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    RoleAppearanceDesignItem,
    RoleBoundPropDesignItem,
    RoleDesignItem,
    RoleDesignOutput,
    RoleExtractItem,
    RoleExtractOutput,
    RoleVoiceItem,
)
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.repositories.role_design_repo import RoleDesignRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = "role_refs_state_smoke"
    project_dir = settings.output.root_dir / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir)

    state = repo.create_project(
        title="Role Refs State Smoke",
        raw_script="韩默带着短匕进入秘境。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=5,
    )
    state = repo.load_state(project_dir)
    role_designs = RoleDesignRepository(repo, repo.layout)

    extract_item = RoleExtractItem(
        name="韩默",
        aliases=["韩小子"],
        importance="lead",
        episode_keys=["episode_001"],
        source_chapters=["episode_001"],
        brief="清瘦谨慎的散修炼丹师。",
        appearance_notes=["灰布短褐", "神情克制"],
    )
    extract_output = RoleExtractOutput(roles=[extract_item])
    repo.save_node_output(project_dir, "role_extract", extract_output)
    state.metadata["role_refs"] = {
        extract_item.name: role_designs.save_extract_item(project_dir, extract_item)
    }
    repo.save_state(project_dir, state)

    state_payload = load_json(project_dir / "state.json")
    expected_ref = "assets/json/roles/role_韩默.json"
    require(state_payload["roles"] == {"韩默": expected_ref}, f"Unexpected state.roles: {state_payload['roles']}")
    require("role_refs" not in state_payload["metadata"], "role_refs should not be duplicated in disk metadata")

    role_path = project_dir / expected_ref
    role_payload = load_json(role_path)
    require(role_payload["role_name"] == "韩默", f"Unexpected role_name: {role_payload}")
    require(role_payload["extract"]["name"] == "韩默", f"Missing extract payload: {role_payload}")
    require(role_payload["design"] is None, f"Design should be empty after role_extract: {role_payload}")
    require(role_payload["state_role"] is None, f"state_role should be empty after role_extract: {role_payload}")
    require(role_payload["bound_props"] == [], f"bound_props should be empty after role_extract: {role_payload}")
    require(role_designs.load_existing_output(project_dir) is None, "Extract-only role JSON should not count as design")

    workflow = PregenWorkflow(repo=repo, router=object())
    design_item = RoleDesignItem(
        name="韩默",
        intro="韩默，十九岁散修炼丹师，清瘦谨慎，擅长在危局中寻找生路。",
        personality="谨慎、隐忍、反应快。",
        aliases=["韩小子"],
        importance="lead",
        episode_keys=["episode_001"],
        source_chapters=["episode_001"],
        appearances=[
            RoleAppearanceDesignItem(
                role_name="韩默",
                name="base",
                desc="清瘦青年，灰布短褐，腰间带短匕。",
                full_body_prompt="正面全身照，清瘦青年，灰布短褐，腰间短匕，神情克制。",
                prompt="角色设定图，清瘦青年，灰布短褐，腰间短匕，干净背景。",
                role_bound_props=[
                    RoleBoundPropDesignItem(
                        name="短匕",
                        desc="旧铁短匕，柄缠黑布。",
                        prompt="旧铁短匕，黑布缠柄，清晰道具设定图。",
                        status="normal",
                    )
                ],
                intro_video_prompt="韩默握住短匕，警惕观察四周。",
            )
        ],
        voices=[
            RoleVoiceItem(
                role_name="韩默",
                emotion="normal",
                desc="年轻男性，语气低稳克制。",
                sample_text="我叫韩默，只想从这处秘境里活着出去。",
            )
        ],
    )
    design_path = role_designs.item_relative_path_for_name(project_dir, design_item.name)
    workflow._apply_role_design_item(project_dir, state, design_item, design_path=design_path)
    role = state.roles["role_韩默"]
    bound_props = [
        prop
        for prop in state.props.values()
        if prop.owner_role_id == role.id
    ]
    role_designs.save_design_item(
        project_dir,
        extract_item=extract_item,
        design_item=design_item,
        role=role,
        bound_props=bound_props,
    )
    repo.save_node_output(project_dir, "role_design", RoleDesignOutput(roles=[design_item]))
    repo.save_state(project_dir, state)

    state_payload = load_json(project_dir / "state.json")
    require(state_payload["roles"] == {"韩默": expected_ref}, f"Unexpected designed state.roles: {state_payload['roles']}")
    require(isinstance(state_payload["roles"]["韩默"], str), "state.roles value should be a JSON path string")
    require("role_韩默" not in state_payload["roles"], "state.roles should be keyed by original role name")

    role_payload = load_json(role_path)
    require(role_payload["design"]["name"] == "韩默", f"Missing design payload: {role_payload}")
    require(role_payload["state_role"]["id"] == "role_韩默", f"Missing state_role payload: {role_payload}")
    require(role_payload["state_role"]["design_path"] == expected_ref, f"Wrong role design_path: {role_payload}")
    require(role_payload["bound_props"][0]["name"] == "短匕", f"Missing bound prop: {role_payload}")
    require(role_payload["source"]["role_extract_path"] == "assets/json/nodes/role_extract.json", "Wrong extract source")
    require(role_payload["source"]["role_design_path"] == "assets/json/nodes/role_design.json", "Wrong design source")

    loaded_state = repo.load_state(project_dir)
    require(loaded_state.metadata["role_refs"] == {"韩默": expected_ref}, "Runtime role refs were not restored")
    require(loaded_state.roles["role_韩默"].name == "韩默", "Runtime Role was not hydrated from role JSON")
    require(loaded_state.roles["role_韩默"].appearances["base"].role_bound_prop_ids, "Hydrated role lost bound props")

    loaded_state.roles["role_韩默"].appearances["base"].design_image_asset_path = "assets/images/roles/role_韩默.png"
    repo.save_state(project_dir, loaded_state)
    role_payload = load_json(role_path)
    require(
        role_payload["state_role"]["appearances"]["base"]["design_image_asset_path"]
        == "assets/images/roles/role_韩默.png",
        "save_state did not refresh state_role inside role JSON",
    )

    print("role_refs_state_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"role_json={expected_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
