"""Verify shot video uses a keyframe plus involved character turnarounds."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.core.schemas import ShotManifestItem, ShotVideoInput  # noqa: E402
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402


class _WorkflowStub:
    @staticmethod
    def _path_exists(project_dir: Path, path_value: str | None) -> bool:
        if not path_value:
            return False
        path = Path(path_value)
        if not path.is_absolute():
            path = project_dir / path
        return path.is_file()


def _input(
    *,
    slot: str,
    asset_type: str,
    asset_id: str,
    asset_path: str,
    order: int,
    role_id: str | None = None,
    kling_content_id: str,
) -> ShotVideoInput:
    return ShotVideoInput(
        slot=slot,
        asset_type=asset_type,
        asset_id=asset_id,
        asset_path=asset_path,
        source_node=(
            "shot_keyframe_image_generation"
            if asset_type == "shot_keyframe"
            else "roleboard_image_generation"
        ),
        role_id=role_id,
        required=True,
        order=order,
        metadata={"kling_content_id": kling_content_id},
    )


def main() -> None:
    project_dir = ROOT / ".tmp" / "shot_video_reference_contract"
    project_dir.mkdir(parents=True, exist_ok=True)
    keyframe = project_dir / "keyframe.png"
    roleboard = project_dir / "roleboard.png"
    keyframe.write_bytes(b"keyframe")
    roleboard.write_bytes(b"roleboard")

    shot = ShotManifestItem(
        shot_id="episode_001_clip_001_shot_001",
        index=1,
        title="contract",
        duration_seconds=5,
        role_ids=["role_a"],
        role_appearance_ids=["role_a_base"],
        video_prompt="角色开始行动。",
        video_inputs=[
            _input(
                slot="image_1",
                asset_type="shot_keyframe",
                asset_id="shot_keyframe",
                asset_path=keyframe.name,
                order=0,
                kling_content_id="shot_keyframe",
            ),
            _input(
                slot="image_2",
                asset_type="roleboard",
                asset_id="role_a_base",
                asset_path=roleboard.name,
                order=1,
                role_id="role_a",
                kling_content_id="role_1",
            ),
        ],
    )
    provider = KlingOmniVideoProvider(
        ProviderSettings(
            models={"video": "kling-v3-omni"},
            options={"api_schema": "official_v3", "max_reference_images": 7},
        ),
        RuntimeSettings(),
    )
    inputs = GenerationWorkflow._shot_video_inputs(
        _WorkflowStub(),
        project_dir,
        shot,
        provider=provider,
    )
    if inputs["contract"] != "shot_keyframe_with_character_turnarounds_v2":
        raise AssertionError("unexpected shot video reference contract")
    refs = GenerationWorkflow._asset_refs_from_shot_video_inputs(project_dir, inputs)
    if [ref.metadata.get("asset_type") for ref in refs] != ["shot_keyframe", "roleboard"]:
        raise AssertionError("shot refs must be keyframe followed by involved character turnarounds")
    payload = provider.build_payload(shot.video_prompt, refs, duration=5)
    content_types = [item["type"] for item in payload["contents"] if item["type"] != "prompt"]
    if content_types != ["refer_image", "refer_image"]:
        raise AssertionError(f"Kling refs must remain ordinary refer_image inputs: {content_types}")
    if any(item["type"] in {"first_frame", "last_frame", "element"} for item in payload["contents"]):
        raise AssertionError("Kling payload unexpectedly forced frame or subject-element inputs")
    if any(
        value is not None
        for value in (
            shot.start_frame_asset_id,
            shot.start_frame_asset_path,
            shot.end_frame_asset_id,
            shot.end_frame_asset_path,
        )
    ):
        raise AssertionError("shot reference contract unexpectedly populated first/end frame fields")

    missing_roleboard = shot.model_copy(
        update={"video_inputs": shot.video_inputs[:1]},
    )
    try:
        GenerationWorkflow._shot_video_inputs(
            _WorkflowStub(),
            project_dir,
            missing_roleboard,
            provider=provider,
        )
    except ValueError as exc:
        if "missing character turnaround references" not in str(exc):
            raise
    else:
        raise AssertionError("missing involved character turnaround was not rejected")

    print("shot_video_reference_contract_smoke: ok")


if __name__ == "__main__":
    main()
