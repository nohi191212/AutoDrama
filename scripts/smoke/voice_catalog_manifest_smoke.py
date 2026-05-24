from __future__ import annotations

import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.providers.local.mock.fake import FakeVoiceDesignProvider  # noqa: E402
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository  # noqa: E402
from autodrama.services.voice_catalog_service import VoiceCatalogService  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "voice_catalog_manifest" / "outputs"
    catalog_root = settings.output.root_dir.parent / ".assets" / "voice_catalog"
    if catalog_root.exists():
        shutil.rmtree(catalog_root)

    repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(repo)
    provider = FakeVoiceDesignProvider()
    manifest = service.load_or_bootstrap_manifest(provider)
    manifest_path = repo.manifest_path(manifest.provider, manifest.model)

    require(manifest_path.exists(), "manifest was not written")
    require(manifest.provider == "fake", "provider mismatch")
    require(manifest.model == "fake-tts", "model/resource mismatch")
    require(manifest.sample_emotions == ["normal"], "sample emotions mismatch")
    require(len(manifest.voices) == 3, "fake speaker count mismatch")
    require(repo.voice_by_type(manifest, "fake_male_voice") is not None, "voice_type lookup failed")
    require(repo.manifest_hash(manifest) == repo.manifest_hash(repo.load_manifest("fake", "fake-tts")), "hash changed after reload")

    print("voice_catalog_manifest_smoke=ok")
    print(f"manifest_path={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
