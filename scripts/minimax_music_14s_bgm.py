from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.minimax.music.music_26 import MiniMaxMusicProvider  # noqa: E402


SCENE_TEXT = """镜头起始于云层上方极高处的静止大远景，视角倾斜约80度俯视下方如液态白银般粘稠翻滚的云海，云层具有金属质感，流动极为缓慢。镜头开始时极其缓慢地向前推近，3秒后加速并垂直向下俯冲，穿过湿冷雾层，雾气在镜头上激起轻微凝结的水汽光斑，光线通过雾气散射呈弥漫性柔光，无直射阳光，整体色彩为低饱和度、低色温的铅灰、冷青与银白。俯冲过程中，风声渐强，夹杂低频如远方冰川断裂的闷响。镜头最终减速悬停在距离绝壁边缘约15米处，视线与崖面齐平，此时画面四边柔焦，中心清晰，胶片颗粒显眼，环境音为持续的低频风声和细微的电气嗡鸣。

接着，镜头以前推轨方式缓慢向云澜推进，从左侧45度角接近。云澜立于崖畔，全身入镜，面容清俊，黑色长发部分束起，额前碎发被风拂动，眉心银色印记在微弱天光下隐约可见。他身穿白色交领长袍，丝绸质料垂坠自然，衣襟银纹刺绣呈现哑光质感，衣摆在气流中轻微摆动，布料褶皱物理真实。身侧斜倚灵渊剑，剑身半透明，内部星云光晕缓慢旋转，剑格琥珀呼吸般明灭。云澜缓缓深呼吸，胸腔起伏，手指轻握剑柄，指腹剑茧摩擦皮革，喉结微动。

镜头继续向云澜的面部推进，变为中近景，从正侧逆光方向照亮他的左脸，伦勃朗光形成三角区域，皮肤毛孔和细微绒毛清晰，耳后灼烧状疤痕凹凸不平，表面微光。突然，镜头微微上扬，云海深处传来低沉的轰鸣，脚下的云层骤然从中破开一道裂隙，一束冷白聚光灯般的天光自裂隙中直射而下，瞬间笼罩云澜全身。天光强光过曝，画面中央高亮，云澜衣袍上的暗金符文刺绣被点亮，金色粒子光尘从绣隙中飘散，在光束中飞舞。灵渊剑被光映射，剑身星云旋转加速，散射出细微棱镜色散。

镜头切换至极特写，聚焦云澜的眉心银色印记，此时印记因天光注入而脉动发光，如呼吸般明暗交替，周围漂浮的粒子形成光尘涡旋。云澜保持静止凝视上方，眼神深邃孤寂。结尾停在眉心印记极特写，脉动光与漂浮粒子清晰，画面高对比，背景暗去，为下一镜头直接硬切至九渊裂谷下坠做视觉准备。声音方面：云海破开时有雷鸣般的低频破裂声，天光照射时伴随高频的净化音效，粒子飘散时有细微的晶体碰撞声，整体混响空间开阔。"""


