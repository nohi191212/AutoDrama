from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.schemas import Role, RoleAppearance
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider
from autodrama.workflows.nodes.role_subject_nodes import RoleSubjectElementGenerationNode


class Layout:
    @staticmethod
    def existing_project_file(project_dir: Path, value: str | Path | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        candidate = path if path.is_absolute() else project_dir / path
        if not candidate.is_file():
            return None
        return str(candidate.relative_to(project_dir))


def main() -> None:
    project_dir = ROOT / ".tmp" / "role_subject_frontal_contract"
    roles_dir = project_dir / "assets" / "images" / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    roleboard = roles_dir / "roleboard.png"
    frontal = roles_dir / "frontal.png"

    if not roleboard.exists() or roleboard.stat().st_size <= 9_500_000:
        Image.frombytes("RGB", (2200, 1600), os.urandom(2200 * 1600 * 3)).save(roleboard, format="PNG")
    Image.new("RGB", (1088, 1920), "white").save(frontal, format="PNG")

    workflow = SimpleNamespace(
        repo=None,
        layout=Layout(),
        router=None,
        media_store=None,
    )
    node = RoleSubjectElementGenerationNode(workflow=workflow)
    role = Role(id="role_demo", name="示例角色", intro="示例")
    appearance = RoleAppearance(
        id="appearance_demo",
        role_id=role.id,
        asset_path=str(roleboard.relative_to(project_dir)),
        subject_frontal_image_asset_id="role_subject_frontal_appearance_demo",
        subject_frontal_image_asset_path=str(frontal.relative_to(project_dir)),
    )

    refs = node._image_refs(project_dir, role, appearance)
    assert len(refs) == 2
    assert refs[0].metadata["reference_role"] == "frontal_image"
    assert refs[1].metadata["reference_role"] == "other_reference_image"
    assert refs[1].path is not None
    assert Path(refs[1].path).stat().st_size <= 9_500_000

    provider = KlingOmniVideoProvider(
        ProviderSettings(models={"video": "kling-v3-omni"}, options={"api_schema": "official_v3"}),
        RuntimeSettings(),
    )
    payload = provider.build_subject_element_payload(
        element_name=role.name,
        element_description=role.intro,
        reference_type="image_refer",
        image_refs=refs,
    )
    assert payload["element_image_list"]["frontal_image"]
    assert len(payload["element_image_list"]["refer_images"]) == 1
    assert payload["element_image_list"]["frontal_image"] != payload["element_image_list"]["refer_images"][0]["image_url"]
    print("role subject frontal contract smoke: ok")


if __name__ == "__main__":
    main()
