# 千川展示事实与 AI 事实分离设计

## 目标

将前端图表、TOP3 偏好下钻和跨章节固定展示数据从 AI 分析事实包中分离，建立独立的 `report-display-facts.json`。前端只消费展示事实，AI 只消费章节分析事实；两者必须由同一批标准化事实生成，并保留可验证的批次一致性。

## 背景与问题

当前 `analysis-units/` 面向 AI 的章节输入包含部分仅用于页面展示的数据：`chapter02-audience-input.json` 含 `preferencesByMetric`，`chapter05-material-input.json` 又将其转换为素材级 `audiencePreferences`。前端图表则从 `final-report-data.json`/Presentation 投影读取同一批数据，导致展示事实与 AI 上下文边界不清晰。

## 设计

### 1. 两类事实快照

由标准化诊断事实一次性生成两类快照：

```text
normalized diagnostic facts
├── report-display-facts.json
│   ├── 第01章素材榜单与汇总
│   ├── 第02章图表与 TOP3 下钻
│   ├── 第03章脚本标签与汇总
│   ├── 第04章高光帧展示
│   └── 第05章素材固定详情
└── analysis-units/
    ├── chapter01-portfolio-input.json
    ├── chapter02-audience-input.json
    ├── chapter03-script-input.json
    ├── chapter04-highlight-input.json
    └── chapter05-material-input.json
```

`report-display-facts.json` 是确定性、不可由 AI 改写的展示数据；`analysis-units/` 是模型上下文，按章节裁剪。

### 2. 第02章边界

`audience.metrics` 是图表和 AI 共同需要的事实源，必须由同一批标准化事实生成。AI 输入保留指标、维度、分桶及证据引用；`preferencesByMetric` 只写入展示事实，不进入 AI 输入。

### 3. 第05章边界

移除素材 AI 输入中的 `audiencePreferences`，避免将第02章 TOP3 展示数据按素材重新复制一份。素材 AI 分析仅接收素材本身的指标、脚本、互动和高光事实。

### 4. Presentation 与前端

Presentation 组合 `report-display-facts.json` 与已校验的 AI 分析结果，不重新聚合事实。前端所有图表、下钻和固定数据只从 Presentation 的展示事实节点读取，不从 AI JSON 或历史顶层事实字段读取。

### 5. 一致性与溯源

展示事实和 AI 事实共享同一批次标识；生成 manifest 时记录各文件的 SHA-256。Presentation 声明每个章节的 `displayFactSource` 与 `aiFactSource`。校验器拒绝跨批次或使用不允许证据前缀的 AI 输出。

### 6. 兼容策略

新批次使用 `report-display-facts.json`。历史报告保留旧字段的读取 fallback，但新生成的 HTML 不再嵌入顶层 `audience`、`batchInsights` 或重复的 TOP3 结构。

## 非目标

- 不让 AI 生成或修正图表数值。
- 不改变现有指标计算口径、素材选择规则和 AI 洞察文本契约。
- 不在 Presentation 层重新聚合或推导业务事实。

## 验收标准

1. 新批次存在 `report-display-facts.json`，覆盖第01至第05章所有固定展示数据。
2. `chapter02-audience-input.json` 不含 `preferencesByMetric`；`chapter05-material-input.json` 不含 `audiencePreferences`。
3. 前端图表和 TOP3 下钻只读取展示事实节点。
4. AI audience 洞察证据只能引用 `audience.metrics`，且图表数值与该事实源逐项一致。
5. Presentation 和 HTML 不新增重复的顶层事实字段。
6. 现有测试通过，并完成一次真实报告渲染和 ZIP 完整性校验。
