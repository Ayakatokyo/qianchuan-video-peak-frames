# 本批次分类粒度与分布图优化设计

## 1. 背景与目标

第四部分已经同时支持固定的标准分类（人像、产品、其他）和 AI 生成的“本批次分类”。当前“本批次分类”通常只有少量大类，难以呈现同一批次内不同画面机制的差异；报告中也只有分类 Tab，没有批次分类整体分布视图。

本次优化目标：

1. 调整 Chapter 04 的 AI 提示词，使本批次成功高光帧在证据充分时细分为更多互斥的内容/画面机制类别。
2. 在“本批次分类”报告级 Tab 内增加分类分布柱状图，展示每个类别覆盖的成功高光帧张数及占比。
3. 保持现有分类详情的内容结构不变，包括分类标题、描述、共性结论、裂变方向、帧分页、灯箱和素材跳转。

本次统计口径固定为“成功高光帧张数”：不统计素材数，不由 AI 生成或修改统计数字。

## 2. 范围与非目标

### 范围

- 更新 `diagnostic-analysis-prompt-v1.md` 中 Chapter 04 的 `customClassification` 规则。
- 扩展 Presentation 的 `chapter04.batchClassification` 投影，增加确定性计算的 `distribution`。
- 在 HTML 报告“本批次分类”面板中复用现有列柱图组件渲染分布。
- 增加提示词、Presentation 和前端模板契约测试。

### 非目标

- 不改变标准分类 `portrait`、`product`、`other` 的规则和页面结构。
- 不改变本批次分类各具体类别内部已有的分析文案和高光帧展示方式。
- 不让 AI 输出 `frameCount`、`percentage`、`coverage` 或其他统计数字。
- 不增加新的图表库，不增加图表点击下钻；分类 Tab 已经承担具体类别切换。
- 不把本批次分类结果扩展到其他章节。

## 3. AI 分类规则设计

### 3.1 粒度策略

提示词仍要求分类只能依据本批次成功高光帧 JPEG 中直接可观察的内容或画面机制。根据成功帧总量给出目标粒度：

- 成功高光帧不少于 12 张：优先拆分为 5～8 个类别。
- 成功高光帧为 6～11 张：优先拆分为 4～6 个类别。
- 成功高光帧少于 6 张：至少拆分为 2 个类别，不为了数量制造没有证据的差异。

“优先”表示目标范围，不改变已有数据契约对至少两个非空类别的兼容性约束。每个类别必须有明确的可观察差异；不能仅按颜色、构图远近、画质或偶然姿态拆分同一机制。

提示词可以提供机制识别方向作为启发，但不得要求每个批次都必须出现这些类别。可观察且适用时，机制方向包括：人物表达/示范、产品或包装证据、开箱礼赠、使用过程、品质或成分证明、生活场景承接、利益点视觉化、步骤或对比展示。若一个画面具备多个信号，只按最主要的说服机制归入一个类别。

### 3.2 输出约束

继续保留现有 `customClassification` 字段和每组字段。AI 必须：

- 为每个成功高光帧分配且只分配一个主导类别；无法可靠判断时放入 `unclassifiedFrameRefs`。
- 使用语义化且互不重复的 `groupName`，禁止“其他1”“其他2”等占位名称。
- 为每组输出恰好 3 条 `commonConclusion` 和 3 条 `derivativeDirections`。
- 让结论和验证方向只引用本组 `frameRefs`。
- 不输出帧数、占比、覆盖率等由 Presentation 计算的统计字段。

## 4. 数据流与接口

### 4.1 Presentation 投影

`scripts/qianchuan_presentation.py` 继续使用成功高光帧 registry 作为唯一统计事实源：

1. 从 `materials[*].highlightFrames` 收集 `extractionStatus == "succeeded"` 且有 `framePath` 的成功帧引用。
2. 对 AI `customClassification.groups[*].frameRefs` 做现有的成功帧过滤。
3. 对每个有效类别计算 `frameCount` 和 `percentage`。
4. 新增 `batchClassification.distribution`，其顺序与分类 Tab 一致，元素结构为：

```json
{
  "groupId": "usage-scene",
  "groupName": "日常使用场景",
  "frameCount": 16,
  "percentage": 17.98
}
```

5. 当 `unclassifiedFrameRefs` 中存在有效成功帧引用时，追加：

```json
{
  "groupId": "unclassified",
  "groupName": "未归类",
  "frameCount": 2,
  "percentage": 2.25
}
```

