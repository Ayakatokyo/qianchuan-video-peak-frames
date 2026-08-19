---
name: 千川短视频素材分析
description: 面向千川短视频投放复盘，帮助识别高消耗素材的表现亮点、内容结构、转化触发点和可复用创意模式，支持素材优劣判断、预算分配复盘、内容迭代方向和投放优化决策。
argument_hint: 输入分析周期（如 2026-06-01 至 2026-06-15）、Top素材数量（如 9 或 50）和广告主id，生成千川短视频素材分析报告。
metadata:
  pre_input: 帮我生成千川短视频素材分析报告，分析周期是 ____，Top素材数量是 ____，广告主id是 ____。
---

# 千川短视频素材分析

用户只需提供分析周期、Top素材数量和广告主id。标准工作流自动完成资产表筛选、候选扩容、波次 RPA、视频下载、峰值帧提取、重试、补位与产物校验；这些执行参数不是业务输入。

RPA 媒体字段兜底：RPA 已返回诊断记录但 `material_info.videoUrl` 暂时为空时，不立即按失败处理，也不立即跳到下一排名素材。技能会对该素材按恢复次数上限重新发起 RPA 获取；重取后拿到可用 `videoUrl` 才进入视频处理。仅当重取仍无可用地址或达到恢复上限，才按候选排名顺序使用后续素材补足 Top N。这样保留排名顺序，同时优先回收原排名素材的数据。

## 周期换算

相对周期使用 T+2 数据回流口径，最新可用完整自然日是执行日前第 2 天。近七天使用执行日前第 8 天至执行日前第 2 天；近三十天使用执行日前第 31 天至执行日前第 2 天。必须先换算为明确的 `CUSTOM` 闭区间，不得把执行日或前一日传入分析。执行日为 `2026-08-04` 时，近七天固定解析为 `2026-07-27` 至 `2026-08-02`（含首尾）；启动 `prepare` 前必须复核并记录该解析结果。

## 标准流程

### 1. 准备事实包

选择新的运行目录，调用标准入口：

```bash
python scripts/run_standard_analysis.py prepare \
  --start-date 2026-07-19 \
  --end-date 2026-07-25 \
  --top-n 9 \
  --advertiser-id "1770096415226951" \
  --output-dir "/path/to/new-run"
```

成功后使用只读状态入口确认结果：

```bash
python scripts/run_standard_analysis.py status \
  --output-dir "/path/to/new-run"
```

只有 `prepare` 成功且状态为 `analysis_pending` 时，才可进入 AI 分析；此时必须同时使用 `diagnostic-analysis/diagnostic-analysis-data.json` 和 `diagnostic-analysis/diagnostic-analysis-prompt.md`。不得从 `top_materials.json`、`top_materials_rpa_ready.json` 或其他候选清单自行构造事实，也不得手写 `missingRanks`。最终素材清单只包含后处理成功的素材；候选池、RPA 波次、失败重试、串行补偿和验证均由标准入口自动处理。

#### AI 交接前置检查

进入 AI 分析后固定按以下顺序执行一次：读取 `diagnostic-analysis-data.json` 和 `diagnostic-analysis-prompt.md`，核对素材数、成功帧数和 missingFrames，再按事实包中的 `framePath` 开始查看 JPEG。每批不超过 6 帧；若某批被策略或 guardrail 拒绝，继续拆分该批并重试，直至完成事实包内全部 JPEG 的查看，不得跳过任何帧。不得连续执行临时 jq 探查来理解字段结构，也不要从其他产物拼接第二份事实；只有发现文件不完整或字段不一致时，才进行针对性排查。

### 运行可观测性与中断

`prepare` 是包含外部 RPA 的长耗时同步任务。未看到实时进度不等于 RPA 未产出数据；在命令仍运行时，不得仅因终端静默或 Agent 尚未观察到 CSV 而中止任务、推断素材失败或启动第二个运行。运行环境允许只读观察时，可以检查运行目录的时间戳和审计产物，但这些观察不能替代 `prepare` 的退出结果，也不能改变执行中的批次。

进度口径必须分层记录：`rpaRequestCount` 是提交数，`recordsPersistedCount`/本地 CSV 数是诊断结果落盘数，`postprocessSucceededCount` 是截帧与 summary 均完成数；提交数绝不等于成功数。只有 `prepare` 正常退出、状态为 `analysis_pending` 且事实包完整时才可进入 AI 分析。

波次口径必须明确：`waveBarrier=true` 的“上一批结束”要求该批所有 RPA 请求已进入 `available` 或 `final_failed`，且所有已选素材的下载、截帧与 summary 后处理均已进入终态。只有此时才能接入下一批 RPA；后处理成功或重试耗尽后的失败都属于终态，失败不会阻断下一批 RPA 补位。最终进入事实包前仍必须等待并校验所有目标素材的后处理完成。

高光帧后处理按素材批量解码：同一素材的 `zero_second` 与峰值帧在一次 FFmpeg 精确时间点提取中输出，避免每帧重复启动并从视频开头解码；不使用输入侧 `-ss` 或 `-noaccurate_seek`，因此 JPEG 仍严格对应原始 `CLICK_COUNT` 秒点。批量 FFmpeg 失败时自动回退逐帧精确提取，并按原有 `missingFrames` 与成功判定记录。

