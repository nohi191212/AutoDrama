from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.providers.local.mock.fake import FakeAudioJudgeProvider, FakeVoiceDesignProvider  # noqa: E402
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository  # noqa: E402
from autodrama.services.voice_catalog_service import VoiceCatalogService  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "voice_catalog_build_samples_profiles" / "outputs"
    catalog_root = settings.output.root_dir.parent / ".assets" / "voice_catalog"
    if catalog_root.exists():
        shutil.rmtree(catalog_root)

    repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(repo)
    speech_provider = FakeVoiceDesignProvider()
    judge = FakeAudioJudgeProvider()
    manifest = service.load_or_bootstrap_manifest(speech_provider)
    manifest = await service.build_samples(
        speech_provider,
        manifest,
        force_samples=True,
        voice_types={"fake_male_voice"},
    )
    manifest = await service.build_profiles(
        judge,
        manifest,
        force_profiles=True,
        voice_types={"fake_male_voice"},
    )
    male = repo.voice_by_type(manifest, "fake_male_voice")
    female = repo.voice_by_type(manifest, "fake_female_voice")

    require(male is not None, "fake_male_voice missing")
    require(female is not None, "fake_female_voice missing")
    require(set(male.samples) == set(manifest.sample_emotions), "male samples were not generated")
    require(male.omni_profile is not None, "male profile was not generated")
    require(male.profile_hash, "male profile hash missing")
    require("fake 评测" in male.omni_profile.summary, "male profile summary should use natural sketch text")
    require(female.samples == {}, "non-target voice should not have samples")
    for sample in male.samples.values():
        path = repo.root / sample.asset_path
        require(path.exists() and path.stat().st_size > 0, f"sample file missing: {path}")
    require(
        repo.sample_manifest_path(manifest.provider, manifest.model, "fake_male_voice").exists(),
        "per-voice sample manifest missing",
    )
    profile_path = repo.voice_profile_path(manifest.provider, manifest.model, "fake_male_voice")
    require(profile_path.exists(), "per-voice profile json missing")
    require("omni_profile" in profile_path.read_text(encoding="utf-8"), "per-voice profile json missing profile payload")

    print("voice_catalog_build_samples_profiles_smoke=ok")
    print(f"manifest_path={repo.manifest_path(manifest.provider, manifest.model)}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