`distribution` 不携带帧路径或帧引用，避免复制 Chapter 05 的帧事实；具体帧仍从既有 `factsByReference` 读取。

### 4.2 兼容与校验

- Presentation 始终为新的 `batchClassification` 生成 `distribution`，因此新报告使用显式数据契约。
- 前端在读取旧版或仅有 `reportData.conclusion.customClassification` 的兼容路径时，可从现有 `groups[*].frameRefs` 临时构造同等分布，确保旧报告不因缺少 `distribution` 失效。
- `validate_presentation_report()` 校验 `distribution` 的元素类型、唯一 `groupId`，以及每个类别的 `frameCount`/`percentage` 与投影后的组数据一致。
- 校验不提高现有 AI payload 的硬性最小组数，避免历史结论和测试夹具失效；目标粒度由提示词驱动，统计和完整覆盖仍由现有质量门禁保证。

## 5. 前端展示设计

### 5.1 页面顺序

`renderBatchClassificationPanel()` 的内容顺序固定为：

1. 规则说明文字；
2. “本批次分类分布”柱状图；
3. 本批次分类内部 Tab；
4. 当前类别的既有详情内容。

这保持此前确认的规则说明位置：说明文字位于标准分类/本批次分类外层 Tab 与具体分类 Tab 之间。

### 5.2 柱状图行为

复用 `renderColumnChart()`，不引入新依赖：

- 标题：`本批次分类分布`。
- 横轴：分类名称。
- 纵轴：`帧数`。
- 柱高：`frameCount`。
- Tooltip 和原生 `title`：同时展示帧数和占比。
- 分类顺序：按 AI 分组顺序，保证图表与内部 Tab 一致；不在前端重新排序，避免用户在图表和 Tab 间失去对应关系。
- 有未归类帧时显示“未归类”，无未归类帧时不显示该项。
- 无有效分布数据时复用现有“暂无可视化数据”空状态。

图表为信息展示，不增加点击下钻。键盘焦点、文本标签和现有响应式密度规则继续由 `renderColumnChart()` 提供。

## 6. 错误处理与边界情况

- 没有 `batchClassification`：保持“本批次未生成分类”提示。
- 有批次分类但没有有效组：保持现有空分类提示，不渲染图表。
- 分组引用只覆盖部分成功帧：图表展示已投影的有效分类和有效未归类项；`availability` 继续由 Presentation 标记为 `partial`。
- 某个 AI 分组的引用在成功帧 registry 中全部无效：不让该分组产生虚假帧数；保持既有投影行为，并由可用性字段反映异常。
- 旧数据没有 `distribution`：前端兼容构造分布，只使用已有合法帧引用，不修改报告数据。
- 只有一个类别或零帧：不额外补造类别；沿用现有数据质量提示。

## 7. 测试设计

### Prompt 契约

- 断言提示词包含按成功帧数量区间细分的 5～8、4～6、至少 2 组策略。
- 断言提示词要求只依据 JPEG 可观察机制、主导机制单归类，并禁止 AI 输出统计数字。

### Presentation

- 两组及以上的 fixture 断言生成 `distribution`，并检查每项 `groupId`、`groupName`、`frameCount` 和百分比。
- 增加未归类帧 fixture，断言“未归类”项的计数和占比。
- 断言分布统计不包含帧路径或帧引用。
- 断言非法或不一致的分布数据被拒绝（如重复 `groupId`、计数不匹配）。

### 前端模板

- 断言批次规则说明之后、内部分类 Tab 之前存在“本批次分类分布”渲染调用。
- 断言使用 `frameCount` 作为柱状图数值、使用百分比作为 Tooltip 信息。
- 断言旧版无 `distribution` 时存在兼容构造逻辑。
- 保留现有双层 Tab、状态隔离、分类详情和规则说明位置测试。

## 8. 变更文件

- 修改：`prompts/diagnostic-analysis-prompt-v1.md`
- 修改：`scripts/qianchuan_presentation.py`
- 修改：`templates/final-report-template.html`
- 修改：`tests/test_qianchuan_presentation.py`
- 修改：`tests/test_final_report.py`
- 如现有样式不足以容纳图表，仅修改：`assets/report/unified-report.css`

不修改历史运行目录和原始报告目录；完成后仅在当前 worktree 生成代码变更，实际报告重渲染另行执行。
