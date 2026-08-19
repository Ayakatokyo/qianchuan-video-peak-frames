# 千川素材分析运维手册

仅在标准工作流失败且需要排障时使用；不要把这些命令作为常规报告流程。常规报告始终从 `scripts/run_standard_analysis.py prepare` 开始，并以 `finalize` 收口。

在启动 AI 分析前，先运行：

```bash
python scripts/run_standard_analysis.py status --output-dir "/path/to/run"
```

仅当状态为 `analysis_pending`，并且 `diagnostic-analysis-data.json` 与 `diagnostic-analysis-prompt.md` 都由成功的 `prepare` 生成时，才可继续。不得从 `top_materials.json` 或手写 `missingRanks` 组装事实包。

## 运行中断或陈旧批次

先读取运行目录的 `run-summary.json` 和 `peak-frames/batch-manifest.json`。若 manifest 中仍有 `running` 或 `submitted` 状态，不能直接覆盖目录。恢复已有批次时，默认先做不触发 RPA 的本地修复；只有用户明确授权重发 RPA 时才使用 `--resume`：

先按三层产物分别盘点并记录数量：`rpaRequestCount`（提交数）→ `recordsPersistedCount`/CSV 数（诊断结果已落盘数）→ `postprocessSucceededCount`（截帧与 summary 均完成数）。提交数不等于成功数；没有本地 CSV/records 时不得声称平台已成功或失败。

若用户未明确授权再次调用 RPA，先运行“修复保留产物”章节的 `repair_manifest.py`。该修复只读取已有 CSV/records 并补做本地后处理，不会发起 RPA；即使 manifest 的 `postprocessStatus` 已写成 `succeeded`，也会以实际 `summary.json`、`summary.md` 和 JPEG 是否完整为准，补跑缺失的截帧。检查 `repair-manifest.json` 的 `rpaCalls` 必须为 `0`，并核对修复后的三层数量。

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

该命令只执行 `CSV -> records`、`records -> 后处理` 和 `summary/JPEG -> manifest` 修复，不重新调用 RPA。若 manifest 缺失，再传入 `--top-materials-json`；无法证明诊断阶段时保持 `rpaStatus: unknown`。

检查保留的 `diagnosis-records/`、`diagnosis-csv/`、素材 `summary.json` 与 JPEG，而不是仅凭文件名判断成功。RPA 输出字段、下载上限和波次状态的完整约束见 [RPA 输出契约](rpa-output-contract.md)。
