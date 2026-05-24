from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.voice_catalog import VoiceCatalogManifest, VoiceCatalogVoiceItem  # noqa: E402
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    manifest = VoiceCatalogManifest(
        catalog_version="lookup-smoke",
        provider="fake",
        model="fake-tts",
        sample_emotions=["normal"],
        voices=[
            VoiceCatalogVoiceItem(
                voice_label="小何 2.0",
                voice_type="fake_female_a",
                voice_resource_id="fake-tts",
                voice_model_family="fake",
                voice_catalog_key="fake:fake-tts:fake_female_a",
            ),
            VoiceCatalogVoiceItem(
                voice_label="小何 2.0",
                voice_type="fake_female_b",
                voice_resource_id="fake-tts",
                voice_model_family="fake",
                voice_catalog_key="fake:fake-tts:fake_female_b",
            ),
            VoiceCatalogVoiceItem(
                voice_label="云舟 2.0",
                voice_type="fake_male",
                voice_resource_id="fake-tts",
                voice_model_family="fake",
                voice_catalog_key="fake:fake-tts:fake_male",
            ),
        ],
    )
    matches = VoiceCatalogRepository.voices_by_label(manifest, "小何 2.0")
    duplicates = VoiceCatalogRepository.duplicate_labels(manifest)

    require([item.voice_type for item in matches] == ["fake_female_a", "fake_female_b"], "label lookup mismatch")
    require(duplicates == {"小何 2.0": ["fake_female_a", "fake_female_b"]}, "duplicate label detection mismatch")
    require(VoiceCatalogRepository.voice_by_type(manifest, "fake_male").voice_label == "云舟 2.0", "voice_type lookup mismatch")

    print("voice_catalog_label_lookup_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
