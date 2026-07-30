---
name: 千川短视频素材分析
description: 面向千川短视频投放复盘，帮助识别高消耗素材的表现亮点、内容结构、转化触发点和可复用创意模式，支持素材优劣判断、预算分配复盘、内容迭代方向和投放优化决策。
argument_hint: 输入分析周期（如 2026-06-01 至 2026-06-15）、Top素材数量（如 9 或 50）和广告主id，生成千川短视频素材分析报告。
metadata:
  pre_input: 帮我生成千川短视频素材分析报告，分析周期是 ____，Top素材数量是 ____，广告主id是 ____。
---

# 千川短视频素材分析

## 概览

提取时间线上第一个点击峰值，以及其后点击次数最高的三个峰值，并保存对应的 JPEG 帧和 JSON/Markdown 摘要。请使用随附的 Python 流程，确保 macOS 与 Windows 上的峰值和取帧规则一致且确定。

## 工作流程

1. 校验字段或准备连接器参数时，阅读 `references/rpa-output-contract.md`。
2. 已有匹配视频时，使用本地模式。
3. 创建隔离的 Python 3.9+ 环境并安装 `requirements.txt`。
4. 在线运行 TOP 素材批量任务前，确认 `ENV_BACKEND_HOST`、`YUCE_AUTHORIZATION`、`YUCE_SESSION_ID`、目标 `shop_id` 以及该 RPA 店铺对应的广告主 id。
5. 调用 RPA 前，先从资产表选择 TOP 素材。两项操作都必须使用同一明确的闭区间起止日期和广告主 id，且不得复用其他运行或周期的选材 JSON。
6. 报告成功前运行批次验证；审查保留的警告，且不要仅凭文件名认定帧有效。

### 分析周期换算

自然语言相对周期按 T+2 数据回流口径换算，最新可用完整自然日固定为执行日前第 2 天。用户输入“近七天”时，必须使用执行日前第 8 天至执行日前第 2 天的七个完整自然日；例如任务执行日为 `2026-07-27` 时，使用 `2026-07-19` 至 `2026-07-25`。用户输入“近三十天”时，必须使用执行日前第 31 天至执行日前第 2 天的三十个完整自然日；例如任务执行日为 `2026-07-27` 时，使用 `2026-06-26` 至 `2026-07-25`。

近七天、近三十天均应换算为明确的 `CUSTOM` 闭区间，并在资产表导出、RPA 预检、正式截帧和验证中复用同一区间；不得将执行任务当天或执行日前第 1 天传入 RPA。

## 本地运行

```bash
python scripts/extract_peak_frames.py \
  --input-json "/path/to/rpa-result.json" \
  --video "/path/to/material.mp4" \
  --output-dir "/path/to/output"
```

若生产环境单独管理 FFmpeg，请传入 `--ffmpeg-bin`。路径须作为独立参数传入；含空格的带引号路径可在 macOS 和 Windows 中使用。
随附流程允许每次 FFmpeg 探测或取帧最长运行 120 秒。托管执行环境可通过 `--ffmpeg-timeout-sec <positive-seconds>` 覆盖该值。

## TOP 素材批量运行

选择一个空的运行目录。选材 JSON 是必须保留的批次产物。目标交付 Top N 时，资产表阶段先导出更大的候选池；例如目标 Top 50 时先导出 Top 150，避免把资产表可选中但 RPA 当前不可用的素材直接送入正式批次。

```bash
python scripts/export_top_materials.py \
  --start-date 2026-06-01 \
  --end-date 2026-06-07 \
  --advertiser-id "<advertiser-id>" \
  --top-n 150 \
  --output "/path/to/run/top_materials.json"
```

导出器会将每条保留的 `origin_data` 行流式汇总到内存中的素材聚合结果，不会在整个日期范围内保留完整原始 JSON 行集合。
资产表选材必须传入与 RPA 店铺匹配的广告主 id；输出的 `top_materials.json` 会在 `filters.advertiserId` 保留该筛选条件，后续排查素材不存在时先核对此字段。
`--advertiser-id` 是业务参数名；资产表物理过滤字段固定是 `adv_id`。不要把资产表过滤字段写成 `advertiser_id`，否则平台会返回 `COLUMN_NOT_EXIST`。
应优先使用完整的目标闭区间（包括按月区间），以确保全局排序准确。只有当资产表服务或执行沙箱仍无法完成精确的流式导出时，才可将手动逐日合并候选项作为低内存兜底方案。

