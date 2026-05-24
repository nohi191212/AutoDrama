from __future__ import annotations

import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
PACKAGE_SRC_DIR = SRC_DIR / "autodrama"
package = sys.modules.get("autodrama")
if package is not None and hasattr(package, "__path__"):
    package.__path__.append(str(PACKAGE_SRC_DIR))

from autodrama.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
