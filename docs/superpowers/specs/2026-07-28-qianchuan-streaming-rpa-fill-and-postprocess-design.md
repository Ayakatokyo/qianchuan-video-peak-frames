# 千川流式 RPA 补位与成功即后处理设计

## 结论

本设计将 TOP 素材批处理从“先预检候选池，再正式执行 ready list”调整为“正式批处理内的流式 RPA 补位队列”。RPA 诊断拉取不再被视为轻量预检，而是每个素材正式处理的第一步。

目标 Top N 时，系统先从资产表按整体消耗降序导出约 `3N` 个候选素材。调度器只按降序持续保持最多 3 个 RPA 任务在跑；RPA 成功的素材立即进入后续抽帧、人群和脚本分析；RPA 失败或无数据的素材记录失败原因并从后续候选中补位。候选池是补位上限，不代表一次性执行 `3N` 个 RPA 任务。

## 背景

当前流程中的 `preflight` 已经真实调用 RPA 诊断连接器，并会下载和解析完整诊断 CSV。CSV 中已经包含：

- `interaction_series`
- `material_info`
- `delivery_material_detail`
- `script_analysis`
- `crowd_analysis`

因此，当前 `preflight` 与真实执行的差异只剩下：成功素材是否继续下载视频、抽峰值帧、生成素材摘要和最终报告。继续把这一步命名为预检会导致两个问题：

1. 容易误以为它只是轻量可用性检查；
2. 容易把 `3N` 候选池理解成要全部执行 RPA。

真实业务口径是：Top N 表示最终需要分析 N 个 RPA 可用素材；候选池只是当高排名素材 RPA 失败时的补位来源。

## 目标

1. 资产表阶段按整体消耗降序导出约 `3N` 个候选素材。
2. RPA 阶段严格按候选排序提交素材。
3. RPA 最多同时并发 3 个任务，默认不一次性提交完整候选池。
4. RPA 成功素材立即进入后处理，后处理可以和后续 RPA 并行。
5. RPA 失败或无数据素材不进入后处理，只记录失败原因并触发候补。
6. 当 Top N 个 RPA 成功素材的后处理全部完成后，批次结束。
7. 报告和 manifest 清楚区分 candidate、submitted、selected、rejected、cancelled。

## 非目标

- 不改变资产表排序指标，本轮仍使用整体消耗降序。
- 不要求一定凑满 Top N；如果 `3N` 候选池耗尽仍不足 N 个成功素材，批次应失败并保留已有产物。
- 不让低排名素材因 RPA 返回更快而插队进入 selected。
- 不把 RPA 失败解释成素材删除；只记录实际失败原因。
- 不在本轮设计新的轻量 RPA availability API；除非平台另有接口，否则诊断拉取就是 RPA 阶段。
- 不默认扩大 RPA 并发到 3 以上。

## 核心语义

### Candidate

资产表导出的候选素材，按整体消耗降序排列。默认候选数量为 `targetTopN * 3`，也可通过 CLI 参数覆盖。

### Submitted

已经提交给 RPA 诊断连接器的素材。Submitted 数量可能大于 Top N，但只应大到足够补位，不应默认等于候选池大小。

### Selected

RPA 成功且具备后续处理所需数据的素材。素材进入 selected 前必须满足：

- RPA 返回可解析诊断数据；
- 诊断数据包含目标素材的 `material_info`；
- `material_info.videoUrl` 非空；
- normalized diagnosis records 已写入本地；
- 如有 files envelope，原始诊断 CSV 已保存。

### Rejected

已提交 RPA 但未能进入 selected 的素材。失败原因包括但不限于 RPA 请求失败、payload 不可解析、缺少 `material_info`、缺少 `videoUrl`。

### Cancelled

候选池中尚未提交 RPA，且因 selected 已达到 Top N 或批次停止而无需继续执行的素材。

## 调度模型

调度器维护两个相互独立但协同的通道：

1. RPA 通道
   - 最大并发数默认 3；
   - 只负责调用 RPA、下载或解析诊断 CSV、写 normalized records；
   - 按候选池降序提交；
   - 只有当 selected 数量小于 Top N 时继续补位。

2. 后处理通道
   - 接收 RPA 成功素材；
   - 执行视频下载、峰值帧抽取、素材 summary、人群分析和脚本分析事实整理；
   - 可与 RPA 通道并行；
   - 后处理失败应记录在该素材 manifest 中，不应伪装成 RPA 失败。

## 调度规则

1. 初始化时从 Rank 1 开始提交素材，最多提交 3 个 RPA 任务。
2. 任一 RPA 任务完成后，立即记录该素材状态。
3. 如果 RPA 成功：
   - 写入 diagnosis CSV 和 normalized records；
   - 按候选池顺序计入 selected；
   - 立即提交该素材到后处理通道。
4. 如果 RPA 失败或无可用数据：
   - 写入 rejected；
   - 不进入后处理。
