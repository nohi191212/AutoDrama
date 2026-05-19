from __future__ import annotations

import base64
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.schemas import Prop  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def main() -> int:
    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "prop_variant_reference"
    image_dir = tmp_dir / "assets" / "images" / "props"
    image_dir.mkdir(parents=True, exist_ok=True)
    normal_path = image_dir / "prop_灵渊剑_normal.png"
    normal_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
        )
    )

    blood = Prop(
        id="prop_灵渊剑_blood",
        name="灵渊剑_blood",
        desc="染血状态的灵渊剑",
        prompt="生成染血状态的灵渊剑。",
        status="blood",
    )
    normal = Prop(
        id="prop_灵渊剑_normal",
        name="灵渊剑_normal",
        desc="常态灵渊剑",
        prompt="生成常态灵渊剑。",
        status="normal",
        asset_id="prop_灵渊剑_normal",
        asset_path="assets/images/props/prop_灵渊剑_normal.png",
    )

    ordered = PregenWorkflow._ordered_props_for_generation([blood, normal])
    if [prop.id for prop in ordered] != ["prop_灵渊剑_normal", "prop_灵渊剑_blood"]:
        raise AssertionError(f"Unexpected prop generation order: {[prop.id for prop in ordered]}")

    normal_props_by_base = PregenWorkflow._normal_props_by_variant_base(ordered)
    refs = PregenWorkflow._prop_reference_refs(tmp_dir, blood, normal_props_by_base)
    if not refs or refs[0].path != str(normal_path):
        raise AssertionError(f"Unexpected prop reference refs: {refs}")
    if refs[0].metadata.get("reference_for") != blood.id:
        raise AssertionError(f"Unexpected reference metadata: {refs[0].metadata}")

    derived_id = PregenWorkflow._prop_asset_id("灵渊剑", "blood")
    if derived_id != "prop_灵渊剑_blood":
        raise AssertionError(f"Unexpected derived prop id: {derived_id}")

    print("prop_variant_reference_smoke=ok")
    print(f"normal_reference={refs[0].path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
