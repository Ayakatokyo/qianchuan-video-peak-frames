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

选择一个空的运行目录。选材 JSON 是必须保留的批次产物。

```bash
python scripts/export_top_materials.py \
  --start-date 2026-06-01 \
  --end-date 2026-06-07 \
  --advertiser-id "<advertiser-id>" \
  --top-n 50 \
  --output "/path/to/run/top_materials.json"
```

导出器会将每条保留的 `origin_data` 行流式汇总到内存中的素材聚合结果，不会在整个日期范围内保留完整原始 JSON 行集合。
资产表选材必须传入与 RPA 店铺匹配的广告主 id；输出的 `top_materials.json` 会在 `filters.advertiserId` 保留该筛选条件，后续排查素材不存在时先核对此字段。
`--advertiser-id` 是业务参数名；资产表物理过滤字段固定是 `adv_id`。不要把资产表过滤字段写成 `advertiser_id`，否则平台会返回 `COLUMN_NOT_EXIST`。
应优先使用完整的目标闭区间（包括按月区间），以确保全局排序准确。只有当资产表服务或执行沙箱仍无法完成精确的流式导出时，才可将手动逐日合并候选项作为低内存兜底方案。

CLI 要求 `top_materials.json` 中包含 `actualStartDate`、`actualEndDate` 与 `materialCount`。对于 `CUSTOM` 批次，其日期必须与 `--custom-start-date` 和 `--custom-end-date` 完全一致；在任何 RPA 调用前会拒绝重复素材 ID 或错误的数量。

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --ffmpeg-timeout-sec 120 \
  --concurrency 3 \
  --output-dir "/path/to/run/peak-frames"
```

保留 `top_materials.json`、位于 `diagnosis-csv/<materialId>.csv` 的原始诊断 CSV、规范化记录、峰值帧摘要、JPEG 和警告。CSV 下载或解析失败仍会阻塞流程；在审查其保留的 CSV 和帧摘要之前，不得将该素材报告为成功。

命令会在峰值帧输出目录写入 `batch-manifest.json`。使用 `--concurrency <positive-integer>` 控制素材间的受控并行；省略时默认一次处理一个素材。每个素材条目会记录 `timingsSec`，其中包含 `rpaFetchSec`、`diagnosisRecordWriteSec`、`videoDownloadSec`、`frameExtractionSec` 和 `totalSec`，以便在进一步调优前定位慢任务。失败素材会记录在此文件中，后续 TOP 素材仍会继续运行；即使成功产物已保留，只要有任一素材仍失败，命令就以非零状态退出。使用 `--resume` 可在完全相同的选中 ID、店铺和日期范围下恢复任务；它只跳过保留摘要和 JPEG 仍能通过验证的成功素材，并重试失败或中断的素材。

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/peak-frames" \
  --resume
```

在完整成功运行或恢复完成后，无需调用 RPA、下载视频或运行 FFmpeg，即可验证保留产物：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --output-dir "/path/to/run/peak-frames" \
  --verify
```

验证会在输出目录原子写入 `batch-verification.json`。它将清单与选中 ID、店铺和日期范围绑定；检查每个素材状态、保留摘要、帧路径、非空 JPEG 结构，并汇总警告。当保留产物未通过验证时返回退出码 `6`。回执不包含凭据、视频 URL 或带签名的诊断 CSV URL。

## 最终 HTML 报告流程

完成 RPA 提取和 `--verify` 后，准备本地诊断事实包：

```bash
python scripts/prepare_diagnostic_analysis.py \
  --top-materials-json "/path/to/run/top_materials.json" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/diagnostic-analysis"
```

让外部分析环境使用保留的事实包和提示词生成 `/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json`。最终报告渲染器不会调用外部 API，也不会生成业务结论；它只注入 `diagnostic-ai-analysis.json` 中的本地结构化结论。

渲染最终 HTML 报告：

```bash
python scripts/render_final_report.py \
  --top-materials-json "/path/to/run/top_materials.json" \
  --peak-output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --analysis-json "/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json" \
  --output-dir "/path/to/run/final-report"
```

仅在有意生成草稿报告时才省略 `--analysis-json`。将报告视为完成前，请审查 `final-report/final-report-data.json`、`final-report/final-report.html`、批次验证错误和警告。

## 单素材 RPA 调试运行

仅用于诊断或重放一个已知素材。`--material-id` 不是 TOP 素材批量运行的替代选材方式。

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --material-id "7647499280192372763" \
  --date-range-type "LAST_7_DAYS" \
  --output-dir "/path/to/output"
```

使用 `CUSTOM` 时，必须同时提供 `--custom-start-date` 与 `--custom-end-date`。适配器调用固定的预策连接器 `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis`，将 `material_info.videoUrl` 下载到临时视频文件，然后复用本地峰值和取帧流程。RPA 摘要记录 `videoSource: rpa-temporary-download`，而不记录已删除的临时下载路径。

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
| TOP 素材批量任务 | 先运行资产表选材，再将运行目录中的 `top_materials.json` 与相同的 CUSTOM 日期传给 RPA |
| 加速 TOP 素材批量任务 | 确认网关可承受并行 RPA 调用后，添加 `--concurrency 3` 实现受控并行 |
| 恢复 TOP 素材批量任务 | 使用完全相同的批次命令添加 `--resume`；先检查 `batch-manifest.json` |
| 验证 TOP 素材批量任务 | 使用完全相同的批次绑定添加 `--verify`；检查 `batch-verification.json`、警告和退出码 |
| 准备诊断事实 | 在 RPA 验证后运行 `prepare_diagnostic_analysis.py` |
| 最终 HTML 报告 | 运行 `render_final_report.py`；仅草稿可省略 `--analysis-json` |
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
- 不要期待 `render_final_report.py` 调用模型或外部 API；最终结论需提供 `diagnostic-ai-analysis.json`。
- 不要在最终报告中隐藏失败素材；保留其警告和失败原因。
