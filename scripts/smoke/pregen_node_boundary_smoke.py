from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.nodes import PREGEN_NODE_NAMES, build_pregen_nodes  # noqa: E402
from autodrama.workflows.nodes.bgm_nodes import (  # noqa: E402
    BGM_NODE_NAMES,
    BGMDesignNode,
    BGMGenerationNode,
)
from autodrama.workflows.nodes.director_nodes import (  # noqa: E402
    DIRECTOR_NODE_NAMES,
    DirectorPrepNode,
)
from autodrama.workflows.nodes.script_nodes import (  # noqa: E402
    SCRIPT_NODE_NAMES,
    ScriptNovelExtractNode,
    ScriptNovelNode,
    ScriptOutlineNode,
)
from autodrama.workflows.nodes.role_nodes import (  # noqa: E402
    AmbientEntityExtractNode,
    ROLE_NODE_NAMES,
    RoleDesignNode,
    RoleDuplicateAuditNode,
    RoleEpisodeKeyAuditNode,
    RoleExtractNode,
    RoleFunctionalExtractNode,
    RolePrimaryExtractNode,
)
from autodrama.workflows.nodes.static_asset_nodes import (  # noqa: E402
    STATIC_ASSET_NODE_NAMES,
    LayoutDedupeReviewNode,
    LayoutDesignNode,
    LayoutExtractNode,
    LayoutImageGenerationNode,
    PropDesignNode,
    PropExtractNode,
    PropGenerationNode,
    RoleFullBodyGenerationNode,
    RoleIntroVideoGenerationNode,
    RoleIntroVideoPromptNode,
    RoleMultiviewGenerationNode,
)
from autodrama.workflows.nodes.voice_nodes import (  # noqa: E402
    VOICE_NODE_NAMES,
    RoleVoiceGenerationNode,
    VoiceSelectNode,
)
from autodrama.workflows.pregen import PREGEN_NODES, PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "pregen_node_boundary"
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)

    nodes = build_pregen_nodes(workflow)
    node_names = [node.name for node in nodes]
    require(node_names == PREGEN_NODE_NAMES, "Pregen node registry order drifted")
    require(PREGEN_NODE_NAMES == PREGEN_NODES, "Pregen node constants drifted")

    expected_script_owners = {
        "script_outline": ScriptOutlineNode,
        "script_novel": ScriptNovelNode,
        "script_novel_extract": ScriptNovelExtractNode,
    }
    by_name = {node.name: node for node in nodes}
    for node_name in SCRIPT_NODE_NAMES:
        owner = getattr(by_name[node_name].run, "__self__", None)
        require(owner is not None, f"{node_name} is not backed by a node instance")
        require(
            isinstance(owner, expected_script_owners[node_name]),
            f"{node_name} should be owned by {expected_script_owners[node_name].__name__}",
        )
        require(owner.repo is workflow.repo, f"{node_name} repo dependency was not wired from workflow")
        require(owner.script_service is workflow.script_service, f"{node_name} script service dependency drifted")

    expected_director_owners = {
        "director_prep": DirectorPrepNode,
    }
    for node_name in DIRECTOR_NODE_NAMES:
        owner = getattr(by_name[node_name].run, "__self__", None)
        require(owner is not None, f"{node_name} is not backed by a node instance")
        require(
            isinstance(owner, expected_director_owners[node_name]),
            f"{node_name} should be owned by {expected_director_owners[node_name].__name__}",
        )
        require(owner.repo is workflow.repo, f"{node_name} repo dependency was not wired from workflow")
        require(owner.director_service is workflow.director_service, f"{node_name} director service dependency drifted")

    expected_role_owners = {
        "role_extract_primary": RolePrimaryExtractNode,
        "role_extract_functional": RoleFunctionalExtractNode,
        "role_extract": RoleExtractNode,
        "role_episode_key_audit": RoleEpisodeKeyAuditNode,
        "role_duplicate_audit": RoleDuplicateAuditNode,
        "ambient_entity_extract": AmbientEntityExtractNode,
        "role_design": RoleDesignNode,
    }
    for node_name in ROLE_NODE_NAMES:
        owner = getattr(by_name[node_name].run, "__self__", None)
        require(owner is not None, f"{node_name} is not backed by a node instance")
        require(
            isinstance(owner, expected_role_owners[node_name]),
            f"{node_name} should be owned by {expected_role_owners[node_name].__name__}",
        )
        require(owner.repo is workflow.repo, f"{node_name} repo dependency was not wired from workflow")
        require(owner.role_service is workflow.role_service, f"{node_name} role service dependency drifted")

    expected_bgm_owners = {
        "bgm_design": BGMDesignNode,
        "bgm_generation": BGMGenerationNode,
    }
    for node_name in BGM_NODE_NAMES:
        owner = getattr(by_name[node_name].run, "__self__", None)
        require(owner is not None, f"{node_name} is not backed by a node instance")
        require(
            isinstance(owner, expected_bgm_owners[node_name]),
            f"{node_name} should be owned by {expected_bgm_owners[node_name].__name__}",
        )
        require(owner.repo is workflow.repo, f"{node_name} repo dependency was not wired from workflow")
        require(owner.asset_service is workflow.asset_service, f"{node_name} asset service dependency drifted")

    expected_static_asset_owners = {
        "role_full_body_generation": RoleFullBodyGenerationNode,
        "role_multiview_generation": RoleMultiviewGenerationNode,
        "role_intro_video_prompt": RoleIntroVideoPromptNode,
        "role_intro_video_generation": RoleIntroVideoGenerationNode,
        "prop_extract": PropExtractNode,
        "prop_design": PropDesignNode,
        "prop_generation": PropGenerationNode,
        "layout_extract": LayoutExtractNode,
        "layout_design": LayoutDesignNode,
        "layout_dedupe_review": LayoutDedupeReviewNode,
        "layout_image_generation": LayoutImageGenerationNode,
    }
    for node_name in STATIC_ASSET_NODE_NAMES:
        owner = getattr(by_name[node_name].run, "__self__", None)
        require(owner is not None, f"{node_name} is not backed by a node instance")
        require(
            isinstance(owner, expected_static_asset_owners[node_name]),
            f"{node_name} should be owned by {expected_static_asset_owners[node_name].__name__}",
        )
        require(owner.repo is workflow.repo, f"{node_name} repo dependency was not wired from workflow")
        require(owner.asset_service is workflow.asset_service, f"{node_name} asset service dependency drifted")

    expected_voice_owners = {
        "voice_select": VoiceSelectNode,
        "role_voice_generation": RoleVoiceGenerationNode,
    }
    for node_name in VOICE_NODE_NAMES:
        owner = getattr(by_name[node_name].run, "__self__", None)
        require(owner is not None, f"{node_name} is not backed by a node instance")
        require(
            isinstance(owner, expected_voice_owners[node_name]),
            f"{node_name} should be owned by {expected_voice_owners[node_name].__name__}",
        )
        require(owner.repo is workflow.repo, f"{node_name} repo dependency was not wired from workflow")
        require(owner.role_service is workflow.role_service, f"{node_name} role service dependency drifted")

    print("pregen_node_boundary_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
