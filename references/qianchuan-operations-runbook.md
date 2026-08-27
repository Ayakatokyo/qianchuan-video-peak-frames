# 千川素材分析运维手册

仅在标准工作流失败且需要排障时使用；不要把这些命令作为常规报告流程。常规报告始终从 `scripts/run_standard_analysis.py prepare` 开始，并以 `finalize` 收口。

## 运行目录

标准运行目录固定为 `$PWD/runs/<run-id>`。平台的 `$PWD` 是当前对话工作空间根目录；本地从技能仓库执行时，`$PWD` 是仓库根目录。`prepare`、`status`、AI 产物、`finalize` 和所有恢复命令必须复用同一个运行目录。本手册中的 `/path/to/run` 均指已创建的 `$PWD/runs/<run-id>`，不得改为技能安装目录。

在启动 AI 分析前，先运行：

```bash
python scripts/run_standard_analysis.py status --output-dir "/path/to/run"
```

仅当状态为 `analysis_pending`，并且五个章节事实包、manifest 与 `diagnostic-analysis-prompt.md` 都由成功的 `prepare` 生成时，才可继续。历史运行可能仍有 `diagnostic-analysis-data.json`，但新运行不再生成；不得从该快照、`top_materials.json` 或手写 `missingRanks` 组装事实包。

## 长任务元数据

通过长时执行工具提交 `prepare` 时，设置 `max_runtime_seconds: 3600`、`completion_mode: data_collection`、`success_pattern: "run_summary=.*run-summary\\.json"`，并以本次 `run-summary.json` 的状态作为 `verify_command`。这些是工具元数据，不是 `prepare` 的命令行参数；只有验证为 `analysis_pending` 才可进入 AI 分析。

## 策略拒绝但后台仍在运行

`GUARDRAIL_ERROR`、策略拒绝或连接器拒绝只能说明调用层没有给出可用完成态，不能据此判断 `prepare` 未提交或已失败。先通过运行面的后台任务/执行会话查询该命令的真实状态；查询不可用时，再反读 `run-summary.json`、`peak-frames/batch-manifest.json` 和 CSV/records/summary 落盘数量。后台为 `running` 时不得重复提交、覆盖目录或向用户报告任务中止；保持同一运行目录，等待后台终态。后台为 `analysis_pending` 时直接进入 AI 分析，为 `complete` 时仅核验交付物。

长任务的工具返回与真实提交是两个字段：调用层拒绝但无法确认后台是否接收时，记录 `tool_call_blocked`、`submitted_unverified` 和 `needs_confirmation`，表示提交结果未知；不得在未知状态重提 prepare，也不得在未知状态将任务改写为 `failed`。只有后台明确终止、退出码可见，且 `run-summary.json`、锁文件和产物链路共同证明未完成时，才允许进入失败恢复。

连续策略拒绝时只做一次有目的的后台/运行摘要核查；之后停止同类探查调用，保留最后一次成功观测、拒绝时间、runtime session/task 标识、运行目录和锁文件。只读核验恢复后产生的状态和产物，再决定接续或恢复；在此之前对用户使用“后台状态未知/待确认”，不使用“进程已中止”或“prepare 失败”。

图片理解工具首次被策略拒绝时，将 AI 阶段记录为 `frame_review_pending`，保存已完成帧批次、未完成帧批次和事实包路径；不得继续拆分批次、逐帧重试或改用 bash/搜索/文件读取工具绕过策略。工具恢复后只从未完成批次续接，已完成批次不得重复查看；若恢复前无法核验，只能报告 AI 阶段待恢复，不得报告事实包或 prepare 失败。

只有后台明确终止且运行摘要或产物链路也显示未完成，才按本手册的“运行中断或陈旧批次”处理。策略服务未提供原始拒绝原因时，记录为 `tool_call_blocked` 与“待策略侧日志确认”；不要把业务任务、RPA 或 FFmpeg 归因为策略故障。

## 运行中断或陈旧批次

先读取运行目录的 `run-summary.json` 和 `peak-frames/batch-manifest.json`。若 manifest 中仍有 `running` 或 `submitted` 状态，不能直接覆盖目录。恢复已有批次时，默认先做不触发 RPA 的本地修复；只有用户明确授权重发 RPA 时才使用 `--resume`：

先运行 `run_standard_analysis.py status`，按五层产物分别盘点并记录数量：`rpaRequestCount`（提交数）→ `rpaAcceptedCount`（有效诊断结果数）→ `rpaSelectedCount`（含可用 videoUrl 的选中数）→ `recordsPersistedCount`/CSV 数（诊断结果已落盘数）→ `postprocessSucceededCount`（截帧与 `summary.json`、`summary.md` 均完整数）。提交数、平台/UI 的“成功”数字、素材目录数均不等于本地落盘或完整后处理数；没有本地 CSV/records 时不得声称平台已成功或失败。

波次调度使用双重屏障：只有上一批 RPA 全部终态且该批已选素材后处理全部终态，下一批 RPA 才会提交。后处理重试耗尽后的失败是终态，允许下一批提交候选素材补位；后处理尚在运行或重试时不得接入下一批。

若用户未明确授权再次调用 RPA，先运行“修复保留产物”章节的 `repair_manifest.py`。该修复优先核验 `rpa-receipts/<materialId>.json` 的 `records_persisted` 收据；收据中的 records 已包含目标素材的平铺 `recordType=material_info` 与非空 `videoUrl` 时，即使 manifest 仍为 submitted/pending，也直接进入本地后处理，不会发起 RPA。即使 manifest 的 `postprocessStatus` 已写成 `succeeded`，也会以实际 `summary.json`、`summary.md` 和 JPEG 是否完整为准，补跑缺失的截帧。检查 `repair-manifest.json` 的 `rpaCalls` 必须为 `0`，并核对修复后的三层数量。

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --top-materials-json "/path/to/run/top_materials.json" \
  --target-top-n 9 \
  --candidate-top-n 27 \
  --date-range-type CUSTOM \
  --custom-start-date 2026-07-19 \
  --custom-end-date 2026-07-25 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/peak-frames" \
  --resume
```

`--resume` 只可用于同一日期、同一候选池和同一店铺的既有批次；它会复用有效 summary 与已落盘 records。

`--resume` 对没有本地 CSV/records 的 `submitted` 素材可能重新提交 RPA。因而只有用户明确授权“恢复并允许重发 RPA”时才可使用；用户要求停止 RPA、或悬空请求尚未完成远端对账时，禁止使用 `--resume`，改用上面的本地 repair 并把悬空素材保留为待对账。

## 修复保留产物

当 CSV、records、summary 或 manifest 不一致时，执行：

```bash
python scripts/repair_manifest.py \
  --output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --diagnosis-csv-dir "/path/to/run/diagnosis-csv"
```

该命令先执行 `records_persisted receipt -> records`，再执行 `CSV -> records`、`records -> 后处理` 和 `summary/JPEG -> manifest` 修复，不重新调用 RPA。若 manifest 缺失，再传入 `--top-materials-json`；无法证明诊断阶段时保持 `rpaStatus: unknown`。

检查保留的 `diagnosis-records/`、`diagnosis-csv/`、素材 `summary.json` 与 JPEG，而不是仅凭文件名判断成功。RPA 输出字段、下载上限和波次状态的完整约束见 [RPA 输出契约](rpa-output-contract.md)。
