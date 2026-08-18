# 第 03 至 05 章事实、AI 与渲染链路设计

## 目标

将第 03、04、05 章与已优化的第 01、02 章对齐：最终报告的每个章节明确区分确定性事实、AI 二次分析和 Presentation 展示投影。当前 schema v5 的 HTML 仅从对应 Presentation 章节读取数据，不直接读取合并报告中的同类字段。

本次不重做章节样式，不改变 AI JSON schema，不修改 RPA CSV、标准化 records 或高光帧抽取规则。

## 统一契约

```text
事实包 diagnostic-analysis-data.json
  + AI 输出 diagnostic-ai-analysis.json
  -> final-report-data.json
  -> presentation-report.json
  -> final-report.html
```

第 01、02 章已经以 Presentation 章节作为当前报告的展示边界。本次为 03 至 05 章补齐同一边界：展示组件只读取 `presentation.chapters.chapter0N`；每个投影字段携带或可追溯到 `source`、`evidenceRefs`、`availability`。

旧 schema 的兼容回退仅在没有 `presentationSchemaVersion: 1` 时保留，不能成为当前 v5 报告的读取路径。

## 第 03 章：脚本洞察

| 层级 | 输入/输出 | 责任 |
|---|---|---|
| 事实包 | `scripts.labelSummary`、`scripts.materials` | 一级/二级标签、出现次数、素材覆盖和证据句 |
| AI | `batchInsights[topic=script]` | 两条组合洞察：一二级内容公式、脚本文案；仅引用 `script:*` |
| Presentation | `chapter03.groups`、`chapter03.insights` | 图表消费标签事实，洞察卡消费 AI 原始标题与正文 |
| HTML | `chapter03Summary()`、`chapter03Insights()` | 只消费 `chapter03` 投影 |

`chapter03.source` 更新为 `facts.scripts + ai.batchInsights[script]`。标签组保留 `facts.scripts.labelSummary` 来源；每条 AI 洞察保留自身 `script:*` 证据。AI 标题不再由投影层改写。

## 第 04 章：高光画面洞察

| 层级 | 输入/输出 | 责任 |
|---|---|---|
| 事实包 | `materials[*].highlightFrames`（来自 `highlights.materials[*].frames`） | JPEG 路径、帧类型、秒点、点击峰值和 `highlight:*` |
| AI | `highlightDashboard.groups` | 固定分类、共同画面结论、裂变方向；每帧恰好归入一类 |
| Presentation | `chapter04.groups`、`chapter04.conclusion` | 按分类把 AI 结论与事实帧卡合并；帧卡保存事实来源 |
| HTML | `chapter04Groups()` 与章节投影的 `conclusion` | 只读取 `chapter04`，不直接读取 `reportData.conclusion.highlightDashboard` |

`chapter04.source` 应声明 `facts.materials.highlightFrames + ai.highlightDashboard`。分类组同时保留帧事实与 AI 输出来源，`derivativeDirections` 的证据必须属于所属分类的帧集合。章节组合结论使用已投影的 `chapter04.conclusion`，避免绕过 Presentation。

## 第 05 章：单素材深度分析

| 层级 | 输入/输出 | 责任 |
|---|---|---|
| 事实包 | `materials`、`delivery`、`scripts.materials`、`interaction`、高光帧 | 单素材身份、确定性互动/脚本/高光帧事实 |
| AI | `materialAnalyses[*]` | 人群、脚本、高光观察、综合判断、决策和下一步；引用合法证据 |
| Presentation | `chapter05.materials[*]` | 每个素材明确投影事实区和 `analysis` AI 区，保留分区来源与证据 |
| HTML | `chapter05Materials()` | 当前 schema 只从素材投影构造页面，不直接使用 `reportData.materials` |

章节与素材级 `source` 分别表达事实与 AI 的组合来源；五个固定 Tab 保持现状。高光帧、互动、人群和脚本 Tab 只展示确定性事实；“综合判断与下一步”只展示 AI `materialAnalyses` 结果。素材 AI 总结不重新计算事实。

## 质量门禁与测试

1. Presentation 单元测试断言三章的 `source`、关键 `evidenceRefs` 和 `availability`。
2. 模板测试断言 current Presentation 路径优先且不从 `reportData.conclusion.highlightDashboard` 或 `reportData.materials` 读取 03 至 05 章。
3. 保留既有 AI 质量门禁：脚本洞察仅 `script:*`，高光分类覆盖所有成功帧且不重复，素材 AI 分析覆盖每条成功素材/高光帧。
4. 使用真实 Top6 事实包和当前 AI JSON 重建 `presentation-report.json`、HTML 和 ZIP，检查三章的来源字段、帧/素材覆盖与归档完整性。

## 非目标

- 不调整第 03、04、05 章 CSS、布局、组件视觉层级或文案长度。
- 不新增或改变 AI JSON schema 字段。
- 不修改 RPA CSV、records、视频下载、高光帧选择或截帧。
- 不恢复第 06 章或组合级下一步策略。
