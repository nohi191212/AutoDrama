# 《御兽仙朝》工作流交接记录

## 任务目标与最新原则

- 项目目录：`outputs/yushou_xianchao`
- 配置：`config.yushou_xianchao.yaml`
- Python：`D:\miniforge3\envs\autodrama\python.exe`
- 禁止运行 pytest；验证使用 `compileall`、CLI 定向运行、JSON 与日志检查。
- 视频只生成前 10 个 clip。

用户已明确以下质量控制原则：

1. 发现问题只能改工作流或提示词模板，不能手工改图片文件。
2. 图片节点后必须有视觉审计节点，使用 `rightcode:gpt-5.6-terra` 直接接收并审查图片。
3. 审计失败时，自动改该资产的提示词并且只重生成该资产；不得重跑整批。
4. 最终工作流只能依赖接口调用，不能依赖人工或外部 Agent。

这应沉淀为固定闭环：`生成 → 视觉审计 → 精准提示词修订 → 单资产重生成 → 复审`。

## 已完成的节点与审计结果

以下节点已成功运行：

- `script_import`
- `script_detail_expand`
- `script_novel_extract`
- `key_vision_prompt`
- `key_vision_image_generation`
- `role_extract_primary`
- `role_extract_functional`
- `role_finalize`
- `roleboard_prompt`
- `roleboard_image_generation`
- `role_subject_frontal_image_generation`
- `prop_extract`
- `prop_finalize`
- `layout_extract`
- `layout_finalize`
- `layout_prop_boundary_review`
- `prop_prompt`
- `layout_prompt`

已人工审看并通过：

- 主视觉：二维水墨国漫风格，人物、老黑与古书结构正常。
- 15 张角色板：视图完整、无可读文字和水印，人物与神兽结构正常；胡师与文蝶已分离。
- 15 张角色主体正面图：单主体、无裁切，身份一致；胡师肩头的文蝶符合剧情中的长期伴随关系。

第一轮道具图不通过：整体偏写实/3D 产品渲染，和二维水墨角色资产不统一；牛棚门栓被错误生成成技术多视图。

已经根据这个系统性问题修改模板并启动第二轮道具生成，但运行在最后一个资产 `prop_麦饼包__握紧褶皱_creased` 时被中断。日志未显示其成功完成；应按未完成处理。

## 已落地的工作流与模板调整

### 提示词卫生

已从剧本、主视觉、角色、道具、场景提示词中移除项目元数据、字段说明与 Agent 工作指南式内容。发送给模型的提示词现在应只保留完成当前内容任务需要的信息。

相关修改包括：

- `autodrama/src/autodrama/prompts/script_import/default.md`
- `autodrama/src/autodrama/prompts/script_detail_expand/default.md`
- `autodrama/src/autodrama/services/script_service.py`
- `autodrama/src/autodrama/services/director_service.py`
- `autodrama/src/autodrama/workflows/nodes/director_nodes.py`
- `autodrama/src/autodrama/prompts/key_vision_prompt/default.md`
- 角色、道具与场景的提取、定稿、提示词模板。

### 角色资产

- 角色板固定为“主视图 + 正/侧/背 + 核心近景 + 材质细节”的六视图身份板。
- 角色板禁用摄影写实硬编码，统一为二维水墨国漫。
- 禁止绘入其他具名角色或同伴，禁止可读文字、logo、水印。
- 功能角色提取排除旁白、屏外声和未具象的回忆人物。

关键文件：