CLI 要求 `top_materials.json` 中包含 `actualStartDate`、`actualEndDate` 与 `materialCount`。对于 `CUSTOM` 批次，其日期必须与 `--custom-start-date` 和 `--custom-end-date` 完全一致；在任何 RPA 调用前会拒绝重复素材 ID 或错误的数量。

正式 TOP 素材批次使用流式 RPA 补位和成功即后处理。候选池按资产表整体消耗降序排列；例如目标 9 个素材时导出 27 个候选。`--candidate-top-n 27` 是候选上限，不是 RPA 调用数量承诺：全部成功时只提交前 9 个；高排名素材 RPA 失败或无数据时才继续按排序补位。

数量口径必须拆开记录：资产表获取素材 ID 数量是候选池数量，资产表阶段最多获取 `--candidate-top-n` 个素材 ID，写入 `top_materials.json` 后作为本次候选上限；传给 RPA 执行的素材 ID 数量是实际提交数量，不会一次性把候选池全部提交给 RPA。RPA 实际提交数量以 `submittedMaterials` 为准：查看 `batch-manifest.json` 的该数组长度；全成功时实际提交数量等于 `--target-top-n`；有失败时才继续补位提交后续候选，最多不超过 `--candidate-top-n`。

防错口径：用户要求 Top18 时，资产表导出可以使用 `--top-n 54` 建立候选池，但这只是候选上限。必须继续执行流式 RPA 命令，并明确传入 `--target-top-n 18` 与 `--candidate-top-n 54`；仅完成 `top_materials.json` 导出不代表已经分析 18 个素材。准备诊断事实包、AI 二次分析和最终报告时，必须使用 `peak-frames/top_materials_rpa_ready.json`；不得把 `top_materials.json` 传给诊断事实包或最终报告，也不得把候选池数量写成最终分析素材数量。

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --target-top-n 9 \
  --candidate-top-n 27 \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --rpa-concurrency 3 \
  --postprocess-concurrency 3 \
  --output-dir "/path/to/run/peak-frames"
```

RPA 通道最多同时运行 3 个连接器请求，并严格从候选头部补位。每当一个素材获得可解析的诊断数据、目标 `material_info` 和非空 `videoUrl` 后，系统立即写入规范化记录并送入独立的后处理通道；视频下载、抽帧和摘要生成可以和后续 RPA 调用并行。并发只影响完成时间，不改变最终素材和报告顺序。

流式运行会写入 `batch-manifest.json`，其中的 `submittedMaterials`、`selectedMaterials`、`rejectedMaterials` 和 `cancelledMaterials` 分别表示已提交、RPA 可用、RPA 不可用与未提交取消的候选。每个条目保留 rank、素材元数据、两个通道状态、失败原因、诊断记录路径、摘要路径和 `timingsSec`；其中包含 `rpaFetchSec`、`diagnosisRecordWriteSec`、`videoDownloadSec`、`frameExtractionSec` 和 `totalSec`。`top_materials_rpa_ready.json` 仍会写出，作为最终报告和下游脚本的兼容输入；其素材只包含 RPA 成功的 Top N，并保持资产表排序。

如果候选池耗尽时仍未选到目标数量，或任一选中素材后处理失败，命令返回非零但保留所有已完成的诊断记录、CSV、摘要和 JPEG。CSV 下载或解析失败仍会阻塞流程，并记录为 RPA 拒绝；不要将它解释为素材删除。后处理失败则保留 RPA selected 状态，不会伪装为 RPA 失败或无条件补充新的低排名素材。

`--preflight-only`、`--rpa-preflight-json` 和 `--allow-rpa-refetch` 仅用于诊断、重放既有预检产物或兼容旧批次，不是新 TOP 素材批量运行的推荐步骤。

兼容旧批次仍支持 `--resume` 与 `--verify`：恢复时后续 TOP 素材仍会继续运行；验证会写入 `batch-verification.json`，保留产物不合格时返回退出码 `6`。流式 Top-N 运行应直接检查其 schemaVersion 2 manifest 的状态数组和错误原因。

## 最终 HTML 报告流程

完成 RPA 提取和 `--verify` 后，准备本地诊断事实包：

```bash
python scripts/prepare_diagnostic_analysis.py \
  --top-materials-json "/path/to/run/top_materials_rpa_ready.json" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --peak-output-dir "/path/to/run/peak-frames" \
  --output-dir "/path/to/run/diagnostic-analysis"