若任务被中断或非零退出，先读取 `run-summary.json` 和 `peak-frames/batch-manifest.json`，再按运维手册处置。manifest 含 `running` 或 `submitted` 时，可能已有诊断 CSV 或 records 落盘而状态尚未回写；`recordsPersistedUnknownCount` 表示持久化状态待核实，不表示数据丢失。不得据此覆盖运行目录、重新发起标准入口或判定数据丢失。排障恢复必须优先核实保留产物，并沿诊断 CSV -> records -> 后处理的链路修复；是否需要恢复、修复或重新调用 RPA 仅按运维手册的条件执行。

`rpa_request_failed` 不等于平台任务失败。manifest 的 `rpaRecoveryAttempts` 会记录每次调用的网关状态码、请求标识、脱敏响应摘要，以及 worker 阶段、子进程退出码或空结果队列等本地证据。只有网关传输、429/5xx、子进程异常或空队列等可重试的快速失败才会在同一波内退避后重试；明确的 4xx、参数和 CSV/records 格式错误不会盲目重试。没有本地 CSV/records 时必须标记为本地远端对账不可用，不能据此断言 RPA 平台失败。

### 2. 生成 AI 二次分析

当前执行技能的模型必须读取事实包与提示词，并实际查看 `highlights.materials[*].frames[*].framePath` 指向的每张 JPEG。模型在同一运行目录的 `diagnostic-analysis/diagnostic-ai-analysis.json` 写入合法 JSON，使用 `schemaVersion: 5`；第 01 部分必须分别输出经营表现、人群、脚本、高光画面和互动时序五条组合层结论。第 `04｜高光画面洞察` 必须输出 `highlightDashboard`：将每个成功高光帧按 `portrait`、`product`、`other` 的固定顺序归类一次且仅一次，并为实际存在的分类填写共同画面结论与可验证的裂变方向。不得把帧秒数宣称为对应某句脚本话术。

生成 JSON 前必须先完成一次门禁自检：读取 `scripts/final_report_lib.py` 中的 `ALLOWED_DECISIONS`、`AI_QUALITY_MINIMUMS` 和 `HIGHLIGHT_DASHBOARD_MINIMUMS`，确认 `decision` 枚举、字段字符数、素材/高光帧覆盖和 dashboard 分类规则；自检未通过前不得写入 JSON 或调用 `finalize`。生成后只做一次本地校验，失败时按报错字段修正 AI JSON，不重新执行 RPA 或事实包准备。

结论只分析人群、脚本、投放明细和高光帧等业务事实；不得分析执行过程、失败、补位、重跑或批次调度。每个成功 JPEG 都必须有对应高光帧分析；若 `missingFrames` 非空，必须在该素材的 `frameCoverageNote` 说明不可用预期帧，并明确视觉结论基于其余成功帧。证据引用必须逐字复用事实包中的引用标识。标准输出使用 schema v5；v4 仅用于历史报告兼容，允许降级渲染组合层高光结论，但不承诺人像、产品、其他分类 dashboard。

### 3. 校验并交付报告

```bash
python scripts/run_standard_analysis.py finalize \
  --output-dir "/path/to/new-run" \
  --analysis-json "/path/to/new-run/diagnostic-analysis/diagnostic-ai-analysis.json"
```

仅当 `run-summary.json` 的状态为 `complete` 时，交付 `final-report.zip`。解压后打开 `final-report/final-report.html`；压缩包包含报告 HTML、数据文件和本地高光帧资产，图片不依赖三方链接。视频仍使用平台返回的 URL，不下载或打包视频文件。

报告统一视觉与交互资产位于本技能 `assets/report/`，`finalize` 会通过渲染器将 CSS 和 JavaScript 自动内联到 `final-report.html`，成品不依赖外部样式或脚本文件。不得交付草稿报告，不得手工修改成品 HTML，也不得绕过 AI JSON 的证据校验。交付前只需在桌面 `1440x1000` 视口检查封面、左侧目录、章节卡片、图表和高光帧，确认无文本遮挡或正文横向溢出；本技能不要求移动端验收。

## 峰值帧口径

每条素材固定保留第 0 秒封面帧 `zero_second`，并从 `CLICK_COUNT` 曲线的全部局部峰值中按点击数降序、秒数升序取最多五个帧。少于五个局部峰值是曲线特征，不伪造峰值。素材后处理成功要求第 0 秒帧成功，且存在局部峰值时至少一张峰值帧成功。

## 失败处理

标准入口失败时，查看对应运行目录中的 `run-summary.json`，其中包含失败阶段和审计产物位置。若状态仍为 `analysis_pending`，修正 AI JSON 后，直接重新执行 finalize，无需重新执行 prepare 或任何运行命令。其余中断、陈旧锁、manifest 与保留产物不一致等情形，必须遵循 [运维手册](references/qianchuan-operations-runbook.md)，不得把其中的恢复命令变成常规报告流程。连接器字段与运行时边界见 [RPA 输出契约](references/rpa-output-contract.md)。
