---
name: 千川短视频素材分析
description: 面向千川短视频投放复盘，识别高消耗素材的表现亮点、内容结构、转化触发点和可复用创意模式。
metadata:
  argument_hint: 输入授权店铺 ID、分析周期、Top素材数量和广告主id，生成千川短视频素材分析报告。
  pre_input: 帮我生成千川短视频素材分析报告，店铺 ID 是 ____，分析周期是 ____，Top素材数量是 ____，广告主id是 ____。
---

# 千川短视频素材分析

收集店铺 ID、分析周期、Top 素材数量和广告主 ID。标准入口用店铺授权查询素材视频报表，再将 `materialId` 交给同店铺 RPA；候选扩容、波次、下载、截帧、重试、补位和校验均自动完成。

## 约束

- **环境**：执行 `prepare`、`fetch-metadata`、`status` 或 `finalize` 前确认当前解释器有 `requests`、`imageio-ffmpeg`；缺失则安装，失败即停，不发起任何请求。`prepare` 也会再次执行该门禁。
- **授权**：`--shop-id` 只用于报表连接器；RPA 使用授权账户中的唯一千川 ID。无授权或多账户先处理授权选择，不重复提交素材诊断。
- **单 Agent 执行边界**：主 Agent 直接执行全流程，**不得创建、调用或委派子代理**，也不得调用会强制创建子代理的渲染校验、并行执行或任务委派技能。
- **日期**：相对周期按 T+2 换算为 `CUSTOM` 闭区间：近七天为执行日前第 8 天至执行日前第 2 天，近三十天为执行日前第 31 天至执行日前第 2 天；不含执行日和前一日。例如执行日 `2026-08-04` 的近七天是 `2026-07-27` 至 `2026-08-02`。
- **长任务**：`prepare` 是单一长任务；无实时进度不要另起运行或手工干预。中断、`GUARDRAIL_ERROR`、策略拒绝或状态不一致时保留目录，按[运维手册](references/qianchuan-operations-runbook.md)和 [RPA 输出契约](references/rpa-output-contract.md)恢复。长时执行工具的完成态契约见[运维手册](references/qianchuan-operations-runbook.md#长任务元数据)。RPA 的异步 `task_group_id`、波次终态、4260 秒外层截止和 `partial_success` 规则均以两份参考文档为准；恢复只查询已保存远端 ID，**不得重复提交**。

## 标准流程

### 1. 准备事实包

每次使用同一个 `$PWD/runs/<run-id>`，不得写入技能安装目录：

```bash
python scripts/run_standard_analysis.py prepare \
  --shop-id "<授权店铺 ID>" --start-date 2026-07-19 --end-date 2026-07-25 \
  --top-n 9 --advertiser-id "1770096415226951" \
  --output-dir "$PWD/runs/<run-id>"
python scripts/run_standard_analysis.py status --output-dir "$PWD/runs/<run-id>"
```

只验证报表授权、不进入 RPA 时改用 `fetch-metadata`（仍写入同一类目录；只产生 `source-data/gateway-response.json` 与 `run-summary.json`，状态 `metadata_ready`）。仅当状态为 `analysis_pending` 才继续 AI。

AI 只读本次运行生成的 `diagnostic-analysis/analysis-units/analysis-unit-manifest.json`、五个章节事实包和 `diagnostic-analysis/diagnostic-analysis-prompt.md`；不得从 `top_materials.json` 组装事实，**不得手写 `missingRanks`**。历史运行可能仍有 `diagnostic-analysis/diagnostic-analysis-data.json`，但新运行不再生成且不得读取。

### 2. 视频与预览

标准入口按顺序完成：**RPA 下载原始 MP4** 并持久化 `video-assets/<materialId>.mp4` → **原始视频提取高光帧**（以原始 MP4 提取 `zero_second` 与局部峰值 JPEG）→ **生成报告预览 MP4**（FFmpeg，并写入 `peak-frames/report-video-assets.json`）→ **报告引用预览 MP4**（放入 `final-report/assets/videos/<materialId>.mp4`，HTML/JSON 只引用相对本地路径）→ 打包 `final-report.zip`。每个引用文件须存在、非符号链接、可被 ZIP 收录并通过解码校验；图片不依赖三方链接；原始 MP4 保留追溯但**原始 MP4 不进入 ZIP**。任一失败都不能标记后处理成功或进入 `complete`。

### 3. 一次 AI 分析

Chapter 01 的 `portfolio.hotMaterialRule` 若存在，只能据其阈值、命中数量、素材指标和 `evidenceRef` 输出 `hotMaterialInsight`；Presentation 将其并入既有 AI 区域，不重复渲染爆款卡片。

#### AI 交接前置检查

先读取事实包和提示词（manifest、五个章节事实包和唯一 Prompt），核对字节数、SHA-256、素材数、成功帧数、`missingFrames`，再按 `chapter04-highlight-input.json` 的 `framePath` 实际查看 JPEG（每批不超过 6 帧）。策略或 guardrail 拒绝时立即**冻结并恢复帧审阅**，不得跳过任何帧或改用其他产物。

当前对话模型在**一次模型调用**中统一读取五个事实包，并一次写入 `diagnostic-analysis/diagnostic-ai-analysis.json`（`schemaVersion: 5`）；不得拆分调用、使用外部模型、`codex exec`、子进程或子代理。保留经营表现、人群、脚本、高光画面和互动时序边界；第 01 只读 `chapter01-portfolio-input.json` 的 `portfolio` 全量事实，不读顶层 `materials`/`materialIds`、`topMaterialRoi`、RPA 精选或 Chapter 05 回退数据。`summaryMetrics` 使用当前 ROI（`total_prepay_and_pay_order_roi2`），不含 `netSettlementRoi`/`overallPaymentRoi`；`overallConclusion` 为第一部分唯一直接展示段落（建议 180-260 字，含至少三项判断和分阶段动作），`commercial.conclusion` 只补充业务含义。第 `04｜高光画面洞察` 输出 `highlightDashboard`，每个成功帧按 `portrait`、`product`、`other` 固定顺序归类一次且仅一次；不得把帧秒数宣称为具体话术。

以该提示词为 AI JSON 输出契约，按其字段、证据、覆盖、枚举和长度规则生成结果：每个成功 JPEG 都有分析，`missingFrames` 写入 `frameCoverageNote`；引用逐字复用事实包标识。`preferencesByMetric` 仅供 drill-down，AI 不读取其 TOP3 字段。v4 仅用于历史报告兼容。`keywordCloudInsight.oneSentenceConclusion` 为 28-45 个中文字符的单一核心判断；summary 控制在 60-110 个中文字符，验证动作放入 summary 或 sellingPoints；不得重复关键词频次、卖点卡片和验证动作。

逐帧分析必须由实际 JPEG 的独有内容写成：观察同时交代主体/位置、动作或使用状态、人物产品场景关系、信息层、叙事作用和相邻帧变化；商业解释对应本帧视觉机制及该素材的脚本或互动事实。删除素材 ID、帧类型、秒数、数值和分类名后，任意两条逐帧观察或商业解释仍须可区分。`customClassification` 的每组 3 条 `commonConclusion` 与 3 条 `derivativeDirections` 同样不得以替换分类名、指标或帧引用复用正文；每条仅引用本组帧，并从本组画面机制推导验证。

写入后先只读校验，再收口：

```bash
python scripts/run_standard_analysis.py validate-analysis \
  --output-dir "$PWD/runs/<run-id>" \
  --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

失败只修正报错字段并重跑校验，不重跑 `prepare`；通过后执行 `finalize`。`finalize` 会再次执行同一门禁。

### 4. 交付

```bash
python scripts/run_standard_analysis.py finalize \
  --output-dir "$PWD/runs/<run-id>" \
  --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

仅当 `run-summary.json` 为 `complete` **同时交付以下两个产物**：独立报告 `final-report/final-report.html` 与完整压缩包 `final-report.zip`（HTML、数据、本地 JPEG 和预览 MP4）。图片和视频均为本地资源；不得交付草稿报告、不得只交付压缩包；报告使用 `assets/report/`，`finalize` 自动内联资源，不得手工修改或改写成品 HTML。若仍为 `analysis_pending`，修正 AI JSON 后，直接重新执行 finalize，无需重新执行 prepare 或任何运行命令；其余故障按运维手册处理。

## 峰值帧口径

每条素材保留第 0 秒封面帧 `zero_second`，从 `CLICK_COUNT` 的全部局部峰值按点击数降序、秒数升序取最多五帧；少于五个不伪造。后处理成功要求第 0 秒帧成功，且存在局部峰值时至少一张峰值帧成功。
