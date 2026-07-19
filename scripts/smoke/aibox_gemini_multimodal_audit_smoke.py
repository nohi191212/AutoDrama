from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "autodrama" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.config import load_settings
from autodrama.postgen.schemas import PostgenFinalAuditReport
from autodrama.providers.base import AssetRef
from autodrama.providers.router import ProviderRouter


async def main() -> None:
    image_path = ROOT / ".tmp" / "postgen_smoke" / "aibox_gemini_audit.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (480, 270), "#202938")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 440, 230), outline="#f2f2f2", width=4)
    draw.ellipse((190, 75, 290, 175), fill="#3d8bfd")
    image.save(image_path, quality=85)
    video_path = ROOT / ".tmp" / "postgen_smoke" / "aibox_gemini_audit.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=8:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video_path),
        ],
        check=True,
    )

    settings = load_settings(ROOT / "config.yaml")
    provider = ProviderRouter(settings).text("postgen_audit", node_name="postgen_final_audit")
    report = await provider.generate_json(
        "审计这张测试画面。它不是实际成片，只验证多模态 JSON 接口。返回 pass，score 90，episode_key=smoke。",
        PostgenFinalAuditReport,
        temperature=0.1,
        refs=[
            AssetRef(id="frame", type="image", path=str(image_path)),
            AssetRef(id="video", type="video", path=str(video_path)),
        ],
        metadata={"node_name": "postgen_final_audit", "episode_key": "smoke", "max_output_tokens": 1024},
    )
    assert report.episode_key == "smoke", report
    print(f"ok provider={getattr(provider, 'name', '-')} model={getattr(provider, 'model', '-')} verdict={report.verdict}")


if __name__ == "__main__":
    asyncio.run(main())
