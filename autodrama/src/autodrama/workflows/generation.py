from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from autodrama.core.schemas import (
    DynamicAssetSolidificationOutput,
    ProjectState,
    ShotDialogueAudioGenerationOutput,
    ShotVideoGenerationOutput,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.dynamic_asset_repo import DynamicAssetRepository
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.delegation import PregenWorkflowDelegateMixin
from autodrama.workflows.generation_checklist import (
    selected_episode_keys_from_checklist,
    update_checklist_from_state,
)
from autodrama.workflows.context import WorkflowRunContext
from autodrama.workflows.dynamic_assets import DynamicAssetNodeMixin
from autodrama.workflows.generation_tasks import load_generation_tasks, save_generation_tasks
from autodrama.workflows.nodes import GENERATION_NODE_NAMES, build_generation_episode_nodes
from autodrama.workflows.selection import sort_episode_keys_in_story_order

GENERATION_NODES = GENERATION_NODE_NAMES

DEFAULT_GENERATION_NODES = list(GENERATION_NODE_NAMES)


class GenerationWorkflow(DynamicAssetNodeMixin, PregenWorkflowDelegateMixin):
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self._init_pregen_delegate(repo=repo, router=router, prompts=prompts)
        self.dynamic_assets = DynamicAssetRepository(self.repo, self.layout)
        self.generation_episode_nodes = build_generation_episode_nodes(self)

    @staticmethod
    def _cg_character_safety_prompt() -> str:
        return (
            "人物形象安全风格要求: 保持项目整体电影级写实CG画面语言，但人类/类人角色必须是高质量风格化CG动漫角色，"
            "不是照片级真人肖像；脸部、皮肤和毛发要有CG建模与动画电影材质感，避免真实摄影人像、真实皮肤毛孔、"
            "汗渍、微血管、皮肤斑点、真实人脸扫描、明星脸或身份证照感。角色识别依靠风格化脸型、眼型、发型、"
            "服装剪裁、配饰、色彩和气质。"
        )

    @staticmethod
    def _no_flat_front_facing_video_prompt() -> str:
        return (
            "人物朝向约束: 视频中人物不要呈现证件照式、完全正对镜头的僵硬构图；"
            "除非当前剧情明确是对镜头直播、自拍或正面宣告，人物脸部和身体应保持轻微侧转，"
            "可采用约 15-45 度的三分之二侧脸、侧身、过肩、低头抬眼、视线看向画面内对象或镜头旁侧。"
            "即使需要表现人物看向观众方向，也要避免双肩水平、脸部完全平贴镜头、眼睛长时间直盯镜头的静态正面姿势。"
        )

    @staticmethod
    def _previous_video_preroll_seconds(provider=None) -> float:
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value = options.get("previous_video_preroll_seconds") or options.get("video_previous_preroll_seconds") or 0
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, seconds)

    @staticmethod
    def _format_seconds(value: float) -> str:
        return f"{value:.1f}".rstrip("0").rstrip(".")

    @classmethod
    def _previous_video_preroll_prompt(cls, shot: StoryboardShot, preroll_seconds: float) -> str:
        preroll_text = cls._format_seconds(preroll_seconds)
        total_duration = float(shot.duration_seconds)
        total_text = cls._format_seconds(total_duration)
        return (
            f"上一镜预滚与硬切结构: 0-{preroll_text}秒取上一镜视频最后{preroll_text}秒作为开场内容，"
            "只承接上一镜尾部视觉状态、动作因果和情绪余韵，不重复上一镜对白，不把上一镜继续演成新剧情；"
            f"第{preroll_text}秒必须发生一次清晰硬切；"
            f"{preroll_text}-{total_text}秒才是当前 shot 正文内容，按当前 shot 的剧情、动作和对白推进。"
        )

    @staticmethod
    def _clean_prompt_text(value: object) -> str:
        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _dialogue_text_from_line(cls, line: str) -> tuple[str | None, str]:
        text = cls._clean_prompt_text(line)
        for separator in ("：", ":"):
            if separator in text:
                speaker, body = text.split(separator, 1)
                speaker = cls._clean_prompt_text(speaker)
                body = cls._clean_prompt_text(body)
                if speaker and body:
                    return speaker, body
        return None, text

    @classmethod
    def _dialogue_body_in_prompt(cls, prompt: str, body: str) -> bool:
        normalized = cls._clean_prompt_text(body).strip("“”\"'。！？!?，,；;：: ")
        return bool(normalized) and normalized in cls._clean_prompt_text(prompt)

    @classmethod
    def _ensure_dialogue_in_video_prompt_body(cls, body: str, dialogue: list[str]) -> str:
        body = cls._clean_prompt_text(body)
        additions: list[str] = []
        for line in dialogue:
            speaker, dialogue_body = cls._dialogue_text_from_line(line)
            if not dialogue_body or cls._dialogue_body_in_prompt(body, dialogue_body):
                continue
            if speaker:
                additions.append(f"{speaker}说：“{dialogue_body}”。说话时口型清晰匹配这句台词。")
            else:
                additions.append(f"画面内说话者说：“{dialogue_body}”。说话时口型清晰匹配这句台词。")
        if additions:
            body = (body.rstrip("。") + "。" if body else "") + " ".join(additions)
        return body

    def _shot_has_previous_video_reference(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        *,
        provider=None,
    ) -> bool:
        refs = self._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
        groups = self._shot_video_prompt_ref_groups(refs, provider=provider)
        return any(
            self._shot_video_ref_asset_type(ref) in {"previous_shot_video", "reference_and_previous_shot_video"}
            for ref in groups["video"]
        )

    @staticmethod
    def _shot_video_ref_asset_type(ref) -> str:
        metadata = getattr(ref, "metadata", {}) or {}
        return str(metadata.get("asset_type") or "").strip()

    @staticmethod
    def _shot_video_ref_label(ref) -> str:
        metadata = getattr(ref, "metadata", {}) or {}
        for key in ("role_name", "name", "source_shot_id", "previous_shot_id", "scene_reference_shot_id"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return str(getattr(ref, "id", None) or "").strip() or "未命名参考"

    @staticmethod
    def _shot_video_ref_seedance_role(ref) -> str:
        metadata = getattr(ref, "metadata", {}) or {}
        return str(metadata.get("seedance_role") or metadata.get("role") or "reference_image").strip().lower()

    @staticmethod
    def _shot_video_ref_has_web_url(ref) -> bool:
        url = str(getattr(ref, "url", None) or "").strip()
        path = str(getattr(ref, "path", None) or "").strip()
        return url.startswith(("http://", "https://")) or path.startswith(("http://", "https://"))

    @staticmethod
    def _shot_video_provider_supports_audio_refs(provider=None) -> bool:
        if provider is None:
            return False
        explicit = getattr(provider, "supports_audio_references", None)
        if explicit is not None:
            return bool(explicit)
        if hasattr(provider, "max_reference_audio"):
            try:
                return int(getattr(provider, "max_reference_audio") or 0) > 0
            except (TypeError, ValueError):
                return False
        return False

    @classmethod
    def _shot_video_prompt_ref_groups(cls, refs: list, provider=None) -> dict[str, list]:
        image_refs = [ref for ref in refs if getattr(ref, "type", None) == "image"]
        audio_refs = [ref for ref in refs if getattr(ref, "type", None) == "audio"]
        video_refs = [ref for ref in refs if getattr(ref, "type", None) == "video"]
        element_refs = [ref for ref in refs if getattr(ref, "type", None) == "element"]

        frame_image_refs = [
            ref
            for ref in image_refs
            if cls._shot_video_ref_seedance_role(ref) in {"first_frame", "last_frame"}
        ]
        if frame_image_refs:
            selected_frames = []
            seen_roles: set[str] = set()
            for ref in frame_image_refs:
                role = cls._shot_video_ref_seedance_role(ref)
                if role in seen_roles:
                    continue
                seen_roles.add(role)
                selected_frames.append(ref)
            return {"image": selected_frames, "audio": [], "video": []}

        max_images = int(getattr(provider, "max_reference_images", 99) or 99)
        max_elements = int(getattr(provider, "max_reference_elements", 99) or 99)
        max_audio = (
            int(getattr(provider, "max_reference_audio", 0) or 0)
            if cls._shot_video_provider_supports_audio_refs(provider)
            else 0
        )
        max_videos = int(getattr(provider, "max_reference_videos", 99) or 99)
        if bool(getattr(provider, "reference_video_requires_web_url", False)):
            video_refs = [ref for ref in video_refs if cls._shot_video_ref_has_web_url(ref)]
        return {
            "image": image_refs[:max_images],
            "element": element_refs[:max_elements],
            "audio": audio_refs[:max_audio],
            "video": video_refs[:max_videos],
        }

    def _shot_video_refs_for_provider(self, refs: list, provider=None) -> list:
        groups = self._shot_video_prompt_ref_groups(refs, provider=provider)
        return [*groups["image"], *groups.get("element", []), *groups["audio"], *groups["video"]]

    def _shot_video_reference_plan(
        self,
        refs: list,
        *,
        provider=None,
    ) -> dict[str, object]:
        groups = self._shot_video_prompt_ref_groups(refs, provider=provider)
        modal_plan: dict[str, list[dict[str, object]]] = {}

        def compact_ref_value(value: object) -> object:
            if isinstance(value, str) and value.startswith("data:") and len(value) > 240:
                return f"{value[:240]}...[truncated]"
            return value

        for group_name, group_refs in groups.items():
            items: list[dict[str, object]] = []
            for index, ref in enumerate(group_refs, start=1):
                items.append(
                    {
                        "slot": f"{group_name}_{index}",
                        "id": getattr(ref, "id", None),
                        "type": getattr(ref, "type", None),
                        "asset_type": self._shot_video_ref_asset_type(ref),
                        "label": self._shot_video_ref_label(ref),
                        "element_id": (getattr(ref, "metadata", {}) or {}).get("element_id"),
                        "path": compact_ref_value(getattr(ref, "path", None)),
                        "url": compact_ref_value(getattr(ref, "url", None)),
                    }
                )
            modal_plan[group_name] = items

        image_asset_types = {
            str(item.get("asset_type") or "")
            for item in modal_plan.get("image", [])
        }
        element_asset_types = {
            str(item.get("asset_type") or "")
            for item in modal_plan.get("element", [])
        }
        if bool(getattr(provider, "supports_subject_elements", False)) or "role_subject_element" in element_asset_types:
            expected_static_anchors = ["storyboard_panel", "key_vision", "role_subject_element"]
            present_static_anchors = [
                "storyboard_panel" if "storyboard_panel" in image_asset_types else "",
                "key_vision" if "key_vision" in image_asset_types else "",
                "role_subject_element" if "role_subject_element" in element_asset_types else "",
            ]
            present_static_anchors = [asset_type for asset_type in present_static_anchors if asset_type]
        else:
            expected_static_anchors = ["storyboard_panel", "roleboard", "key_vision"]
            present_static_anchors = [
                asset_type for asset_type in expected_static_anchors if asset_type in image_asset_types
            ]
        return {
            "static_anchor_policy": (
                "storyboard_panel + key_vision + role_subject_element when Kling subject mode is active"
                if "role_subject_element" in expected_static_anchors
                else "storyboard_panel + roleboard + key_vision when available"
            ),
            "expected_static_anchors": expected_static_anchors,
            "present_static_anchors": present_static_anchors,
            "missing_static_anchors": [
                asset_type for asset_type in expected_static_anchors if asset_type not in present_static_anchors
            ],
            "limits": {
                "max_reference_images": int(getattr(provider, "max_reference_images", 99) or 99),
                "max_reference_elements": int(getattr(provider, "max_reference_elements", 99) or 99),
                "max_reference_audio": (
                    int(getattr(provider, "max_reference_audio", 0) or 0)
                    if self._shot_video_provider_supports_audio_refs(provider)
                    else 0
                ),
                "max_reference_videos": int(getattr(provider, "max_reference_videos", 99) or 99),
            },
            "modal_refs": modal_plan,
        }

    @staticmethod
    def _shot_video_static_anchor_label(asset_type: str) -> str:
        labels = {
            "storyboard_panel": "故事板单格",
            "roleboard": "角色身份板",
            "key_vision": "主视觉原图",
        }
        return labels.get(asset_type, asset_type)

    def _shot_video_static_anchor_prompt(self, image_refs: list) -> str:
        expected = ["storyboard_panel", "roleboard", "key_vision"]
        image_slots: dict[str, int] = {}
        for index, ref in enumerate(image_refs, start=1):
            asset_type = self._shot_video_ref_asset_type(ref)
            if asset_type in expected and asset_type not in image_slots:
                image_slots[asset_type] = index

        present_parts = [
            f"{self._shot_video_static_anchor_label(asset_type)}=图片{image_slots[asset_type]}"
            for asset_type in expected
            if asset_type in image_slots
        ]
        missing_parts = [
            self._shot_video_static_anchor_label(asset_type)
            for asset_type in expected
            if asset_type not in image_slots
        ]
        if not present_parts:
            return ""

        parts = [
            "静态三锚点策略: 优先把当前 shot 的故事板单格、角色身份板、主视觉原图作为图片参考一起使用；"
            + "；".join(present_parts)
            + "。"
        ]
        if missing_parts:
            parts.append(
                "本次未传入"
                + "、".join(missing_parts)
                + "；不要臆造缺失参考图的具体细节，只依据当前 shot 文本和已传入素材补足。"
            )
        else:
            parts.append(
                "三者职责不可互相覆盖: 故事板单格决定构图/景别/机位/动作方向，"
                "角色身份板决定画面主体外观，主视觉原图决定世界观/色调/光影/整体制作质感。"
            )
        return "".join(parts)

    @classmethod
    def _shot_video_image_ref_instruction(cls, ref, index: int) -> str:
        asset_type = cls._shot_video_ref_asset_type(ref)
        label = cls._shot_video_ref_label(ref)
        seedance_role = cls._shot_video_ref_seedance_role(ref)
        prefix = f"图片{index}（{label}）"
        if asset_type == "previous_shot_last_frame" or seedance_role == "first_frame":
            return (
                f"{prefix}: 上一 shot 尾帧/first_frame 输入。只用于硬切后承接上一段末尾的人物姿态、"
                "空间方向、道具位置、能量位置和环境粒子；本片段仍按当前描述继续动作，不做软转场，不拖上一段声音。"
            )
        if asset_type == "storyboard_panel":
            return (
                f"{prefix}: 当前 shot 故事板单格，只用于锁定本镜头的构图、景别、机位、人物站位、"
                "动作方向、镜头运动和镜头顺序；不要复刻黑白线稿风格，不要生成编号、边框、标题或文字标签，"
                "最终画质、颜色、材质和角色细节仍以当前 shot 描述、角色身份板和主视觉为准。"
            )
        if asset_type == "key_vision":
            return (
                f"{prefix}: 主视觉原图，只用于世界观、美术风格、色调、光影、质感和整体制作水准参考；"
                "不要用主视觉覆盖当前 shot 的构图、动作、机位、出场角色或剧情节奏。"
            )
        if asset_type == "layout":
            return (
                f"{prefix}: 场景图，作为空间锚点。锁定场景结构、材质、光照基调、关键背景物和空间尺度；"
                "不要从场景图擅自添加当前 shot 未出现的人物或剧情动作。"
            )
        if asset_type == "roleboard":
            return (
                f"{prefix}: 角色身份板，作为当前视觉主体的静态外观锚点。锁定同一人物的脸型、发型、"
                "身体比例、服装层次、配饰、色彩、材质、年龄感和表情/动作习惯；不要把它当成当前 shot 的构图图，"
                "人物在本段中的站位、动作、表情和口型仍以当前 shot 的 video_prompt、故事板单格和对白空间约束为准。"
            )
        if asset_type == "prop":
            return (
                f"{prefix}: 道具设计图，作为道具静态参考锚点。锁定造型、材质、颜色、尺寸感和可识别细节；"
                "道具在画面中的位置和运动以当前 shot 描述为准。"
            )
        return f"{prefix}: 图片参考素材。只用于与其来源一致的静态外观或空间约束，不作为首帧或尾帧。"

    @classmethod
    def _shot_video_audio_ref_instruction(cls, ref, index: int) -> str:
        asset_type = cls._shot_video_ref_asset_type(ref)
        label = cls._shot_video_ref_label(ref)
        prefix = f"音频{index}（{label}）"
        if asset_type == "shot_dialogue_audio":
            return (
                f"{prefix}: 本段对白音频。用于当前台词的口型节奏、语气、情绪强弱和停顿；"
                "声音只发生在本片段内部，不提前入场，不拖尾到下一片段。"
            )
        if asset_type == "role_audio":
            return (
                f"{prefix}: 角色说话声音锚点。锁定该角色音色、年龄感、性别感、语速、咬字和基础情绪；"
                "台词内容必须以当前 shot 的 dialogue/video_prompt 为准，不复述参考音频文本。"
            )
        return f"{prefix}: 音频参考素材。只用于声音质感、语气和口型节奏，不改变当前台词内容。"

    @classmethod
    def _shot_video_video_ref_instruction(cls, ref, index: int) -> str:
        asset_type = cls._shot_video_ref_asset_type(ref)
        label = cls._shot_video_ref_label(ref)
        prefix = f"视频{index}（{label}）"
        if asset_type == "reference_video":
            return (
                f"{prefix}: 同场景镜头视频，作为空间锚点。锁定同一潜在三维空间中的场景结构、人物/道具相对位置、"
                "环境动态、人群/光影规律；允许当前 shot 根据剧情换机位，但不能无故镜像、越轴或反转空间拓扑。"
            )
        if asset_type == "previous_shot_video":
            return (
                f"{prefix}: 上一 shot 镜头视频，作为逻辑连贯性锚点。只承接上一镜硬切前后的动作因果、情绪余韵、"
                "人物/道具状态和节奏，不要求首帧等于上一镜尾帧，不做 J-Cut/L-Cut，不逐帧复刻。"
            )
        if asset_type == "reference_and_previous_shot_video":
            return (
                f"{prefix}: 同场景镜头视频与上一 shot 镜头是同一个素材。它同时承担空间锚点和逻辑连贯性锚点："
                "既保持同一物理空间结构，又承接上一镜硬切后的动作因果、情绪和道具状态；仍然不要逐帧复刻。"
            )
        return f"{prefix}: 视频参考素材。只使用其动态规律或空间关系，不复刻画面内容。"

    @classmethod
    def _shot_video_element_ref_instruction(cls, ref, index: int) -> str:
        metadata = getattr(ref, "metadata", {}) or {}
        role_name = str(metadata.get("role_name") or cls._shot_video_ref_label(ref)).strip() or "角色主体"
        appearance_name = str(metadata.get("appearance_name") or metadata.get("name") or "").strip()
        label = f"{role_name}/{appearance_name}" if appearance_name and appearance_name != role_name else role_name
        return (
            f"<<<element_{index}>>> = {label} 的可灵主体库主体。必须把它作为当前镜头内同一人物，"
            "锁定脸型、五官、发型、体型比例、服装、配饰、年龄感和身份气质；"
            "动作、站位、表情和口型仍以当前 shot 描述、故事板和对白约束为准。"
        )

    def _shot_video_kling_reference_material_prompt(
        self,
        refs: list,
        *,
        provider=None,
    ) -> str:
        groups = self._shot_video_prompt_ref_groups(refs, provider=provider)
        image_refs = groups["image"]
        element_refs = groups.get("element", [])
        parts = [
            "Kling Omni 素材引用规则: prompt 中必须用 <<<image_1>>>、<<<image_2>>>、<<<element_1>>> 等占位符引用实际传入素材；占位符编号与 payload 中 image_list/element_list 的顺序一致。"
        ]
        for index, ref in enumerate(image_refs, start=1):
            asset_type = self._shot_video_ref_asset_type(ref)
            if asset_type == "storyboard_panel":
                parts.append(
                    f"<<<image_{index}>>> = 当前 shot 故事板单格。只锁定构图、景别、机位、人物站位、动作方向和镜头运动；不要继承黑白线稿、编号、边框或文字。"
                )
            elif asset_type == "key_vision":
                parts.append(
                    f"<<<image_{index}>>> = 主视觉原图。只锁定世界观、写实质感、色调、光影、材质和整体制作水准；不要覆盖当前 shot 的构图、动作、出场角色或剧情节奏。"
                )
            else:
                parts.append(
                    f"<<<image_{index}>>> = {self._shot_video_static_anchor_label(asset_type)}。只按素材来源提供静态外观或空间参考，不作为首帧或尾帧。"
                )
        for index, ref in enumerate(element_refs, start=1):
            parts.append(self._shot_video_element_ref_instruction(ref, index))
        if image_refs and element_refs:
            parts.append(
                "主体生成模式: 让主体 <<<element_1>>> 按照 <<<image_1>>> 的构图、景别、机位、动作方向和镜头运动完成当前 shot；"
                "整体世界观、色调、光影、材质和写实制作质感参考 <<<image_2>>>。"
            )
        elif element_refs:
            parts.append("主体生成模式: 当前 shot 的人物身份以 element_list 中的可灵主体为准。")
        if not element_refs:
            parts.append("本次没有可用可灵主体 element；如果 provider 要求主体生成模式，应先运行 role_subject_element_generation。")
        parts.append(
            "素材职责边界: 当前 shot 的 video_prompt 是剧情、动作和对白内容的唯一来源；参考素材不能新增剧情、改台词或改角色关系。"
        )
        return "\n".join(parts)

    def _shot_video_reference_material_prompt(
        self,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        *,
        provider=None,
        project_dir: Path | None = None,
    ) -> str:
        if project_dir is None:
            return (
                "参考素材使用方式: 按实际传入的视频模型素材，以模态内编号理解为图片1、图片2、视频1等；"
                "图片只管静态外观或空间，视频只管动态/空间/连续性，不互相覆盖职责。"
            )
        try:
            refs = self._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
        except Exception:
            refs = []
        groups = self._shot_video_prompt_ref_groups(refs, provider=provider)
        if bool(getattr(provider, "supports_kling_omni_placeholders", False)):
            return self._shot_video_kling_reference_material_prompt(refs, provider=provider)
        image_refs = groups["image"]
        audio_refs = groups["audio"]
        video_refs = groups["video"]

        slot_parts: list[str] = []
        if image_refs:
            slot_parts.append("图片1/图片2")
        if video_refs:
            slot_parts.append("视频1/视频2")
        if audio_refs:
            slot_parts.append("音频1/音频2")
        slot_text = "、".join(slot_parts) if slot_parts else "无参考素材"
        parts = [
            f"参考素材编号与职责（按实际传入视频模型的模态内顺序编号：{slot_text}；不同模态编号互不共享）:"
        ]
        static_anchor_prompt = self._shot_video_static_anchor_prompt(image_refs)
        if static_anchor_prompt:
            parts.append(static_anchor_prompt)
        if image_refs:
            parts.append(
                "图片参考: "
                + "；".join(
                    self._shot_video_image_ref_instruction(ref, index)
                    for index, ref in enumerate(image_refs, start=1)
                )
            )
        if video_refs:
            parts.append(
                "视频参考: "
                + "；".join(
                    self._shot_video_video_ref_instruction(ref, index)
                    for index, ref in enumerate(video_refs, start=1)
                )
            )
        if audio_refs:
            parts.append(
                "音频参考: "
                + "；".join(
                    self._shot_video_audio_ref_instruction(ref, index)
                    for index, ref in enumerate(audio_refs, start=1)
                )
            )
        if not (image_refs or video_refs or audio_refs):
            parts.append("本次没有可用参考素材；完全依据当前 shot 主体描述生成。")
        asset_types = {
            self._shot_video_ref_asset_type(ref)
            for ref in [*image_refs, *video_refs, *audio_refs]
        }
        visual_ref_names = []
        seen_visual_ref_names: set[str] = set()
        for ref in image_refs:
            if self._shot_video_ref_asset_type(ref) != "roleboard":
                continue
            name = self._shot_video_ref_label(ref)
            if name and name not in seen_visual_ref_names:
                seen_visual_ref_names.add(name)
                visual_ref_names.append(name)
        audio_ref_names = []
        seen_audio_ref_names: set[str] = set()
        for ref in audio_refs:
            if self._shot_video_ref_asset_type(ref) not in {"role_audio", "shot_dialogue_audio"}:
                continue
            name = self._shot_video_ref_label(ref)
            if name and name not in seen_audio_ref_names:
                seen_audio_ref_names.add(name)
                audio_ref_names.append(name)
        if visual_ref_names:
            parts.append(
                "画面中央人物绑定: "
                + "、".join(visual_ref_names)
                + " 是当前 shot 的视觉主体；对应的角色身份板必须锁定同一位画面中央人物，"
                "不要用说话人音频反向改变画面主体身份。"
            )
        if audio_ref_names:
            parts.append(
                "说话人音频绑定: "
                + "、".join(audio_ref_names)
                + " 的音频只绑定当前 dialogue/video_prompt 中的说话人；如果说话人与画面中央人物不同，"
                "画面中央人物不能替他说话、不能对口型。"
            )
        boundary_clauses: list[str] = []
        if "layout" in asset_types:
            boundary_clauses.append("场景图只锁定无人物空场景的空间结构、材质、光照和尺度")
        if "storyboard_panel" in asset_types:
            boundary_clauses.append("故事板单格只锁定当前 shot 的构图、景别、机位、动作方向、镜头运动和镜头顺序，不锁定线稿画风")
        if "key_vision" in asset_types:
            boundary_clauses.append("主视觉原图只锁定世界观、美术风格、色调、光影和质感")
        if "roleboard" in asset_types:
            boundary_clauses.append("角色身份板只锁定画面中央人物的静态外观、表情和动作习惯")
        if "prop" in asset_types:
            boundary_clauses.append("道具设计图只锁定道具造型与材质")
        if asset_types.intersection({"reference_video", "reference_and_previous_shot_video"}):
            boundary_clauses.append("同场景镜头只锁定空间连续性和环境动态")
        if asset_types.intersection({"previous_shot_video", "reference_and_previous_shot_video"}):
            boundary_clauses.append("上一 shot 镜头只锁定硬切后的逻辑连贯性")
        if asset_types.intersection({"role_audio", "shot_dialogue_audio"}):
            boundary_clauses.append("音频只锁定说话声音、口型节奏和对白情绪")
        if boundary_clauses:
            parts.append("素材职责边界: " + "；".join(boundary_clauses) + "。")
        if asset_types.intersection({"reference_video", "previous_shot_video", "reference_and_previous_shot_video"}):
            parts.append(
                "同场景镜头视频和上一 shot 镜头视频通常是同一个素材；只有转场、回忆、极大角度切镜或跨空间切换时才会不同。"
                "若二者不同，同场景镜头优先解决空间一致性，上一 shot 镜头只解决硬切后的动作、情绪和叙事连续。"
            )
        conflict_parts = [
            "当前 shot 的 video_prompt 是剧情、动作和对白内容的唯一来源",
            "参考素材不能新增剧情、改台词、改角色关系",
        ]
        if "storyboard_panel" in asset_types:
            conflict_parts.append("当前 shot 构图、景别、机位、动作方向和镜头顺序冲突优先听故事板单格")
        if "key_vision" in asset_types:
            conflict_parts.append("主视觉只解决风格和世界观，不改变当前 shot 的构图和动作")
        if asset_types.intersection({"layout", "reference_video", "reference_and_previous_shot_video"}):
            conflict_parts.append("空间冲突优先听场景图或同场景镜头视频")
        if "roleboard" in asset_types:
            conflict_parts.append("画面中央人物外观冲突优先听角色身份板")
        if asset_types.intersection({"role_audio", "shot_dialogue_audio"}):
            conflict_parts.append("声音冲突优先听音频")
        parts.append("冲突处理: " + "；".join(conflict_parts) + "。")
        return "\n".join(parts)

    def _shot_video_dialogue_spatial_prompt(self, state: ProjectState, shot: StoryboardShot) -> str:
        if not shot.dialogue:
            return ""

        visual_role_names = [
            state.roles[role_id].name
            for role_id in self._shot_intro_role_ids(state, shot)
            if role_id in state.roles
        ]
        visual_subject = "、".join(visual_role_names) or "画面内角色"
        parts: list[str] = []
        for dialogue_line in shot.dialogue:
            role, dialogue_text, speaker_name = self._role_for_dialogue_line(state, shot, dialogue_line)
            speaker = role.name if role is not None else (speaker_name or "说话者")
            if self._dialogue_speaker_is_voiceover(dialogue_line):
                parts.append(
                    f"{speaker}的台词“{dialogue_text}”是画外音/VO，声音来自画面外或非实体旁白；"
                    f"不要让{visual_subject}张嘴、对口型或用{speaker}的声音说这句台词。"
                    f"{visual_subject}只能保持当前动作、表情变化或对画外声音做反应。"
                )
            else:
                parts.append(
                    f"{speaker}的台词“{dialogue_text}”必须由{speaker}本人说出；"
                    "其他画面内角色不能替他说话、不能对这句台词做口型，只能做听者反应。"
                )
        return "对白空间约束: " + " ".join(parts)

    def _shot_video_prompt(
        self,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        provider=None,
        project_dir: Path | None = None,
    ) -> str:
        body = self._ensure_dialogue_in_video_prompt_body(shot.video_prompt.strip(), shot.dialogue)
        reference_mode = self._video_reference_mode(provider)
        preroll_seconds = self._previous_video_preroll_seconds(provider)
        use_previous_video_preroll = (
            preroll_seconds > 0
            and float(shot.duration_seconds) > preroll_seconds
            and shot.start_frame_source != "previous_shot_last_frame"
            and project_dir is not None
            and self._shot_has_previous_video_reference(project_dir, state, episode, shot, provider=provider)
        )
        parts: list[str] = [self._cg_character_safety_prompt()]
        parts.append(
            self._shot_video_reference_material_prompt(
                state,
                episode,
                shot,
                provider=provider,
                project_dir=project_dir,
            )
        )
        parts.append(self._no_flat_front_facing_video_prompt())
        if shot.start_frame_source == "previous_shot_last_frame":
            if self._video_reference_mode_uses_frame_images(reference_mode):
                lead = (
                    "本片段按硬切进入当前画面；若参考素材中存在上一 shot 尾帧/first_frame，"
                    "只从该状态继续当前片段动作，不把它理解成软转场或声音延续。"
                )
            else:
                lead = (
                    "本片段按硬切进入当前画面；当前参考模式不传上一 shot 尾帧图片。"
                    "若参考素材中存在上一 shot 镜头视频，只承接动作因果、情绪余韵和叙事连续。"
                )
            if shot.start_frame_inheritance_reason:
                lead = f"{lead} 延续原因是{shot.start_frame_inheritance_reason.rstrip('。')}。"
            for prefix in ("首帧承接上一段视频尾帧，", "首帧为上一段视频尾帧，", "首帧为图片1，"):
                if body.startswith(prefix):
                    body = body.removeprefix(prefix).lstrip()
                    break
            parts.append(lead)
        elif use_previous_video_preroll:
            for prefix in (
                "首帧为参考图，",
                "首帧为本片段参考图，",
                "首帧为图片1，",
                "首帧为锚点参考图，",
            ):
                if body.startswith(prefix):
                    body = body.removeprefix(prefix).lstrip()
                    break
            parts.append(self._previous_video_preroll_prompt(shot, preroll_seconds))
        else:
            for prefix in (
                "首帧为参考图，",
                "首帧为本片段参考图，",
                "首帧为图片1，",
                "首帧为锚点参考图，",
            ):
                if body.startswith(prefix):
                    body = body.removeprefix(prefix).lstrip()
                    break
            parts.append(
                "本片段按硬切进入当前画面。不要把任何参考素材当作本片段首帧或尾帧，不要逐帧复刻参考素材；"
                "参考素材只按上方编号职责提供外观、空间、动态或连续性约束。"
            )
        if dialogue_spatial_prompt := self._shot_video_dialogue_spatial_prompt(state, shot):
            parts.append(dialogue_spatial_prompt)
        parts.append("当前 shot 主体视频描述:")
        parts.append(body)
        parts.append(
            "片段首尾只允许硬切；任何 J-Cut 或 L-Cut 只能发生在本片段内部中段，"
            "不要让声音提前进入本片段之前，也不要让声音拖尾到下一片段。"
        )
        parts.append(
            "如果背景中存在人群或群众，不要让他们静止不动；让他们进行符合场景逻辑、"
            "情绪氛围和空间关系的自然移动、避让、聚散或反应，但不要抢占主体动作。"
        )
        parts.append("全片不要出现任何字幕、标志、logo、水印、文字标识、片段编号、可读文字或无关商标。")
        return "\n".join(item for item in parts if item)

    async def _run_generation_node_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        node_name: str,
        episode_key: str,
    ) -> BaseModel:
        node_by_name = {node.name: node for node in self.generation_episode_nodes}
        return await node_by_name[node_name].run(project_dir, state, episode_key)

    @staticmethod
    def _empty_run_outputs(target_nodes: list[str]) -> dict[str, BaseModel]:
        outputs: dict[str, BaseModel] = {}
        if "shot_dialogue_audio_generation" in target_nodes:
            outputs["shot_dialogue_audio_generation"] = ShotDialogueAudioGenerationOutput(
                generated_dialogue_audios=[],
                skipped_dialogue_lines=[],
            )
        if "shot_video_generation" in target_nodes:
            outputs["shot_video_generation"] = ShotVideoGenerationOutput(generated_videos=[])
        if "dynamic_asset_solidification" in target_nodes:
            outputs["dynamic_asset_solidification"] = DynamicAssetSolidificationOutput(solidified_assets=[])
        return outputs

    @staticmethod
    def _merge_run_output(run_outputs: dict[str, BaseModel], node_name: str, output: BaseModel) -> None:
        current = run_outputs[node_name]
        if node_name == "shot_dialogue_audio_generation":
            if not isinstance(current, ShotDialogueAudioGenerationOutput) or not isinstance(
                output,
                ShotDialogueAudioGenerationOutput,
            ):
                raise TypeError("shot_dialogue_audio_generation output type mismatch")
            current.generated_dialogue_audios.extend(output.generated_dialogue_audios)
            current.skipped_dialogue_lines.extend(output.skipped_dialogue_lines)
        elif node_name == "shot_video_generation":
            if not isinstance(current, ShotVideoGenerationOutput) or not isinstance(output, ShotVideoGenerationOutput):
                raise TypeError("shot_video_generation output type mismatch")
            current.generated_videos.extend(output.generated_videos)
        elif node_name == "dynamic_asset_solidification":
            if not isinstance(current, DynamicAssetSolidificationOutput) or not isinstance(
                output, DynamicAssetSolidificationOutput
            ):
                raise TypeError("dynamic_asset_solidification output type mismatch")
            current.solidified_assets.extend(output.solidified_assets)
        else:
            raise ValueError(f"Unsupported generation node output merge: {node_name}")

    def _save_run_outputs(self, project_dir: Path, run_outputs: dict[str, BaseModel]) -> None:
        for node_name, output in run_outputs.items():
            self.repo.save_node_output(project_dir, node_name, output)

    def _shot_generation_saved_path(
        self,
        project_dir: Path,
        *,
        node_name: str,
        output: BaseModel,
        episode_key: str,
        shot: StoryboardShot,
    ) -> str:
        if node_name == "shot_dialogue_audio_generation" and isinstance(output, ShotDialogueAudioGenerationOutput):
            for item in output.generated_dialogue_audios:
                if item.episode_key == episode_key and item.shot_id == shot.shot_id and item.asset.asset_path:
                    return item.asset.asset_path
        if node_name == "shot_video_generation" and isinstance(output, ShotVideoGenerationOutput):
            for item in output.generated_videos:
                if item.episode_key == episode_key and item.shot_id == shot.shot_id and item.asset_path:
                    return item.asset_path
        if node_name == "dynamic_asset_solidification":
            return self._project_relative(project_dir, self.dynamic_assets.index_path(project_dir))
        return self._project_relative(project_dir, self.layout.node_output_path(project_dir, node_name))

    @staticmethod
    def _log_shot_generation_started(logger, episode_key: str, shot: StoryboardShot, node_name: str) -> None:
        logger.info(
            "%s shot %d %s started",
            episode_key,
            shot.index,
            node_name,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    @staticmethod
    def _log_shot_generation_finished(
        logger,
        episode_key: str,
        shot: StoryboardShot,
        node_name: str,
        saved_path: str,
    ) -> None:
        logger.info(
            "%s shot %d %s finished successfully, saved in %s",
            episode_key,
            shot.index,
            node_name,
            saved_path,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    @staticmethod
    def _log_shot_generation_failed(
        logger,
        episode_key: str,
        shot: StoryboardShot,
        node_name: str,
        exc: Exception,
    ) -> None:
        logger.error(
            "%s shot %d %s failed, %s",
            episode_key,
            shot.index,
            node_name,
            exc,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    def _delete_project_file_if_exists(self, project_dir: Path, path_value: str | None) -> None:
        if not path_value:
            return
        path = Path(path_value)
        if not path.is_absolute():
            path = project_dir / path
        resolved_project_dir = project_dir.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_project_dir)
        except ValueError:
            get_logger().warning("skip deleting dynamic asset outside project: %s", resolved_path)
            return
        if resolved_path.exists() and resolved_path.is_file():
            resolved_path.unlink()

    def _clear_shot_dynamic_asset_fields(self, project_dir: Path, shot: StoryboardShot) -> None:
        for audio in shot.dialogue_audio_assets:
            self._delete_project_file_if_exists(project_dir, audio.asset_path)
        for bgm in shot.shot_bgm_assets:
            self._delete_project_file_if_exists(project_dir, bgm.asset_path)
        self._delete_project_file_if_exists(project_dir, shot.video_asset_path)
        self._delete_project_file_if_exists(project_dir, shot.video_last_frame_asset_path)

        shot.dialogue_audio_assets = []
        shot.shot_bgm_assets = []
        shot.video_asset_id = None
        shot.video_asset_path = None
        shot.video_provider = None
        shot.video_model = None
        shot.video_task_id = None
        shot.video_task_status = None
        shot.video_request_id = None
        shot.video_last_frame_asset_path = None
        shot.video_usage = {}
        shot.video_raw_response = {}
        shot.solidified_asset_ids = []

    def _clear_selected_shot_dynamic_assets(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shots: list[StoryboardShot],
    ) -> None:
        selected_shot_ids = {shot.shot_id for shot in shots}
        for shot in episode.shots:
            if shot.shot_id in selected_shot_ids:
                self._clear_shot_dynamic_asset_fields(project_dir, shot)
        self._save_storyboard_episode(project_dir, episode)

        existing_assets = self.dynamic_assets.load_index(project_dir)
        remaining_assets = [
            item
            for item in existing_assets
            if not (item.episode_key == episode.episode_key and item.shot_id in selected_shot_ids)
        ]
        if len(remaining_assets) != len(existing_assets):
            self.dynamic_assets.save_index(project_dir, remaining_assets)

        registry = load_generation_tasks(project_dir, project_id=state.project_id)
        task_keys = {
            self._shot_video_task_key(episode.episode_key, shot_id)
            for shot_id in selected_shot_ids
        }
        tasks = registry.get("tasks", [])
        if isinstance(tasks, list):
            remaining_tasks = [
                task
                for task in tasks
                if not (isinstance(task, dict) and task.get("task_key") in task_keys)
            ]
            if len(remaining_tasks) != len(tasks):
                registry["tasks"] = remaining_tasks
                save_generation_tasks(self.repo, project_dir, registry)

    @staticmethod
    def _target_generation_nodes(until: str, only: str | None) -> list[str]:
        if only:
            return [only]
        if until in DEFAULT_GENERATION_NODES:
            return DEFAULT_GENERATION_NODES[: DEFAULT_GENERATION_NODES.index(until) + 1]
        return GENERATION_NODES[: GENERATION_NODES.index(until) + 1]

    def _sort_episode_keys_in_story_order(self, state: ProjectState, episode_keys: list[str]) -> list[str]:
        return sort_episode_keys_in_story_order(state, episode_keys)

    def _active_episode_keys_in_order(self, state: ProjectState) -> list[str]:
        context = getattr(self, "_run_context", None)
        if context is not None and context.selected_episode_keys is not None:
            selected = context.selected_episode_key_set
            return [episode_key for episode_key in self._expected_episode_keys(state) if episode_key in selected]
        active_episode_keys = getattr(self, "_active_episode_keys", None)
        expected = self._expected_episode_keys(state)
        if active_episode_keys is None:
            return expected
        return [episode_key for episode_key in expected if episode_key in active_episode_keys]

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "dynamic_asset_solidification",
        force: bool = False,
        episode_keys: list[str] | None = None,
        only: str | None = None,
        shot_selectors: list[str] | None = None,
    ) -> ProjectState:
        if until not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation stop node: {until}")
        if only is not None and only not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation only node: {only}")
        target_nodes = self._target_generation_nodes(until, only)

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        self._hydrate_roles_from_design_files(project_dir, state)
        selected_episode_keys, checklist = selected_episode_keys_from_checklist(
            self.repo,
            project_dir,
            state,
            episode_keys=episode_keys,
        )
        if force and not episode_keys:
            selected_episode_keys = self._expected_episode_keys(state)

        if not selected_episode_keys:
            update_checklist_from_state(self.repo, project_dir, state, default_generate=False)
            logger.info("workflow=generation skipped project_id=%s reason=no_selected_episodes", state.project_id)
            return state

        selected_set = set(selected_episode_keys)
        known_episode_keys = {str(item.get("episode_key")) for item in checklist.get("episodes", [])}
        unknown_episode_keys = sorted(selected_set.difference(known_episode_keys))
        if unknown_episode_keys:
            raise ValueError(f"Unknown episode keys: {', '.join(unknown_episode_keys)}")
        selected_episode_keys = self._sort_episode_keys_in_story_order(state, selected_episode_keys)
        selected_set = set(selected_episode_keys)

        run_outputs = self._empty_run_outputs(target_nodes)
        logger.info(
            "workflow=generation project_id=%s until=%s only=%s force=%s episodes=%s shots=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys),
            ",".join(shot_selectors or []) or "-",
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        previous_active_shot_selectors = getattr(self, "_active_shot_selectors", None)
        previous_force_generation = getattr(self, "_force_generation", None)
        previous_run_context = getattr(self, "_run_context", None)
        self._run_context = WorkflowRunContext(
            workflow_name="generation",
            project_dir=project_dir,
            until=until,
            only=only,
            force=force,
            selected_episode_keys=selected_episode_keys,
            shot_selectors={
                str(selector).strip().lower().replace("-", "_")
                for selector in shot_selectors or []
                if str(selector).strip()
            },
        )
        self._force_generation = bool(force)
        if shot_selectors:
            self._active_shot_selectors = {
                str(selector).strip().lower().replace("-", "_")
                for selector in shot_selectors
                if str(selector).strip()
            }
        elif hasattr(self, "_active_shot_selectors"):
            delattr(self, "_active_shot_selectors")
        try:
            for episode_index, episode_key in enumerate(selected_episode_keys, start=1):
                self._active_episode_keys = {episode_key}
                logger.info(
                    "episode %d/%d %s started nodes=%s",
                    episode_index,
                    len(selected_episode_keys),
                    episode_key,
                    ",".join(target_nodes),
                )
                try:
                    for node_name in target_nodes:
                        with log_context(node_name=node_name, episode_key=episode_key):
                            output = await self._run_generation_node_for_episode(
                                project_dir,
                                state,
                                node_name,
                                episode_key,
                            )
                            self._merge_run_output(run_outputs, node_name, output)
                            if node_name == "dynamic_asset_solidification":
                                if not isinstance(output, DynamicAssetSolidificationOutput):
                                    raise TypeError("dynamic_asset_solidification output type mismatch")
                                output = cast(DynamicAssetSolidificationOutput, output)
                                self.dynamic_assets.merge_episode_assets(
                                    project_dir,
                                    episode_key,
                                    output.solidified_assets,
                                )
                            state.mark_completed(node_name)
                            self.repo.save_state(project_dir, state)
                            self._save_run_outputs(project_dir, run_outputs)
                except Exception:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        failed_episode_keys={episode_key},
                    )
                    logger.exception("episode %s failed", episode_key)
                    raise
                if target_nodes[-1] == GENERATION_NODES[-1]:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        processed_episode_keys={episode_key},
                    )
                logger.info("episode %d/%d %s completed", episode_index, len(selected_episode_keys), episode_key)
        finally:
            if previous_active_episode_keys is None:
                if hasattr(self, "_active_episode_keys"):
                    delattr(self, "_active_episode_keys")
            else:
                self._active_episode_keys = previous_active_episode_keys
            if previous_active_shot_selectors is None:
                if hasattr(self, "_active_shot_selectors"):
                    delattr(self, "_active_shot_selectors")
            else:
                self._active_shot_selectors = previous_active_shot_selectors
            if previous_force_generation is None:
                if hasattr(self, "_force_generation"):
                    delattr(self, "_force_generation")
            else:
                self._force_generation = previous_force_generation
            if previous_run_context is None:
                if hasattr(self, "_run_context"):
                    delattr(self, "_run_context")
            else:
                self._run_context = previous_run_context

        processed_episode_keys = selected_set if target_nodes[-1] == GENERATION_NODES[-1] else None
        update_checklist_from_state(
            self.repo,
            project_dir,
            state,
            default_generate=False,
            processed_episode_keys=processed_episode_keys,
        )
        get_logger().info(
            "workflow=generation completed project_id=%s current_node=%s episodes=%s",
            state.project_id,
            state.current_node,
            ",".join(selected_episode_keys),
        )
        return state
