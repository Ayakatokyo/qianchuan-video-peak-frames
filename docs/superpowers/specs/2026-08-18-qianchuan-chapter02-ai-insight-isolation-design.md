# 第 02 章人群洞察 AI 职责隔离设计

## 目标

第 02 章保留当前 AI 输出结构：`batchInsights` 中 `topic=audience` 的三条洞察。每条仍由 AI 独立生成 `title`、`conclusion` 和 `evidenceRefs`，不固定标题文本。

本次将第 02 章从第 01 章的人群组合结论中隔离。两章可以读取同一份 `audience` 事实，但 AI 提示词职责、质量校验和 Presentation 展示入口必须各自归属，且第 02 章不得经由 `chapter01` 读取任何洞察。

## 展示目标

第 02 章顶部展示三条 AI 人群洞察，固定覆盖以下三个分析维度，顺序不变：

1. 展现次数（`SHOW_COUNT`）
2. 点击次数（`CLICK_COUNT`）
3. 成交金额（`TRANSACTION_AMOUNT`）

AI 为每个维度生成描述该维度业务重点的标题和结论；标题不是固定文案。下方人群分布、年龄、性别、地域图表和偏好素材继续直接消费 `audience.metrics`、`audience.preferences` 与 `audience.preferencesByMetric`。

## AI 职责与输入边界

事实包不新增字段，继续以 `diagnostic-analysis-data.json.audience` 为唯一人群结构化事实源。

`portfolioDimensionConclusions.audience` 仅服务第 01 章。提示词只要求其形成 60-120 字的组合层摘要：概括跨口径共同信号、组合层判断和一个优先验证方向。它不得复述第 02 章三条洞察的标题、完整判断链或具体行动方案。

`batchInsights[topic=audience]` 仅服务第 02 章。提示词要求其恰好三条，分别对应一个指标口径；每条标题和结论由 AI 根据该指标下的八大人群、年龄、性别、地域事实生成。单条只能引用本指标的 `audience:{metric}:*` 证据，不能复用第 01 章人群组合结论的措辞、判断或建议。三种口径不能混写，展示文案只能使用“展现次数”“点击次数”“成交金额”。

两类字段仍在一次 AI 调用中生成。因此本设计提供字段、提示词职责、质量门禁和展示链路隔离，不承诺模型上下文的物理隔离；物理隔离需要额外 AI 调用，不在本次范围内。

## 展示投影

`qianchuan_presentation.py::_chapter02()` 新增 `insights`，其值仅为当前 AI 结论中的 `batchInsights[topic=audience]`。第 02 章的 `source` 同时标明事实和 AI 来源。

`_chapter01()` 删除仅为其他章节中转而存在的 `batchInsights` 投影。第 01 章只保留自己的 `overallConclusion` 与五条 `points`。

HTML 新增第 02 章专属读取函数，优先读取 `presentation.chapters.chapter02.insights`。仅当旧 Presentation 数据不存在时，才直接回退 `final-report-data.json` 自身的旧 `batchInsights.audience`；不得回退至 `chapter01`。

```text
facts.audience
  -> AI batchInsights[topic=audience]
  -> presentation.chapter02.insights
  -> 02 顶部 AI 洞察

facts.audience
  -> AI portfolioDimensionConclusions.audience
  -> presentation.chapter01.points[audience]
  -> 01 组合结论
```

## 校验与兼容

现有 schema v5 和 `batchInsights` 输出结构不变。质量门禁继续验证第 02 章存在至少三条 audience 洞察、标题和结论非空、证据合法、结论覆盖三种指标口径，并拒绝“触达”“响应”等非标准术语。

新增字段范围校验：每条 audience 洞察的 `evidenceRefs` 必须全部属于同一个 `audience:{metric}:*` 口径；三条洞察的证据口径集合必须恰好覆盖 `SHOW_COUNT`、`CLICK_COUNT`、`TRANSACTION_AMOUNT`。第 01 章人群组合结论继续独立验证其证据合法性与长度，且不作为第 02 章的展示数据源。

旧 v5 成品报告不重写 AI JSON；其第 02 章可通过自身旧 `batchInsights.audience` 直接渲染。新生成报告必须写出 `presentation.chapter02.insights`，并且 `chapter01` 不再出现 `batchInsights`。

## 测试与验收

- Presentation 单测：`chapter02.insights` 只投影 audience 洞察；`chapter01` 不包含 `batchInsights`；三条洞察保留 AI 标题与证据。
- HTML 单测：第 02 章读取 `chapter02.insights`，源码和渲染行为均不依赖 `chapter01Insights("audience")`。
- AI 质量单测：验证 audience 洞察按三个口径覆盖，证据不跨口径，且第 01 章人群组合结论不能替代第 02 章洞察。
- 回归：全量测试、字节码编译、`git diff --check`，并使用真实 Top6 批次重新生成 AI JSON、Presentation JSON、HTML 和 ZIP；验收第 01 与第 02 章分别显示各自的 AI 内容。

## 范围外

不改 RPA CSV、标准化 records、高光帧、`audience` 聚合字段、03-05 章 AI 输出或页面结构；不新增第二次 AI 调用，不升级 AI schema。