DEFAULT_MUSIC_PROMPT = """14-second cinematic instrumental background score for a xianxia fantasy cliff scene, no vocals, no lyrics.
Timeline and music direction:
0-3s: extremely sparse, high-altitude stillness above metallic silver clouds; deep sub-bass drone, cold airy pads, distant icy wind, very low tempo, low saturation mood.
3-7s: sudden vertical dive through wet fog; rising wind noise texture, swelling low brass and sub-bass impact, distant glacier-crack rumble, fast downward tension, no percussion groove.
7-10s: decelerate near the cliff and slowly push toward Yunlan; solemn strings, restrained taiko-like low hits, shimmering glass harmonics, lonely heroic atmosphere.
10-13s: cold white heavenly light tears open the cloud sea; powerful but clean crescendo, high-frequency purification shimmer, golden particle sparkle, prism-like crystal chimes, wide cathedral reverb.
13-14s: hard stop into an extreme close-up feeling; leave a suspended pulsing silver mark, dark tail, one-second fade out for a direct cut.
Style: epic cinematic fantasy, cold silver-gray and cyan emotional color, spacious reverb, sub-bass wind, crystal particles, sacred light, lonely immortal hero, dramatic but not busy."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and save a 14-second-prompted instrumental BGM with MiniMax music_generation."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument(
        "--model",
        default="music-2.6",
        help="MiniMax music model name. Use music-2.6 if the API rejects music-2.7.",
    )
    parser.add_argument("--duration", type=float, default=14.0, help="Requested/target music duration in seconds.")
    parser.add_argument(
        "--output",
        default="outputs/manual_audio/minimax_cloud_cliff_bgm_14s.mp3",
        help="Saved MiniMax output path.",
    )
    parser.add_argument(
        "--full-output",
        default="",
        help="Optional path for the untrimmed MiniMax result. Defaults to <output>.full.<ext>.",
    )
    parser.add_argument("--prompt-file", help="Optional text file overriding the generated music prompt.")
    parser.add_argument("--base-url", help="Override MiniMax base URL from config.")
    parser.add_argument("--format", default="mp3", choices=["mp3", "wav", "m4a", "aac", "ogg"], help="Audio format.")
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--bitrate", type=int, default=256000)
    parser.add_argument("--timeout", type=float, default=900.0, help="Music generation HTTP timeout in seconds.")
    parser.add_argument("--trim", action="store_true", help="Use ffmpeg to force the saved file to --duration seconds.")
    return parser


def resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def final_output_path(args: argparse.Namespace) -> Path:
    path = resolve_path(args.output)
    if path.suffix:
        return path
    return path.with_suffix(f".{args.format}")


def full_output_path(args: argparse.Namespace, final_path: Path) -> Path:
    if args.full_output:
        return resolve_path(args.full_output)
    return final_path.with_name(f"{final_path.stem}.full{final_path.suffix}")


def sidecar_path(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}{suffix}")


def load_music_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return resolve_path(args.prompt_file).read_text(encoding="utf-8").strip()
    return DEFAULT_MUSIC_PROMPT.strip()


async def download_audio(url: str, output_path: Path, timeout_seconds: float) -> None:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise RuntimeError(f"Audio download failed with HTTP {response.status_code}: {response.text[:500]}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)


async def write_audio_result(result, output_path: Path, timeout_seconds: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if result.audio_data:
        data = str(result.audio_data)
        if data.startswith("data:") and ";base64," in data:
            data = data.split(";base64,", 1)[1]
        output_path.write_bytes(base64.b64decode(data))
        return
    if result.audio_url:
        await download_audio(str(result.audio_url), output_path, timeout_seconds)
        return
    raise RuntimeError("MiniMax music result has no audio URL or audio data")


async def trim_audio(ffmpeg_path: str, source_path: Path, output_path: Path, duration_seconds: float) -> None:
    fade_duration = min(1.0, max(0.0, duration_seconds / 4))
    fade_start = max(0.0, duration_seconds - fade_duration)
    audio_filter = (
        f"apad=pad_dur={duration_seconds:.3f},"
        f"atrim=0:{duration_seconds:.3f},"
        f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}"
    )
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-af",
        audio_filter,
        "-t",
        f"{duration_seconds:.3f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "256k",
        str(output_path),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"ffmpeg trim failed with exit code {process.returncode}: {detail}")


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    minimax_settings = settings.providers.get("minimax")
    if minimax_settings is None:
        print("generation_failed=config has no providers.minimax section")
        return 1

    models = dict(minimax_settings.models)
    models["music"] = args.model
    options = dict(minimax_settings.options)
    options.update(
        {
            "music_format": args.format,
            "music_sample_rate": args.sample_rate,
            "music_bitrate": args.bitrate,
            "output_format": "url",
            "is_instrumental": True,
            "lyrics_optimizer": False,
            "music_timeout_seconds": args.timeout,
        }
    )
    update = {"models": models, "options": options}
    if args.base_url:
        update["base_url"] = args.base_url
    provider = MiniMaxMusicProvider(minimax_settings.model_copy(update=update), settings.runtime)

    prompt = load_music_prompt(args)
    final_path = final_output_path(args)
    full_path = full_output_path(args, final_path)

    print(f"provider={provider.name}")
    print(f"endpoint={provider.endpoint}")
    print(f"model={provider.model}")
    print(f"key_present={bool(provider.api_key)}")
    print(f"target_duration_seconds={args.duration:g}")
    print(f"raw_output={full_path}")
    print(f"final_output={final_path}")

    if not provider.api_key:
        print("generation_failed=missing MiniMax API key. Set MINIMAX_API_KEY in apikeys.yaml or environment.")
        return 1

    final_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path(final_path, ".scene.txt").write_text(SCENE_TEXT, encoding="utf-8")
    sidecar_path(final_path, ".prompt.txt").write_text(prompt, encoding="utf-8")

    try:
        result = await provider.generate_music(
            prompt,
            metadata={
                "model": args.model,
                "is_instrumental": True,
                "output_format": "url",
                "audio_setting": {
                    "sample_rate": args.sample_rate,
                    "bitrate": args.bitrate,
                    "format": args.format,
                },
                "duration": int(round(args.duration)),
            },
        )
    except Exception as exc:
        print(f"generation_failed={exc}")
        print("hint=MiniMax official docs currently list music-2.6/music-2.6-free for music_generation.")
        print("hint=If music-2.7 is rejected, rerun with: --model music-2.6")
        return 1

    await write_audio_result(result, full_path, args.timeout)
    sidecar_path(final_path, ".metadata.json").write_text(
        json.dumps(
            {
                "provider": result.provider,
                "model": result.model,
                "audio_id": result.audio_id,
                "audio_url": result.audio_url,
                "audio_format": result.audio_format,
                "duration_seconds": result.duration_seconds,
                "sample_rate": result.sample_rate,
                "request_id": result.request_id,
                "usage": result.usage,
                "raw_response": result.raw_response,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"minimax_duration_seconds={result.duration_seconds or '-'}")
    print(f"saved_raw={full_path}")
    if not args.trim:
        if full_path != final_path:
            final_path.write_bytes(full_path.read_bytes())
        print(f"saved={final_path}")
        return 0

    try:
        await trim_audio(settings.runtime.ffmpeg_path, full_path, final_path, args.duration)
    except FileNotFoundError:
        print(f"trim_failed=ffmpeg not found: {settings.runtime.ffmpeg_path}")
        print(f"saved_raw={full_path}")
        print("hint=Install ffmpeg or set runtime.ffmpeg_path in config.yaml, then rerun with --trim.")
        return 1
    print(f"saved_trimmed={final_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