- `autodrama/src/autodrama/services/role_service.py`
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/prompts/roleboard_prompt/aibox_gpt_image_2_guan.md`
- `config.yushou_xianchao.yaml`

### 道具与场景资产

- 道具中的书、试卷与印文只允许不可辨读纹样、笔迹或光痕，不得生成可读文字。
- 道具提示词强制二维水墨国漫，禁止写实摄影、3D/PBR、CAD、工程图、多视图和拼图。
- 场景从“三视图/写实摄影”改为单幅、无人、可调度的二维水墨场景母版。
- 场景尺寸从横版改为竖版 `2160x3840`。
- 场景边界规则已明确：明确门墙边界或独立表演功能的院落/教室、内室/灶房保持独立 base；作为剧情证据被操作或损坏的固定结构（如门栓）仍可作为道具。
- 后续背景规划不再强制 `layout.reference_image_kind == "three_view"`。

关键文件：

- `autodrama/src/autodrama/prompts/prop_extract/default.md`
- `autodrama/src/autodrama/prompts/prop_finalize/default.md`
- `autodrama/src/autodrama/prompts/prop_prompt/default.md`
- `autodrama/src/autodrama/prompts/layout_extract/default.md`
- `autodrama/src/autodrama/prompts/layout_finalize/default.md`
- `autodrama/src/autodrama/prompts/layout_prop_boundary_review/default.md`
- `autodrama/src/autodrama/prompts/layout_prompt/default.md`
- `autodrama/src/autodrama/prompts/layout_prompt/rightcode_gpt_image_2.md`
- `autodrama/src/autodrama/prompts/layout_prompt/toapi_gpt_image_2.md`
- `autodrama/src/autodrama/prompts/layout_prompt/volcengine_seedream.md`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `config.yushou_xianchao.yaml`

## 正在实现但尚未接入完成的能力

已经写入、但尚未经过编译验证和节点注册的代码：

- `autodrama/src/autodrama/core/schemas.py`
  - 增加 `ImageAssetAuditItem`、`ImageAssetAuditOutput`。
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
  - 增加 `_active_asset_ids` 精确资产选择。
  - `prop_image_generation`、`layout_image_generation` 支持仅生成指定资产并合并保留其他输出。
- `autodrama/src/autodrama/cli.py`
  - 新增 `pregen --assets asset_id[,asset_id]`。
- `autodrama/src/autodrama/workflows/pregen.py`
  - 新增资产选择参数与约束。
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
  - 新增 `prop_image_audit`、`layout_image_audit`。
  - 节点将图片交给 `gpt-5.6-terra` 审计；拒绝时写回修订提示词、只重生该资产、再复审，默认最多两次。
- `autodrama/src/autodrama/prompts/image_asset_audit/default.md`
  - 审计模板只描述视觉验收和修订提示词，避免 Agent 指南式表达。

## 下一位 Agent 的首要工作

1. 在 `autodrama/src/autodrama/workflows/nodes/__init__.py` 注册 `image_audit_nodes`：
   - 导入节点名与构造函数。
   - 在默认 pregen 链路加入：
     - `prop_image_generation → prop_image_audit`
     - `layout_image_generation → layout_image_audit`
   - 更新 `PREGEN_NODE_NAMES` 与 `AVAILABLE_PREGEN_NODE_NAMES`。
2. 在 `config.yushou_xianchao.yaml` 新增：

   ```yaml
   prop_image_audit:
     model: rightcode:gpt-5.6-terra
     params:
       temperature: 0.1
       reasoning_effort: high
       max_attempts: 2
   layout_image_audit:
     model: rightcode:gpt-5.6-terra
     params:
       temperature: 0.1
       reasoning_effort: high
       max_attempts: 2
   ```

3. 验证新代码：

   ```powershell
   D:\miniforge3\envs\autodrama\python.exe -m compileall -q autodrama/src/autodrama
   git diff --check
   ```

4. 只有验证通过后，重跑第二轮道具初始批次并执行审计：

   ```powershell
   D:\miniforge3\envs\autodrama\python.exe -m autodrama.cli run pregen --config config.yushou_xianchao.yaml --project yushou_xianchao --only prop_image_generation --force
   D:\miniforge3\envs\autodrama\python.exe -m autodrama.cli run pregen --config config.yushou_xianchao.yaml --project yushou_xianchao --only prop_image_audit
   ```

5. 审计发现单张问题后，工作流或 CLI 应使用精准资产选择，例如：

   ```powershell
   D:\miniforge3\envs\autodrama\python.exe -m autodrama.cli run pregen --config config.yushou_xianchao.yaml --project yushou_xianchao --only prop_image_generation --assets prop_牛棚门栓__base --force
   ```

## 当前实现的已知限制，必须先修复再扩大使用范围

- `roleboard_image_generation` 目前不能直接用于单资产重跑：它依赖锚点角色板，非锚点资产必须复用既有锚点而不是重新生成整批角色板。
- `role_subject_frontal_image_generation` 的单资产重跑需要加载并合并既有输出，否则会丢失未选中的输出项。
- `image_audit_nodes.py` 当前只实现道具与场景审计。还需以同样模式补齐：
  - `key_vision_image_audit`
  - `roleboard_image_audit`
  - `role_subject_frontal_image_audit`
  - `shot_background_image_audit`
  - `shot_keyframe_image_audit`
- 需要为视频实现对应的自动审计与只重生成失败镜头能力；生成时严格用 clip 选择器限制在前 10 个 clip。

## 后续预期节点顺序

`layout_image_generation → 场景审计 → role_kling_voice_generation → role_subject_element_generation → clip_segment → clip_to_shots → layout_to_background_prompt → shot_background_image_generation → 背景审计 → shot_keyframe_prompt → shot_keyframe_image_generation → 关键帧审计 → shot_manifest_generation → 前 10 个 clip 的音频与视频生成 → 视频审计`。

## 临时文件与清理

本任务此前创建的审计临时脚本为：`.tmp/audit_roleboards.py`。它目前改为输出道具 contact sheet；任务结束时应删除脚本及本任务创建的 contact sheets，不要删除用户既有的 `.tmp` 内容。
