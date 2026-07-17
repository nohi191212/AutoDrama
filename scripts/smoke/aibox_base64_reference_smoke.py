from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.aibox.image.gpt_image import AiboxImageProvider  # noqa: E402


def sanitized(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitized(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return f"<base64 data URL omitted; chars={len(value)}>"
    return value


async def main() -> None:
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    output_dir = ROOT / ".tmp" / "aibox_protocol" / "base64_reference"
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_path = output_dir / "reference.png"

    image = Image.new("RGB", (256, 256), (40, 100, 210))
    draw = ImageDraw.Draw(image)
    draw.ellipse((64, 64, 192, 192), fill=(230, 60, 60))
    image.save(reference_path)
    data_url = f"data:image/png;base64,{base64.b64encode(reference_path.read_bytes()).decode('ascii')}"

    settings = load_settings(config_path)
    provider = AiboxImageProvider(settings.providers["aibox"], settings.runtime)
    payload = provider.build_payload(
        "严格参考输入图片的构图，保留中央圆形，把蓝色背景改成绿色，简洁平面图形，无文字。",
        size="1024x1024",
        reference_images=[data_url],
        metadata={"model": provider.model, "quality": "auto"},
    )
    (output_dir / "request.json").write_text(
        json.dumps(sanitized(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    async with httpx.AsyncClient(timeout=provider._http_timeout()) as client:
        response = await client.post(provider.generation_endpoint, headers=provider._headers(), json=payload)
        body = provider._json_response(response, label="AIBOX base64 reference generation")
        (output_dir / "create_response.json").write_text(
            json.dumps(sanitized(body), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"create_http_status={response.status_code}")
        print(f"create_body={json.dumps(sanitized(body), ensure_ascii=False)}")
        provider._raise_for_error(response, body, label="AIBOX base64 reference generation")
        task_id = provider._task_id(body)
        final_body = await provider._wait_for_task(client, task_id) if task_id else body

    (output_dir / "final_response.json").write_text(
        json.dumps(sanitized(final_body), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    image_urls, image_data = provider._extract_images(final_body)
    print(f"task_id={task_id or '-'}")
    print(f"task_status={provider._status(final_body) or '-'}")
    print(f"image_urls={len(image_urls)} image_data={len(image_data)}")
    if image_urls:
        print(f"result_url={image_urls[0]}")


if __name__ == "__main__":
    asyncio.run(main())
