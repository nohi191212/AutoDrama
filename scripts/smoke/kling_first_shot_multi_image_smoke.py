"""Minimal Kling prompt comparison script.

Edit PROMPTS and IMAGE_LIST, then run this file.
By default it only writes payload JSON files under .tmp/.
Set SUBMIT = True when you want to create real Kling tasks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
import yaml


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / ".tmp" / "kling_prompt_tests"

SUBMIT = os.environ.get("KLING_SUBMIT", "").strip().lower() in {"1", "true", "yes", "on"}
SUBMIT_LIMIT = int(os.environ.get("KLING_SUBMIT_LIMIT", "0") or "0")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("KLING_REQUEST_TIMEOUT_SECONDS", "300") or "300")
PROMPT_LIMIT = int(os.environ.get("KLING_PROMPT_LIMIT", "2500") or "2500")
PROMPT_NAME_FILTER = os.environ.get("KLING_PROMPT_NAME", "").strip()
BASE_URL = "https://api-beijing.klingai.com"
ENDPOINT = "/v1/videos/omni-video"
KLING_API_KEY = "api-key-kling-AR7IhpWJFxyiL8D15Md9DdBwUSw_PxgBck4ksDZ2a5c"
IMAGE_ROOT = ROOT / "outputs" / "huyao" / "assets" / "images"
MIB = 1024 * 1024
IMAGE_COMPRESS_THRESHOLD_BYTES = int(float(os.environ.get("KLING_IMAGE_COMPRESS_THRESHOLD_MB", "10")) * MIB)
IMAGE_COMPRESS_TARGET_BYTES = int(float(os.environ.get("KLING_IMAGE_COMPRESS_TARGET_MB", "8")) * MIB)
COMPRESSED_IMAGE_DIR = OUT_DIR / "compressed_images"
IMAGE_UPLOAD_SUMMARY: list[dict[str, Any]] = []


# 1) Change these prompts to compare different video effects.
PROMPTS = [
    {
        "name": "基准",
        "text": (
            """
            固定输入说明：
            - image_1：clip_start_frame。首个 clip 时是当前 clip 的首帧；非首个 clip 时是上一 clip 的尾帧，只用于当前视频开头连续性。
            - image_2：clip_end_frame。当前 clip 的尾帧，视频必须最终收束到这张图。
            - image_3：当前 clip 的 12 宫格故事板整图，只锁定构图、景别、机位、动作方向、camera shot 边界、镜头节奏和面板顺序。
            - 人物参考：无，只锁定人物身份、脸型、发型、服装、体态、配饰和年龄感。
            - 场景参考：无，只锁定空间结构、材质、光照、尺度和可取景区域。
            - 道具参考：无，只锁定道具造型、材质、尺寸和识别细节。
            
            首尾帧与衔接规则：
            image_1 是当前 clip 的首帧，视频必须从它自然开始；image_2 是当前 clip 的尾帧，视频必须最终收束到它；image_3 是当前 clip 的十二宫格 storyboard，用于执行中段 Camera Shot 运动和切镜节奏。
            
            
            输入清单：
            [  {\n    \"slot\": \"image_1\",\n    \"asset_type\": \"clip_start_frame\",\n    \"label\": \"当前 clip 的首帧关键帧\",\n    \"asset_id\": \"episode_001_clip_001_start_frame\",\n    \"asset_path\": \"assets/images/storyboard_keyframes/episode_001_clip_001_start_frame.png\",\n    \"asset_url\": null,\n    \"required\": true,\n    \"metadata\": {\n      \"clip_id\": \"episode_001_clip_001\",\n      \"frame_role\": \"start\",\n      \"panel_ref\": \"P01\",\n      \"source_clip_id\": \"episode_001_clip_001\"\n    }\n  },\n  {\n    \"slot\": \"image_2\",\n    \"asset_type\": \"clip_end_frame\",\n    \"label\": \"当前 clip 的尾帧关键帧\",\n    \"asset_id\": \"episode_001_clip_001_end_frame\",\n    \"asset_path\": \"assets/images/storyboard_keyframes/episode_001_clip_001_end_frame.png\",\n    \"asset_url\": null,\n    \"required\": true,\n    \"metadata\": {\n      \"clip_id\": \"episode_001_clip_001\",\n      \"frame_role\": \"end\",\n      \"panel_ref\": \"P12\",\n      \"source_clip_id\": \"episode_001_clip_001\"\n    }\n  },\n  {\n    \"slot\": \"image_3\",\n    \"asset_type\": \"storyboard\",\n    \"label\": \"当前 clip 的 12 宫格故事板整图\",\n    \"asset_id\": \"episode_001_clip_001_storyboard\",\n    \"asset_path\": \"assets/images/storyboards/episode_001_clip_001_storyboard.png\",\n    \"asset_url\": \"https://cos.lingkeai.vip/uploads/2026.06/25/20260625143853_18bc3fa5d2bee963f7b1.png\",\n    \"required\": true,\n    \"metadata\": {\n      \"clip_id\": \"episode_001_clip_001\"\n    }\n  }\n]
            
            
            当前 clip 的 camera shot 与十二宫格面板内容：
            内部 camera shots：Camera Shot 1（0-4秒）冷灰日间的古老殿宇，残墙、断柱、破窗、塌裂房梁和厚重蜘蛛网锁定为破败初始空间，中央布满灰尘的圆形石台压在画面深处；用全画幅数字电影机质感、24mm广角、低机位滑轨从碎石地面缓慢推进，灰尘在冷光里漂浮，低频环境嗡鸣与空旷风声铺底，九韶以冷静机械VO逐字说出“检测到适配宿主，灵魂绑定已完成。” Camera Shot 2（4-8秒）切到江未晞三分之二侧脸近景，她是瘦削病弱的高中少女，凌乱黑发低马尾、脸颊雀斑、灰蓝磨损卫衣、旧白T、黑色斜挎包带和脏旧帆布鞋保持角色锁定；35mm手持微晃，焦点从睫毛拉到眼睛，她猛然睁眼、急促吸气，表情从昏沉变成惊醒，视线看向镜头旁侧的殿内深处，不直视镜头。 Camera Shot 3（8-12秒）切到过肩与主观视线结合，50mm缓慢推向落尘石台，江未晞撑起身体环顾四周，残垣断壁、蛛网和断梁在她视线中压迫展开；声音保留衣料摩擦、手掌撑地的沙砾声、远处风穿破窗声和系统低频余响。剧情画面内禁止出现字幕、对白气泡、水印、logo、片段编号和无关可读文字；仅允许故事板底部说明条与颜色图例文字；角色不得保持侧向角度，始终以侧脸、侧身或过肩视角呈现。 十二宫格面板规划 P01-P12：P01（Camera Shot 1）低机位碎石前景，石台远置，蓝箭头标示滑轨慢推，紫字标注低频嗡鸣；本格底部写颜色图例：红=动作，蓝=镜头，绿=构图，橙=灯光，紫=情绪/声音，黑=镜头注记。 P02（Camera Shot 1）断梁蛛网与冷光束，九韶VO机械响起，橙箭头标光源，蓝箭头继续推进；底部同样保留颜色图例。 P03（Camera Shot 1）广角显出环形空地和中央石台，绿线框构图中心，紫字标“绑定完成”的听感；底部同样保留颜色图例。 P04（Camera Shot 1）石台与远处江未晞模糊身影同框，Shot 1结束；切镜标记：在P04与P05所在两行之间的水平分隔线中央画醒目的红色斜杠，并标注 CUT P04-P05，红色斜杠只在故事板分隔线上，不进入剧情画面或首尾关键帧；底部同样保留颜色图例。 P05（Camera Shot 2）江未晞睫毛颤动近景，承接P04-P05剪辑点，红箭头标眼皮动作，蓝箭头标轻微手持；底部同样保留颜色图例。 P06（Camera Shot 2）她猛然睁眼，雀斑、尘土、凌乱发丝清晰，紫字标急促吸气；底部同样保留颜色图例。 P07（Camera Shot 2）她撑地坐起，灰蓝卫衣袖口沾灰，视线扫向画面内深处，红箭头标起身；底部同样保留颜色图例。 P08（Camera Shot 2）她半坐回望石台，惊疑表情，Shot 2结束；切镜标记：在P08与P09所在两行之间的水平分隔线中央画醒目的红色斜杠，并标注 CUT P08-P09；底部同样保留颜色图例。 P09（Camera Shot 3）过肩看石台，蓝箭头向前慢推，承接P08-P09剪辑点；底部同样保留颜色图例。 P10（Camera Shot 3）石台古纹和厚尘特写，蛛网虚焦，紫字标空旷回声；底部同样保留颜色图例。 P11（Camera Shot 3）江未晞侧脸虚焦在前景，视线锁住石台，绿线标纵深；底部同样保留颜色图例。 P12（Camera Shot 3）石台居中停顿，灰尘旋转，江未晞背影边缘化，整张图底部再保留一条全局颜色图例说明带：红=动作，蓝=镜头，绿=构图，橙=灯光，紫=情绪/声音，黑=镜头注记。
            
            模型强相关负向规则：
            - 无字幕、无logo、无水印、无分格线、无面板编号、不要直接展示场景三视图、人物三视图。
            
            Kling Omni 执行要求：
            生成 12.0 秒电影级真人剧视频。严格按当前 clip 的 Camera Shot 段落推进动作和镜头运动；不要逐秒硬切，不要把每个故事板面板当成独立镜头。首尾帧优先级高于 storyboard、人物、场景和道具参考。只在 video_prompt 明确标出的 Camera Shot 边界处切镜，大部分镜头保持 3-6 秒连续运动。非首个 clip 必须从上一 clip 尾帧开始并立刻硬切到当前 clip 的 P01 内容，不要做丝滑变形过渡。参考图只作为输入锚点，不使用 subject element，不生成参考图版式、三视图布局、宫格或面板编号。"""
        ),
    },
    {
        "name": "基准——单角色",
        "text": (
            """
            爱死机动画剧集美学风格，超精细写实CGI动画，电影级戏剧性布光，强烈的明暗对照，高对比度赛博朋克色调，体积雾与丁达尔光束穿透黑暗，复杂精细的表面纹理，清晰聚焦与深景深，虚幻引擎5渲染，4K画质，电影海报级构图，宏大科幻氛围

            <<<image_1>>> 是当前视频的首帧，<<<image_2>>> 是当前视频的尾帧。
            <<<image_3>>> 是视频的 12 宫格故事板整图，只用来参考构图、景别、机位、动作方向、镜头节奏和面板顺序。
            <<<image_4>>> 是视频中 {role_1} 的详细介绍，锁定人物身份、脸型、发型、服装、体态、配饰和年龄感。
            <<<image_5>>> 是视频中所处的场景三维空间示意图。
            
            镜头1（0-2秒）：用全画幅数字电影机质感、24mm广角、低机位滑轨从碎石地面缓慢推进，冷灰日间的古老殿宇，残墙、断柱、破窗、塌裂房梁和厚重蜘蛛网锁定为破败初始空间，中央布满灰尘的圆形石台压在画面深处，灰尘在冷光里漂浮，低频环境嗡鸣与空旷风声铺底。

            镜头2（2-4秒）：切到另一个视角，这个视角中可以远远地看到江未晞趴在地上。
            
            镜头3（4-8秒）：切到江未晞三分之二侧脸近景，她是瘦削病弱的高中少女，凌乱黑发低马尾、脸颊雀斑、灰蓝磨损卫衣、旧白T、黑色斜挎包带和脏旧帆布鞋保持角色锁定；九韶以冷静机械VO逐字说出“检测到适配宿主，灵魂绑定已完成；35mm手持微晃，焦点从睫毛拉到眼睛，江未晞猛然睁眼、急促吸气，表情从昏沉变成惊醒，视线看向镜头旁侧的殿内深处，不直视镜头。 
            
            镜头4（8-11秒）：切到江未晞的第一人称视角，视角逐渐转动，视角中心缓缓掠过石台，然后快速回到石台。

            镜头5（11-15秒）：切到过肩与主观视线结合，50mm缓慢推向落尘石台，江未晞撑起身体环顾四周，残垣断壁、蛛网和断梁在她视线中压迫展开；声音保留衣料摩擦、手掌撑地的沙砾声、远处风穿破窗声和系统低频余响。
            
            - 角色不得正对镜头，始终以侧脸、侧身或过肩视角呈现。 
            - 无字幕、无logo、无水印、无分格线、无面板编号，禁止直接展示场景三视图、场景俯瞰图、人物三视图。
            
            """
        ),
    },
    {
        "name": "基准——单角色",
        "text": (
            """
            爱死机动画剧集美学风格，超精细写实CGI动画，电影级戏剧性布光，强烈的明暗对照，高对比度赛博朋克色调，体积雾与丁达尔光束穿透黑暗，复杂精细的表面纹理，清晰聚焦与深景深，虚幻引擎5渲染，4K画质，电影海报级构图，宏大科幻氛围

            <<<image_1>>> 是当前视频的首帧，<<<image_2>>> 是当前视频的尾帧。
            <<<image_3>>> 是视频的 12 宫格故事板整图，只用来参考构图、景别、机位、动作方向、镜头节奏和面板顺序。
            <<<image_4>>> 是视频中 {role_1} 的详细介绍，锁定人物身份、脸型、发型、服装、体态、配饰和年龄感。
            <<<image_5>>> 是视频中所处的场景三维空间示意图。
            
            镜头1（0-2秒）：
            景别运镜： 大远景转 24mm 广角低机位慢速滑轨推进。
            画面内容： 纯黑中被一声苍凉的旷野风声撕开。冷灰色的昼光透过破败的天窗如利剑般斜刺入殿宇深处。画面从地面的碎石与一具死寂的枯蝉细节开始，低机位滑轨向前，残墙、断柱在极深的透视下层层交错，厚重的蜘蛛网在冷光中如半透明的裹尸布般微微晃动。深处的圆形石台在丁达尔光效（斜射光束）中沉睡，万千灰尘在光晕里如星屑般疯狂漂浮。
            声音设计： 极低频的空气嗡鸣、长风穿过残垣的空洞呜咽。

            镜头2（2-4秒）：
            景别运镜： 俯瞰远景，固定机位，微弱的呼吸感摇晃。
            画面内容： 切至高处断梁之上的俯视视角。在庞大、冰冷、宛如巨兽肋骨般的塌裂房梁阴影下，江未晞那瘦削的身影显得极其渺小，她如同一件被丢弃的灰蓝色旧物，毫无生机地趴在龟裂的石板地面上。一缕冷光刚好打在她磨损的卫衣一角，边缘被黑暗吞噬。
            声音设计： 风声渐弱，引入极其微弱、不规律的心跳声（咚……咚……）。
            
            镜头3（4-8秒）：
            景别运镜： 三分之二侧脸特写，35mm 手持感微晃。
            画面内容：焦距锁在少女病弱、略带雀斑的苍白脸颊上，几缕凌乱的黑发散落。画外音（九韶的冷静机械音）响起的瞬间，伴随着“全息波纹”在空气中一闪而逝（用动画独有的几何线条或光效粒子划过她面部）。6-8秒：焦点暴起，从睫毛闪速拉到瞳孔。江未晞猛然睁眼，瞳孔骤缩（动画特写：瞳孔在微秒内的震颤与放大），她急促地大口吸气，胸口剧烈起伏。她没有看向镜头，而是带着惊恐与空洞，死死盯向斜侧方的殿宇深处。九韶（VO）： 「检测到适配宿主。灵魂绑定——已完成。」（声音带有点阵电子音的质感，直接在3D环绕脑海中炸开）。
            声音设计： 机械音落下的刹那，心跳声陡然加速并放大，伴随一声极具真实感的、甚至带有点黏着感的窒息式吸气声。
            
            镜头4（8-11秒）：
            景别运镜： 第一人称主观视角，模拟人类惊醒时的视线模糊与失焦（Lens Blur）。
            画面内容： 视线伴随着不稳定的粗重呼吸剧烈晃动、重影。视线先是茫然地向右转动，杂乱无章地掠过布满蜘蛛网的断梁；突然，视线中心缓缓滑过了那个落满灰尘的圆形石台——此时大脑还未反应过来，视线继续往左移了一点；第10秒：少女猛然意识到不对，视线极速、神经质般地“甩回”（Whip Pan/快摆镜头），死死锁定了大殿中央的那座石台。
            声音设计： 强烈的耳鸣声铺底，少女粗重的、混有沙哑颗粒感的呼吸声就在耳边。

            镜头5（11-15秒）：
            景别运镜： 低机位过肩拉远/推镜头。
            画面内容： 镜头卡在江未晞单侧灰蓝色的肩膀后方。她用脏旧的帆布鞋蹬着地面，手掌撑地，在一阵沙砾摩擦声中勉强撑起瘦削的身体。50mm 的镜头开始缓缓向前推进，越过她的肩膀锁定那座石台。此时，周围原本压迫的残垣断壁在纵深推移中仿佛活了过来，向画幅边缘压迫退去，而中央那座巨大的、布满历史尘埃的圆形石台，在冰冷的丁达尔光柱中，散发出一种古老、神秘且诡异的神圣感。
            声音设计： 衣料的沙沙摩擦声、手掌压碎砂砾的清脆声。远处的风声在这一刻突然拉远，取而代之的是系统绑定成功后的低频低鸣余响，预示着少女的命运在此处彻底转折。


            - 角色不得正对镜头，始终以侧脸、侧身或过肩视角呈现。 
            - 无字幕、无logo、无水印、无分格线、无面板编号，禁止直接展示场景三视图、场景俯瞰图、人物三视图。
            
            """
        ),
    },
]


def image_url_value(value: str | Path) -> str:
    text = str(value)
    if text.startswith(("http://", "https://", "data:")):
        return text

    path = Path(text)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path}")
    upload_path = prepare_image_for_upload(path)
    return base64.b64encode(upload_path.read_bytes()).decode("ascii")


def prepare_image_for_upload(path: Path) -> Path:
    source_bytes = path.stat().st_size
    upload_path = path
    compressed = False
    if source_bytes > IMAGE_COMPRESS_THRESHOLD_BYTES:
        upload_path = compress_image(path)
        compressed = True

    upload_bytes = upload_path.stat().st_size
    IMAGE_UPLOAD_SUMMARY.append(
        {
            "source_path": str(path),
            "upload_path": str(upload_path),
            "source_mb": round(source_bytes / MIB, 2),
            "upload_mb": round(upload_bytes / MIB, 2),
            "compressed": compressed,
        }
    )
    return upload_path


def compress_image(path: Path) -> Path:
    COMPRESSED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    digest = hashlib.sha1(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:12]
    target_mb = round(IMAGE_COMPRESS_TARGET_BYTES / MIB, 2)
    output_path = COMPRESSED_IMAGE_DIR / f"{path.stem}_{digest}_target_{target_mb:g}mb_q95.jpg"
    if output_path.exists() and output_path.stat().st_size <= IMAGE_COMPRESS_TARGET_BYTES:
        return output_path

    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            work = image_to_rgb(image)
    except UnidentifiedImageError as exc:
        raise RuntimeError(f"Cannot compress unsupported image file: {path}") from exc

    qualities = (95, 93, 90, 88, 85, 82, 80, 75, 70, 65, 60, 55, 50, 45)
    for _ in range(8):
        for quality in qualities:
            work.save(output_path, "JPEG", quality=quality, optimize=True, progressive=True)
            if output_path.stat().st_size <= IMAGE_COMPRESS_TARGET_BYTES:
                return output_path

        current_bytes = output_path.stat().st_size
        scale = min(0.9, max(0.65, (IMAGE_COMPRESS_TARGET_BYTES / current_bytes) ** 0.5 * 0.95))
        new_size = (max(1, int(work.width * scale)), max(1, int(work.height * scale)))
        if new_size == work.size:
            break
        work = work.resize(new_size, Image.Resampling.LANCZOS)

    return output_path


def image_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def image_item(value: str | Path, *, image_type: str | None = None) -> dict[str, str]:
    item = {"image_url": image_url_value(value)}
    if image_type is not None:
        item["type"] = image_type
    return item


def redacted_image_list(image_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    redacted: list[dict[str, Any]] = []
    for item in image_list:
        clone = dict(item)
        value = clone.get("image_url")
        if isinstance(value, str) and not value.startswith(("http://", "https://", "data:")):
            clone["image_url"] = f"<base64 {len(value)} chars>"
        redacted.append(clone)
    return redacted


# 2) Change these paths/URLs to your first frame / end frame / storyboard / scene / character / prop.
# Kling uses type=first_frame and type=end_frame inside image_list for the opening/ending frame anchors.
IMAGE_LIST = [
    image_item(IMAGE_ROOT / "storyboard_keyframes" / "episode_001_clip_001_start_frame.png", image_type="first_frame"),
    image_item(IMAGE_ROOT / "storyboard_keyframes" / "episode_001_clip_001_end_frame.png", image_type="end_frame"),
    image_item(IMAGE_ROOT / "storyboards" / "episode_001_clip_001_storyboard.png"),
    image_item(IMAGE_ROOT / "roles" / "role_江未晞_appearance_base_roleboard.png"),
    image_item(IMAGE_ROOT / "layouts" / "layout_古老殿宇.png"),
]


# 3) This is the base payload. Keep it simple and edit directly when needed.
PAYLOAD_BASE = {
    "model_name": "kling-v3-omni",
    "mode": "pro",
    "duration": "15",
    "aspect_ratio": "9:16",
    "sound": "off",
    "watermark_info": {"enabled": False},
    "image_list": IMAGE_LIST,
}


def load_api_key() -> str | None:
    key = os.environ.get("KLING_API_KEY")
    if key:
        return key
    if KLING_API_KEY:
        return KLING_API_KEY

    apikeys_path = ROOT / "apikeys.yaml"
    if not apikeys_path.exists():
        return None

    data = yaml.safe_load(apikeys_path.read_text(encoding="utf-8")) or {}
    return data.get("KLING_API_KEY")


def build_payload(prompt: str) -> dict:
    payload = dict(PAYLOAD_BASE)
    payload["image_list"] = [dict(item) for item in IMAGE_LIST]
    payload["prompt"] = str(prompt or "").strip()[:PROMPT_LIMIT]
    return payload


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def submit_payload(payload: dict) -> dict:
    api_key = load_api_key()
    if not api_key:
        raise RuntimeError("Missing KLING_API_KEY in environment or apikeys.yaml")

    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=60.0, write=REQUEST_TIMEOUT_SECONDS)
    response = httpx.post(
        f"{BASE_URL}{ENDPOINT}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    try:
        body = response.json()
    except ValueError:
        body = {"raw_text": response.text}
    if response.status_code >= 400:
        body["_http_status_code"] = response.status_code
        raise RuntimeError(f"Kling submit failed HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)[:1000]}")
    return body


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image_summary_path = OUT_DIR / "image_upload_summary.json"
    save_json(image_summary_path, {"images": IMAGE_UPLOAD_SUMMARY})
    print(f"image upload summary: wrote {image_summary_path}")

    image_list_debug_path = OUT_DIR / "image_list_redacted.json"
    save_json(image_list_debug_path, {"image_list": redacted_image_list(IMAGE_LIST)})
    print(f"image_list: wrote redacted preview {image_list_debug_path}")

    matched_count = 0
    submitted_count = 0
    for item in PROMPTS:
        name = item["name"]
        if PROMPT_NAME_FILTER and name != PROMPT_NAME_FILTER:
            continue
        matched_count += 1

        payload = build_payload(item["text"])
        original_prompt_len = len(str(item["text"] or "").strip())
        prompt_len = len(payload["prompt"])

        payload_path = OUT_DIR / f"{name}_payload.json"
        save_json(payload_path, payload)
        print(f"{name}: wrote {payload_path}")
        if original_prompt_len != prompt_len:
            print(f"{name}: prompt truncated {original_prompt_len} -> {prompt_len} chars")

        if SUBMIT and (SUBMIT_LIMIT <= 0 or submitted_count < SUBMIT_LIMIT):
            submitted_count += 1
            result_path = OUT_DIR / f"{name}_response.json"
            try:
                result = submit_payload(payload)
            except Exception as exc:
                result = {"error": str(exc)}
                save_json(result_path, result)
                print(f"{name}: submit failed, response={result_path}")
                raise
            save_json(result_path, result)
            task_id = result.get("data", {}).get("task_id")
            print(f"{name}: submitted task_id={task_id}, response={result_path}")

    if PROMPT_NAME_FILTER and matched_count == 0:
        raise RuntimeError(f"No prompt named {PROMPT_NAME_FILTER!r}")


if __name__ == "__main__":
    main()