```

`--diagnosis-records-dir` 推荐传规范化 JSON 目录 `diagnosis-records`。若排障或旧执行只保留了 RPA 原始 CSV，事实包准备和最终报告渲染也兼容保留的 `diagnosis-csv` 目录，会自动解析同名 `<materialId>.csv`；不得在存在 CSV 的情况下把空事实包当作完成报告继续交付。

当前执行技能的模型必须读取 `diagnostic-analysis-data.json` 和 `diagnostic-analysis-prompt.md`，并实际查看事实包 `highlights.materials[*].frames[*].framePath` 指向的每张 JPEG。模型在同一次任务内生成 `/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json`，必须使用 `schemaVersion: 2`，分开 `SHOW_COUNT`、`CLICK_COUNT` 与 `TRANSACTION_AMOUNT` 人群结论，展示文案使用“展现次数”“点击次数”“成交金额”，并逐字引用事实包已有的 evidence ref。模型不得仅凭帧的秒数或点击值猜测画面；高光帧观察必须基于实际查看 JPEG。

AI 二次分析必须按“真实数据 + 一句总结结论”组织内容，不得只写抽象判断。`overallConclusion` 必须综合人群、脚本、高光帧和投放明细中的主要事实，对本次提取到的所有素材做组合级汇总分析；`batchInsights.topic=integrated` 必须同时引用人群、脚本和高光帧证据；人群洞察必须分别从展现次数、点击次数、成交金额三个角度展开。

AI 二次分析只分析 RPA 提取到的业务事实，包括人群、脚本、投放明细和高光帧。不得分析执行过程，也不得把运行目录名、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑、批次调度或素材 rank 缺口写入任何输出字段。若当前对话或日志中出现这些流程信息，只能用于排障，不能进入 `diagnostic-ai-analysis.json`。

AI 二次分析输出必须和最终 HTML 的 5 个页面 section 对齐：`overallConclusion` 与 `batchInsights.topic=integrated` 对应 `01｜TOP 素材组合结论`，`audience` 对应 `02｜人群洞察`，`script` 对应 `03｜脚本洞察`，`materialAnalyses` 对应 `04｜单素材深度分析`，`nextActions` 对应 `05｜下一轮策略`。策略卡片只展示 `title` 和 `body`，`priority` 只用于排序，不在页面展示。不得输出 `batchInsights.topic=highlight`，高光帧只作为组合结论和单素材分析的证据来源；不得输出“数据质量与执行口径”“方法说明”“补充说明”等额外页面段落，也不得输出顶层 `warnings` 字段；缺失输入或事实不足时，在对应业务字段中写“数据不足以判断”等边界说明。

最终报告渲染器不会调用外部 API，也不会生成业务结论；它只校验并注入当前执行技能的模型写入的本地结构化结论。若渲染器提示 schema 或证据引用错误，模型必须修正 `diagnostic-ai-analysis.json` 后重新渲染。

渲染最终 HTML 报告：

```bash
python scripts/render_final_report.py \
  --top-materials-json "/path/to/run/top_materials_rpa_ready.json" \
  --peak-output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --analysis-json "/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json" \
  --output-dir "/path/to/run/final-report"
```

仅在有意生成草稿报告时才省略 `--analysis-json`；草稿不是完成的商业分析报告。将报告视为完成前，请审查 `final-report/final-report-data.json` 的状态为 `complete` 或已解释的 `partial`、`final-report/final-report.html`、批次验证错误和警告。

## 单素材 RPA 调试运行

仅用于诊断或重放一个已知素材。`--material-id` 不是 TOP 素材批量运行的替代选材方式。

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --material-id "7647499280192372763" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-07-19 \
  --custom-end-date 2026-07-25 \
  --output-dir "/path/to/output"
```

