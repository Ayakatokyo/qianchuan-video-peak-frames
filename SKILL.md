---
name: 千川短视频素材分析
description: 面向千川短视频投放复盘，帮助识别高消耗素材的表现亮点、内容结构、转化触发点和可复用创意模式，支持素材优劣判断、预算分配复盘、内容迭代方向和投放优化决策。
metadata:
  argument_hint: 输入授权店铺 ID、分析周期（如 2026-06-01 至 2026-06-15）、Top素材数量（如 9 或 50）和广告主id，生成千川短视频素材分析报告。
  pre_input: 帮我生成千川短视频素材分析报告，店铺 ID 是 ____，分析周期是 ____，Top素材数量是 ____，广告主id是 ____。
---

# 千川短视频素材分析

收集授权店铺 ID、分析周期、Top 素材数量和广告主 ID 后，按以下流程生成报告。标准入口用该店铺授权一次查询素材视频报表，按素材聚合后将 `materialId` 传入同店铺的 RPA；候选扩容、RPA 波次、视频下载、峰值帧提取、重试、补位和产物校验均由标准入口处理，不作为业务输入或手工步骤。

## 执行环境门禁

这是所有后续步骤的第一步。执行 `prepare`、`fetch-metadata`、`status` 或 `finalize` 前，先检查当前 Python 解释器是否具备 `requirements.txt` 中的 `requests` 和 `imageio-ffmpeg`；缺失时先安装到当前解释器，安装失败立即停止，不发起报表、RPA 或报告请求。

报表查询的 `--shop-id` 是授权店铺 ID，仅用于素材报表连接器。RPA 诊断使用 RPA 授权账户列表中的千川 ID；标准入口不会把报表店铺 ID 传给 RPA，而由 RPA 批处理自动解析当前授权的唯一千川 ID。无授权账户或多账户时，先处理授权选择错误，不重复提交素材诊断。

## 单 Agent 执行边界

本技能的完整流程由当前对话的主 Agent 直接执行，**不得创建、调用或委派子代理**，也不得调用会强制创建子代理的渲染校验、并行执行或任务委派技能。不要为进度等待、事实包核验、帧审阅、AI 分析、`finalize`、报告检查或归档拆分额外 Agent。

交付前的报告核验由当前主 Agent 直接完成：打开本地 `final-report/final-report.html`，在桌面 `1440x1000` 视口检查封面、目录、章节卡片、图表和高光帧，并结合 `run-summary.json` 与报告文件存在性确认交付状态。若当前环境无法打开或查看 HTML，只记录“视口核验待执行”及原因；不得以创建子代理作为替代，也不得将未完成的视口核验表述为已通过。

## 周期换算

相对周期使用 T+2 数据回流口径，先换算为 `CUSTOM` 闭区间：近七天为执行日前第 8 天至执行日前第 2 天，近三十天为执行日前第 31 天至执行日前第 2 天。不得将执行日或前一日纳入分析；例如执行日 `2026-08-04` 的近七天是 `2026-07-27` 至 `2026-08-02`（含首尾）。

## 标准流程

### 1. 准备事实包

在当前工作目录的 `$PWD/runs/<run-id>` 创建新的运行目录后执行。平台中 `$PWD` 是当前对话工作空间根目录；本地 Codex 从仓库执行时 `$PWD` 是技能仓库根目录。一次分析使用同一个 `run-id`，并在 `prepare`、`status`、AI 产物写入和 `finalize` 中复用；不得将运行目录设为技能安装目录。

```bash
python scripts/run_standard_analysis.py prepare \
  --shop-id "<授权店铺 ID>" \
  --start-date 2026-07-19 \
  --end-date 2026-07-25 \
  --top-n 9 \
  --advertiser-id "1770096415226951" \
  --output-dir "$PWD/runs/<run-id>"
```

