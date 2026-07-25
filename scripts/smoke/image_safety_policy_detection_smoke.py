from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase  # noqa: E402


def main() -> None:
    english_error = ProviderBadResponseError("generated images appear to be unsafe")
    chinese_error = ProviderBadResponseError(
        "AIBOX image task 54415429 failed: 任务执行失败：我们很抱歉，我们创建的图像可能违反了我们的内容政策。"
    )
    aibox_review_error = ProviderBadResponseError(
        "AIBOX image task 77786026 failed: 提交的内容未通过安全审核，请修改内容后重试。"
    )
    neutral_error = ProviderBadResponseError("AIBOX image task failed: no image URL returned")

    if not StaticAssetNodeBase._is_image_safety_failure(english_error):
        raise AssertionError("english safety error should be detected")
    if not StaticAssetNodeBase._is_image_safety_failure(chinese_error):
        raise AssertionError("chinese content policy error should be detected")
    if not StaticAssetNodeBase._is_image_safety_failure(aibox_review_error):
        raise AssertionError("AIBOX safety review error should be detected")
    if StaticAssetNodeBase._is_image_safety_failure(neutral_error):
        raise AssertionError("neutral provider error should not be treated as safety failure")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "image_safety_policy_detection_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("image_safety_policy_detection_smoke: ok")


if __name__ == "__main__":
    main()
