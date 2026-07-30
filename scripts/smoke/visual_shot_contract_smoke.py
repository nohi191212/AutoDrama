"""Deterministic regression checks for the visual/shot delivery contract."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import RoleAppearance, ShotManifestEpisodeOutput, ShotManifestItem  # noqa: E402
from autodrama.core.visual_contract import (  # noqa: E402
    assert_duration_gate,
    build_visual_style_spec,
    identity_brief,
    normalize_shot_durations,
    render_visual_style_brief,
)


def main() -> None:
    style = build_visual_style_spec(
        {
            "schema_version": 2,
            "medium": "stylized_3d_cg",
            "render_engine_language": ["高质感东方玄幻国漫，风格化 CG 人物。"],
            "materials": ["精细织物", "石材"],
            "palette": ["月白", "墨蓝"],
            "lighting": ["电影级体积光"],
            "negative_constraints": ["真人照片", "水印", "logo"],
        }
    )
    if style.medium != "stylized_3d_cg":
        raise AssertionError(f"unexpected medium: {style.medium}")
    brief = render_visual_style_brief(style)
    if brief != (
        "视觉媒介：stylized_3d_cg。\n"
        "高质感东方玄幻国漫，风格化 CG 人物。\n"
        "材质：精细织物、石材。\n"
        "色板：月白、墨蓝。\n"
        "灯光：电影级体积光。\n"
        "不得出现：真人照片、水印、logo。"
    ):
        raise AssertionError("visual brief lost required style dimensions")

    durations = normalize_shot_durations([7] * 20, 150)
    if sum(durations) != 150 or any(value < 3 or value > 15 for value in durations):
        raise AssertionError(f"invalid normalized durations: {durations}")
    assert_duration_gate(sum(durations), 150)

    appearance = RoleAppearance(
        id="role_yefan_appearance_old",
        role_id="role_yefan",
        identity_invariants=["清瘦脸型", "灰白长发", "胸口固定发光纹章"],
        wardrobe=["旧灰袍"],
        desc="未来将激活小炉并爆发赤光",
    )
    clean_identity = identity_brief(appearance)
    if clean_identity != "清瘦脸型；灰白长发；胸口固定发光纹章；旧灰袍":
        raise AssertionError(f"structured identity was not consumed exactly: {clean_identity}")

    shot = ShotManifestItem(
        shot_id="episode_001_clip_001_shot_001",
        index=1,
        title="clean plate",
        duration_seconds=5,
        video_prompt="画面保持无可读文字",
    )
    episode = ShotManifestEpisodeOutput(episode_key="episode_001", shots=[shot])
    if shot.ready_for_video or episode.ready_for_video:
        raise AssertionError("unaudited manifest unexpectedly became ready for video")

    print("visual_shot_contract_smoke: ok")


if __name__ == "__main__":
    main()
