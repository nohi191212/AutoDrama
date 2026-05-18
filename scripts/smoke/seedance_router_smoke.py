from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider  # noqa: E402


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    provider = ProviderRouter(settings).video("shot")
    assert isinstance(provider, VolcengineSeedanceVideoProvider)
    assert provider.model == settings.providers["volcengine"].models["seedance_2"]
    assert provider.ratio == settings.providers["volcengine"].options["video_ratio"]
    print("seedance_router_smoke=ok")
    print(f"provider={provider.name} model={provider.model} ratio={provider.ratio}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
