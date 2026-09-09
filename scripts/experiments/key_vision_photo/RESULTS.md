# 真人摄影主视觉模板对比

2026-09-09。最终选择 **D：叙事电影剧照＋完整摄影示例**，已替换正式 key_vision_prompt/default.md。原正式主视觉、项目 state.json、role/prop/layout 图片未修改。

## 实验边界

共完成 7 张：原模板基线 1 张，A/B 各 1 张，C/D 各 2 张。实际复用 PregenWorkflow 的 key_vision_prompt → key_vision_image_generation 节点；Gemini gemini-3.6-flash 编译，gpt-image-2-guan 生图，3840×2160，quality=high，零参考图。没有使用内置 imagegen 或另写 SDK 调用。

固定同一个现存世界观、全局视觉风格、两人夜间维修间场景与动作要求；只改变模板写法。场景要求本身也比原正式图更贴合用户，所以旧正式图不能作为严格对照；这里另外生成了同场景要求的原模板基线。

每个目录保存 compiler-input.md、image-prompt.md、manifest.json、节点输出、接口边界提示词日志和原尺寸图片。原模板的快照仍在基线测试目录内。D 两张测试所用模板与上线模板 SHA256 完全相同：08357AF88B558791B0E8FD781965E9C1477FF38465DB1DF8CD406C35593D4E4D。

这是一轮小样本人工视觉对比，不是盲评或统计显著性实验。没有固定随机种子，模板变化也通过 Gemini 改变最终文字，不能将差异归因于某一个关键词。不能承诺以后每张都达到 D 首图水平。

## 来源与改写关系

- [Google Gemini 官方生图指南](https://ai.google.dev/gemini-api/docs/image-generation)：Photorealistic scenes 提供“摄影类型＋主体＋场景＋光线＋角度＋镜头”的模板，A 据此改写。
- [Google Cloud Imagen 官方提示词指南](https://cloud.google.com/vertex-ai/generative-ai/docs/image/img-gen-prompt-guide)：Photography modifiers 列出机位、距离、照明、相机设置、镜头与胶片，B 据此改写。本轮不采用其中可能增加过锐质感的 HDR 方向。
- C/D 是根据上述摄影原则及用户视觉需求写出的叙事型模板，不是声称网上存在的原文模板。没有把源模型的建议当作 GPT Image 效果保证。
- imagegen 技能的摄影语言、固定约束与逐项迭代建议用于对比方法；用户要求优先，实际调用继续走项目节点。未增加 Python 审美/内容验收门禁。

## 为什么选 D

关键不是堆“photorealistic”，而是把光如何落在皮肤上、远景为什么失去纹理、哪些表面应保持干燥，写进直接交给生图模型的连续文字。C 复跑显示，Gemini 会将约束留在编译输入里而省略在最终提示词中；D 用一段完整示例和明确的传递要求修正这一点。

正式模板删除了 production-art finish 和七段标签，保留现有 JSON 三字段接口，prompt 字段仍为连续文字。没有把此次固定两人维修间场景写成生产规则；示例明确不可照搬场景，具体世界和内容仍从节点输入决定。生产未自动获得测试 brief，故后续默认运行不会承诺复现同一构图。

仅做了提示词渲染、JSON 接口 smoke、语法编译和文件差异检查；没有 pytest，也没有任何 Python 视觉打分。旧 smoke 中绑定美术形容词的断言已移除。

## 图片与最终提示词

### 原模板基线

夜景与人物构图有所改善，但颈部发光、长袍式衣服和悬挂管线强化了幻想美术感。额头与颧骨高光明显。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/baseline-1/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/baseline-1/manifest.json)

![原模板基线](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/baseline-1/assets/images/key_visions/key_vision_original.png)

### A：简洁摄影描述

现代服装、人物动作和紧张感较好；皮肤仍偏亮，出现明显招牌文字，路面有湿反射。最终提示词省略了部分约束。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/a_scene-1/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/a_scene-1/manifest.json)

![A：简洁摄影描述](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/a_scene-1/assets/images/key_visions/key_vision_original.png)

### B：摄影条件优先

人物可读，光线有方向性；远景依然密集窗格和重复建筑纹理，整体偏暗。镜头术语本身不能控制背景复杂度。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/b_camera-1/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/b_camera-1/manifest.json)

![B：摄影条件优先](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/b_camera-1/assets/images/key_visions/key_vision_original.png)

### C：叙事电影剧照，首次

皮肤反光较收敛，动作有被打断的瞬间感，远景较简洁；仍有粗糙脏污衣料、湿地面和零星图形。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/c_film-1/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/c_film-1/manifest.json)

![C：叙事电影剧照，首次](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/c_film-1/assets/images/key_visions/key_vision_original.png)

### C：原样复跑

画面回到油亮皮肤、湿路面和文字招牌。说明首次好图不足以证明模板可靠；最终提示词再次遗漏关键约束。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/c_film-2/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/c_film-2/manifest.json)

![C：原样复跑](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/c_film-2/assets/images/key_visions/key_vision_original.png)

### D：叙事＋完整摄影示例，首次（首选图）

七张中最接近本次目标：人物皮肤最柔和哑光，建筑以体块为主，街道干燥，前中远景可读。缺点：男方机械义肢不明显，女性站姿稍静，整体赛博朋克强度有所减弱。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/d_refined-1/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/d_refined-1/manifest.json)

![D：叙事＋完整摄影示例，首次（首选图）](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/d_refined-1/assets/images/key_visions/key_vision_original.png)

### D：原样复跑

干燥街道、简化远景和无显眼文字保持下来，机械义肢更突出；额头高光和装甲反光比首次强。两次结果支持优先采用这个方向，不代表已证实稳定性。

[最终生图提示词](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/d_refined-2/image-prompt.md) · [生成记录](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/d_refined-2/manifest.json)

![D：原样复跑](C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/.tmp/key_vision_photo/d_refined-2/assets/images/key_visions/key_vision_original.png)