首次 `prepare` 会自动检查缺失依赖并安装 `requirements.txt`。长时执行工具的完成态契约见[运维手册](references/qianchuan-operations-runbook.md#长任务元数据)。

完成后确认状态：

```bash
python scripts/run_standard_analysis.py status \
  --output-dir "$PWD/runs/<run-id>"
```

只验证报表网关授权与元数据、且不进入 RPA 时，使用同一类运行目录：

```bash
python scripts/run_standard_analysis.py fetch-metadata \
  --shop-id "<授权店铺 ID>" \
  --start-date 2026-07-19 \
  --end-date 2026-07-25 \
  --advertiser-id "1770096415226951" \
  --output-dir "$PWD/runs/<run-id>"
```

该命令只落盘 `source-data/gateway-response.json` 和 `run-summary.json`（状态 `metadata_ready`），不下载结果文件、不生成候选素材，也不调用 RPA。完整 `prepare` 在内网可达时会在相同的 `source-data/` 目录保留网关元数据、原始 CSV 与结构化 JSON，并以结构化记录生成候选素材和 ROI 事实。

仅当状态为 `analysis_pending` 时进入 AI 分析。必须使用本次 `prepare` 生成的 `diagnostic-analysis/analysis-units/analysis-unit-manifest.json`、manifest 声明的五个章节事实包与 `diagnostic-analysis/diagnostic-analysis-prompt.md`；不得从 `top_materials.json` 或其他候选清单组装事实，也不得手写 `missingRanks`。历史运行可能仍有 `diagnostic-analysis/diagnostic-analysis-data.json`，但新运行不再生成，新分析不得读取该快照。

将 `prepare` 视为单一长任务：不要因没有实时进度而另起运行，也不要手工干预 RPA、波次或后处理。出现中断、`GUARDRAIL_ERROR`、策略拒绝或状态不一致时，保留运行目录并遵循[运维手册](references/qianchuan-operations-runbook.md)；连接器与波次约束见 [RPA 输出契约](references/rpa-output-contract.md)。

RPA 执行只有一个 480 秒截止时间。超过截止时间时，系统终止该素材的本地 RPA worker，将其记录为不可重试的终态失败，并在同一波其余素材全部进入终态后接入下一整波候选素材补位；该超时素材不再占用本地 RPA 并发位。迟到的 CSV 或记录不回写已推进的运行，只能通过显式恢复流程核对。

### 2. 生成 AI 二次分析

#### AI 交接前置检查

先读取事实包和提示词：具体包括 manifest、五个章节事实包和唯一 Prompt；核对字节数、SHA-256、素材数、成功帧数与 `missingFrames`，再按 `chapter04-highlight-input.json` 的 `framePath` 实际查看每张 JPEG；每批不超过 6 帧。策略或 guardrail 拒绝时，立即按[运维手册](references/qianchuan-operations-runbook.md)冻结并恢复帧审阅，不得跳过任何帧或改用其他产物补充事实。

必须由当前对话模型在一次模型调用中统一读取五个章节事实包，并在同一运行目录一次写入完整的 `diagnostic-analysis/diagnostic-ai-analysis.json`，使用 `schemaVersion: 5`。不得拆成五次分析，不得通过外部模型服务、`codex exec` 或子进程替代当前对话模型。报告仍保留经营表现、人群、脚本、高光画面和互动时序的章节边界；其中第 01 部分只读取 `chapter01-portfolio-input.json` 的 `portfolio` 全量素材事实，覆盖近 7 天上新素材量、消耗、ROI、点击率、转化率、3 秒完播率和 CPM；不得读取 Chapter 01 顶层 RPA 选中素材字段 `materials` 或 `materialIds`。第 01 部分事实包的 `summaryMetrics` 只保留当前 `roi` 及其他榜单指标，不包含 `netSettlementRoi`、`overallPaymentRoi`；当前 ROI 来源字段为 `total_prepay_and_pay_order_roi2`。`overallConclusion` 是第一部分唯一直接展示的段落，建议写满 180-260 个中文字符，除关键数据锚点外至少包含三项分析判断和分阶段验证动作；`commercial.conclusion` 不得重复其完整指标串，应补充业务含义和验证动作。第 `04｜高光画面洞察` 必须输出 `highlightDashboard`，将每个成功高光帧按 `portrait`、`product`、`other` 固定顺序归类一次且仅一次。不得把帧秒数宣称为对应某句脚本话术。

以该提示词为 AI JSON 输出契约：按其中的字段、证据、覆盖、枚举、长度与定向修正规则完成一次完整分析。结论仅分析业务事实；每个成功 JPEG 都要有高光帧分析，`missingFrames` 必须在 `frameCoverageNote` 说明影响，证据引用逐字复用事实包标识。v4 仅用于历史报告兼容。

写入 AI JSON 后、调用 `finalize` 前，必须执行一次只读质量校验：

```bash
python scripts/run_standard_analysis.py validate-analysis \
  --output-dir "$PWD/runs/<run-id>" \
  --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

该命令复用报告渲染的 JSON、证据、帧覆盖、dashboard、枚举和内容长度门禁，不修改运行目录。校验失败时，当前主 Agent 仅修正报错字段后重新执行该命令；不得重新执行 `prepare`，不得调用子代理。校验通过后再进入 `finalize`；`finalize` 仍会重复执行同一质量门禁，防止跳过校验命令。

### 3. 校验并交付报告

```bash
python scripts/run_standard_analysis.py finalize \
  --output-dir "$PWD/runs/<run-id>" \
  --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

仅当 `run-summary.json` 状态为 `complete` 时交付 `final-report.zip`。解压后由当前主 Agent 打开 `final-report/final-report.html`；压缩包包含报告 HTML、数据文件和本地高光帧，图片不依赖三方链接，视频仍使用平台返回的 URL。报告使用 `assets/report/` 的视觉与交互资产，`finalize` 自动内联资源；不得交付草稿报告，也不得手工修改成品 HTML。交付前由当前主 Agent 在桌面 `1440x1000` 视口检查封面、目录、章节卡片、图表和高光帧，无文本遮挡或正文横向溢出。

若状态仍为 `analysis_pending`，修正 AI JSON 后，直接重新执行 finalize，无需重新执行 prepare 或任何运行命令；其余故障按[运维手册](references/qianchuan-operations-runbook.md)恢复。

## 峰值帧口径

每条素材保留第 0 秒封面帧 `zero_second`，并从 `CLICK_COUNT` 的全部局部峰值中按点击数降序、秒数升序取最多五帧。少于五个局部峰值是曲线特征，不伪造峰值；素材后处理成功要求第 0 秒帧成功，且存在局部峰值时至少一张峰值帧成功。
