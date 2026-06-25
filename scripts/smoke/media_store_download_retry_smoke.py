from __future__ import annotations

import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import Settings
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.services.media_store import MediaStore


class FlakyImageHandler(BaseHTTPRequestHandler):
    attempts = 0
    payload = b"fake image bytes"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        type(self).attempts += 1
        if type(self).attempts < 3:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"temporary failure")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)


async def _run() -> None:
    tmp_dir = ROOT / ".tmp" / "media_store_download_retry_smoke"
    project_dir = tmp_dir / "project"
    output_path = project_dir / "assets" / "images" / "storyboards" / "retry.png"
    project_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    layout = ProjectLayout(settings)
    store = MediaStore(layout, timeout_seconds=5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/image.png"
        result = SimpleNamespace(image_data=[], image_urls=[url])
        rel_path = await store.write_first_generated_image(project_dir, output_path, result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if rel_path != "assets/images/storyboards/retry.png":
        raise AssertionError(f"unexpected project-relative path: {rel_path}")
    if output_path.read_bytes() != FlakyImageHandler.payload:
        raise AssertionError("downloaded payload mismatch")
    if FlakyImageHandler.attempts != 3:
        raise AssertionError(f"expected 3 attempts, got {FlakyImageHandler.attempts}")

    (ROOT / ".tmp" / "media_store_download_retry_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("media_store_download_retry_smoke: ok")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