5. 只要 selected 数量仍小于 Top N，并且 RPA in-flight 数量低于 3，就继续提交下一个候选素材。
6. 当 selected 数量达到 Top N 后，不再提交新的 RPA 任务。
7. 已经在跑的 RPA 任务不应影响 Top N 口径；实现可以选择短暂等待并记录为 rejected/extra，也可以取消并记录为 cancelled，但不得把它们追加进 selected。
8. 当 Top N 个 selected 素材的后处理全部完成后，批次结束。
9. 如果候选池耗尽后 selected 仍不足 Top N，批次以非零状态退出，并保留 manifest、诊断数据和失败原因。

## 排序要求

并发只影响执行时间，不影响业务排序。

Selected list 和最终报告展示顺序必须按资产表候选顺序重建。例如：

- 候选顺序：A、B、C、D；
- RPA 并发：3；
- C 最先成功，A 第二个成功，B 失败，D 后续补位成功；
- selected 顺序必须是 A、C、D，而不是 C、A、D。

## 状态与产物

批次 manifest 应至少表达：

- `targetTopN`
- `candidateCount`
- `candidateLimit`
- `rpaConcurrency`
- `postprocessConcurrency`
- `submittedMaterials`
- `selectedMaterials`
- `rejectedMaterials`
- `cancelledMaterials`
- 每个素材的 `rank`、`materialId`、`materialName`、`totalCost`
- 每个素材的 RPA 状态、后处理状态、失败原因和 timings

建议继续保留以下产物：

- `top_materials.json`：资产表候选池；
- `diagnosis-csv/<materialId>.csv`：RPA 原始诊断 CSV；
- `diagnosis-records/<materialId>.json`：normalized records；
- `peak-frames/<materialId>/summary.json`；
- `peak-frames/<materialId>/*.jpg`；
- `batch-manifest.json`；
- `batch-verification.json`；
- `final-report/final-report-data.json`；
- `final-report/final-report.html`。

`top_materials_rpa_ready.json` 可以作为兼容产物继续写出，但语义应改为“本次流式队列 selected Top N”，而不是独立预检阶段输出。

## CLI 行为

推荐新增或调整为一个正式批处理入口，避免用户必须手动分两步执行预检和正式处理。

示例：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --target-top-n 9 \
  --candidate-top-n 27 \
  --date-range-type CUSTOM \
  --custom-start-date 2026-07-20 \
  --custom-end-date 2026-07-26 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --rpa-concurrency 3 \
  --postprocess-concurrency 3 \
  --output-dir "/path/to/run/peak-frames"
```

兼容策略：

- 如果暂不新增 `--rpa-concurrency`，可沿用 `--concurrency` 表示 RPA 并发，并让后处理复用同一并发数；
- `--preflight-only` 应逐步降级为诊断调试入口，不再作为推荐批处理路径；
- 文档中必须说明 `--candidate-top-n 27` 是候选上限，不是 RPA 调用数量承诺。

## 错误处理

- RPA 请求超时：记录 `rpa_request_failed` 或更具体的 timeout reason，继续补位。
- RPA payload 不可解析：记录 `rpa_payload_invalid`，继续补位。
- 缺少 `material_info`：记录 `missing_material_info`，继续补位。
- 缺少 `videoUrl`：记录 `missing_video_url`，继续补位。
- 视频下载失败：记录后处理失败，不继续抽帧。
- 抽帧失败：记录后处理失败，并让批次最终非零退出。
- 候选池耗尽：如果 selected 或后处理完成数不足 Top N，批次非零退出。

## 验证要求

测试应覆盖：

1. 目标 Top 9、候选 27、RPA 全成功时，只提交前 9 个素材。
2. 前 9 个中失败 2 个时，只继续提交 Rank 10 和 Rank 11 补位，不提交 Rank 12 之后。
3. RPA 并发为 3 时，任意时刻 in-flight RPA 不超过 3。
4. RPA 成功后立即提交后处理，不等待当前 3 个 RPA 全部结束。
5. 低排名素材先完成时，selected 和报告顺序仍按资产表排序。
6. RPA 失败素材不进入后处理。
7. 候选池耗尽仍不足 Top N 时，保留部分产物并返回非零。
8. manifest 能区分 submitted、selected、rejected、cancelled。

## 迁移说明

现有“预检 + ready list + 正式执行”的实现可以分阶段迁移：

1. 先抽出统一的 per-material RPA 诊断获取函数，明确它是正式处理第一步；
2. 再新增流式调度器，替代 `_run_rpa_preflight()` 对完整候选池的预检语义；
3. 后处理复用现有单素材视频下载、峰值抽帧和 summary 逻辑；
4. 最后更新 `SKILL.md`，把推荐流程改成单次流式批处理命令。

迁移完成后，用户输入“Top 素材数量是 9”时，系统行为应稳定解释为：按消耗降序从候选池中流式补位，最终最多产出 9 个 RPA 成功并完成后处理的素材；不会无条件执行 27 个 RPA 任务。