自然语言近七天、近三十天调试同样先按 T+2 口径换算为 `CUSTOM`。使用 `CUSTOM` 时，必须同时提供 `--custom-start-date` 与 `--custom-end-date`。适配器调用固定的预策连接器 `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis`，将 `material_info.videoUrl` 下载到临时视频文件，然后复用本地峰值和取帧流程。RPA 摘要记录 `videoSource: rpa-temporary-download`，而不记录已删除的临时下载路径。

## 选峰契约

- 仅读取指标为 `CLICK_COUNT` 的 `interaction_series` 记录。
- 排除时序的第一个和最后一个点。
- 将等值平台合并为一个峰值，并取其最早秒数。
- 先选择最早的峰值。
- 仅对其后的峰值按点击数降序、秒数升序排序；取前三个。
- 若其后峰值少于三个，返回可用帧并给出警告；绝不伪造峰值。

## 快速参考

| 需求 | 操作 |
| --- | --- |
| 默认 FFmpeg | 从 `requirements.txt` 安装 `imageio-ffmpeg` |
| 托管 FFmpeg | 添加 `--ffmpeg-bin "/path/to/ffmpeg"` |
| FFmpeg 超时 | 默认 120 秒；通过 `--ffmpeg-timeout-sec <positive-seconds>` 覆盖 |
| 本地结果 | 检查 `<output>/<material-id>/summary.json` |
| RPA 账户 | 使用 `scripts/rpa_adapter.py` 助手或预策连接器账户列表查找 `shop_id` |
| TOP 素材批量任务 | 导出候选池 `top_materials.json`，再用一次流式 RPA 命令传入 target 与 candidate Top N |
| 加速 TOP 素材批量任务 | 使用 `--rpa-concurrency 3` 和 `--postprocess-concurrency 3` 实现受控并行 |
| 批次状态 | 检查 `batch-manifest.json` 的 submitted、selected、rejected 和 cancelled 素材及失败原因 |
| 准备诊断事实 | 在流式 RPA 批次完成后运行带 `--peak-output-dir` 的 `prepare_diagnostic_analysis.py` |
| 真实商业分析 | 当前执行技能的模型读取事实包、实际查看 JPEG、写出 schema v2 结论 JSON |
| 最终 HTML 报告 | 运行带 `--analysis-json` 的 `render_final_report.py`；草稿不代表完成 |
| 单素材调试 | 传入 `--source rpa --shop-id ... --material-id ... --date-range-type ...` |
| 回归检查 | 运行 `python -m unittest tests/test_peak_frames.py -v` |

## 常见错误

- 不要将所有峰值一起排序；最早峰值必须在后续排序前固定。
- 不要将第一个或最后一个样本视为峰值。
- 其后峰值不足三个不应直接失败；应给出警告。
- 不要仅凭文件名报告成功；需检查摘要和 JPEG。
- 不要手写 `platform` 或 `function_code`；适配器会将其固定为巨量千川单素材诊断连接器。
- 不要在资产表选材前调用批量 RPA，也不要使用 `--material-id` 绕过选材。
- 不要把资产表过滤字段写成 `advertiser_id`；CLI 参数仍叫 `--advertiser-id`，但底层 SQL 必须使用资产表真实列 `adv_id`。若平台返回 `COLUMN_NOT_EXIST` 且提示 `advertiser_id` 不存在，说明执行环境里的脚本或手写 SQL 仍是旧字段。
- 不要使用不同的排序和诊断日期范围。
- 不要覆盖或丢弃运行目录中的 `top_materials.json`。
- 不要对不同的素材选择、店铺或日期范围使用 `--resume`；应新建批次。
- `--verify` 返回零前，不要报告批次成功；退出码 `6` 表示保留输出未通过验证。
- 将 RPA CSV 下载或解析失败视为阻塞问题；诊断时检查保留的 `diagnosis-csv/<materialId>.csv`。
- 不要期待 `render_final_report.py` 调用模型或外部 API；当前执行技能的模型必须在同一任务中查看 JPEG、生成并修正 `diagnostic-ai-analysis.json`。
- 不要在最终报告中隐藏失败素材；保留其警告和失败原因。
