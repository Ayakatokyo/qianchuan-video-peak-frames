# 千川素材分析运维手册

仅在标准工作流失败且需要排障时使用；不要把这些命令作为常规报告流程。常规报告始终从 `scripts/run_standard_analysis.py prepare` 开始，并以 `finalize` 收口。

在启动 AI 分析前，先运行：

```bash
python scripts/run_standard_analysis.py status --output-dir "/path/to/run"
```

仅当状态为 `analysis_pending`，并且 `diagnostic-analysis-data.json` 与 `diagnostic-analysis-prompt.md` 都由成功的 `prepare` 生成时，才可继续。不得从 `top_materials.json` 或手写 `missingRanks` 组装事实包。

## 运行中断或陈旧批次

先读取运行目录的 `run-summary.json` 和 `peak-frames/batch-manifest.json`。若 manifest 中仍有 `running` 或 `submitted` 状态，不能直接覆盖目录。恢复已有批次时使用：

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

## 修复保留产物

当 CSV、records、summary 或 manifest 不一致时，执行：

```bash
python scripts/repair_manifest.py \
  --output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --diagnosis-csv-dir "/path/to/run/diagnosis-csv"
```

该命令只执行 `CSV -> records`、`records -> 后处理` 和 `summary/JPEG -> manifest` 修复，不重新调用 RPA。若 manifest 缺失，再传入 `--top-materials-json`；无法证明诊断阶段时保持 `rpaStatus: unknown`。

## 单素材 RPA 调试

仅用于已知素材的协议或视频问题，不可替代资产表选材：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --material-id "7647499280192372763" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-07-19 \
  --custom-end-date 2026-07-25 \
  --output-dir "/path/to/debug-output"
```

检查保留的 `diagnosis-records/`、`diagnosis-csv/`、素材 `summary.json` 与 JPEG，而不是仅凭文件名判断成功。RPA 输出字段、下载上限和波次状态的完整约束见 [RPA 输出契约](rpa-output-contract.md)。
