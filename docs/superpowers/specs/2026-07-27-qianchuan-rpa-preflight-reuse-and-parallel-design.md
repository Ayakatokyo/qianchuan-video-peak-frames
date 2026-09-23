# 千川 RPA 预检数据复用与并行补位设计

## 结论

本文件是本轮优化的完整设计入口，独立覆盖两个目标：预检阶段诊断数据复用，以及预检阶段受控并行补位。后续 implementation plan 和代码实现应以本文件为准，避免在多份设计之间来回拼接需求。

## 目标

本轮优化只覆盖两个目标：

1. 复用预检阶段已经获取到的诊断 CSV 和 normalized records；
2. 让 RPA 预检补位阶段支持素材间受控并行。

优化后，RPA 诊断连接器调用主要集中在预检阶段。正式截帧阶段默认读取本地 records，继续执行视频下载、FFmpeg 截帧、summary、batch manifest 和 verify。

## 背景

当前批量链路已经从“资产表 Top N 直接正式执行”调整为“资产表候选池 + RPA 预检补位 + ready list 正式执行”。

真实执行日志暴露了两个新的效率问题：

1. 预检阶段虽然调用的是正式诊断连接器，成功后已经能拿到诊断 CSV 或 records，但正式阶段仍会再次拉取同一素材诊断数据；
2. 技能文档提到 `--concurrency` 可控制 RPA 批量并行，但当前代码只在正式批次 `_run_rpa_batch()` 中使用线程池，`--preflight-only` 的 `_run_rpa_preflight()` 仍按候选素材串行执行。

因此，目标 Top N 较大或候选池较大时，预检会成为主要耗时点。日志中 Top 50 候选、目标 Top 9 的预检在 600 秒内只完成 3 个素材，符合当前串行预检实现的表现。

## 非目标

- 不改变资产表选材排序指标和候选池生成逻辑；
- 不把预检失败解释为“素材已删除”；
- 不在预检阶段下载视频、运行 FFmpeg 或生成 JPEG；
- 不把正式阶段的视频下载、截帧、summary、manifest、verify 移入预检；
- 不专门治理外部执行器超时后残留进程的问题；
- 不默认无限并发调用 RPA 网关。

日志中曾观察到 `--preflight-only` 之后出现 JPEG 产物。该现象不作为本轮独立优化目标，但本轮实现必须守住边界：预检路径自身不得调用正式素材处理函数。如果未来仍出现 JPEG，应按外部执行器重复启动、旧进程残留或进程树清理问题另行排查。

## 设计原则

### 1. 预检是唯一默认诊断拉取阶段

启用预检回执复用时，每个 ready 素材默认只在预检阶段调用一次 RPA 诊断连接器。正式截帧阶段读取本地 `diagnosis-records/<materialId>.json`。

### 2. 预检成功必须等于数据可复用

素材进入 `selectedMaterials` 前必须满足：

- RPA 返回可解析 records；
- records 中存在目标素材的 `material_info`；
- `material_info.videoUrl` 非空；
- normalized records 已成功写入本地；
- `rpa-preflight.json` 能引用该本地 records 路径。

只拿到 pass/fail 但没有本地 records 的素材不得进入 ready list。

### 3. 并行只改变调度，不改变排序

预检可以并发请求多个候选素材，但最终 `selectedMaterials` 和 `top_materials_rpa_ready.json.materials` 必须保持资产表候选池顺序。并发完成顺序不能影响 Top N 选择结果。

### 4. 有界提交，有界等待

预检并行不能一次性提交完整候选池。调度器应维持最多 `preflightConcurrency` 个 in-flight 任务，并在 selected 数量满足 `targetTopN` 后停止提交新的候选。

已经提交的 in-flight 任务可以完成并记录结果，但不得再扩大候选扫描范围。

### 5. 正式阶段消费本地事实

正式阶段传入 `--rpa-preflight-json` 时，默认不调用 `fetch_material_diagnosis()`。只有显式传入 `--allow-rpa-refetch`，并且本地 records 缺失、损坏或人工确认需要刷新时，才允许回退到 RPA 重新拉取。

## CLI 行为

### 预检阶段

推荐复用现有 `--concurrency` 参数作为预检并发数，避免新增两个相近概念的参数。语义调整为：

- `--concurrency` 在正式批次中控制素材处理并发；
- `--concurrency` 在 `--preflight-only` 或预检补位阶段控制 RPA 预检并发；
- 默认仍为 `1`，保持旧行为兼容。

示例：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --target-top-n 9 \
  --candidate-top-n 50 \
  --preflight-only \
  --date-range-type CUSTOM \
  --custom-start-date 2026-07-21 \
  --custom-end-date 2026-07-26 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --concurrency 3 \
  --output-dir "/path/to/run/peak-frames"
```

预检阶段必须写出：

- `peak-frames/rpa-preflight.json`
- `peak-frames/top_materials_rpa_ready.json`
- `diagnosis-records/<materialId>.json`
- `diagnosis-csv/<materialId>.csv`，仅 files envelope 场景

### 正式阶段

正式阶段显式消费预检回执：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/peak-frames/top_materials_rpa_ready.json" \
  --rpa-preflight-json "/path/to/run/peak-frames/rpa-preflight.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-07-21 \
  --custom-end-date 2026-07-26 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --ffmpeg-timeout-sec 120 \
  --concurrency 3 \
  --output-dir "/path/to/run/peak-frames"
```

新增或保留参数：

- `--rpa-preflight-json`
  - 指向预检回执；
  - 正式批次从中读取 `materialId -> diagnosisRecordsPath`；
  - 传入后默认禁止重新调用 RPA 诊断连接器。

- `--allow-rpa-refetch`
  - 默认关闭；
  - 仅在本地 records 缺失、损坏或人工明确要求刷新时使用；
  - 开启后可以回退到现有 RPA 拉取行为。

## 并行预检调度

### 输入

调度器输入：

- 候选素材列表，来自 `top_materials.json.materials`；
- `candidateTopN`，默认候选池全部；
- `targetTopN`；
- `preflightConcurrency`，来自 `--concurrency`；
- shopId、日期范围、diagnosis 目录和 per-material timeout。

### 状态

每个候选素材在预检回执中应能表达以下状态：

- `pending`：尚未提交；
- `running`：已提交，正在执行；
- `selected`：预检通过并可复用 records；
- `rejected`：预检失败；
- `cancelled`：因已满足目标 Top N，尚未提交或无需继续执行。

其中 `top_materials_rpa_ready.json` 只包含 `selected` 素材。

### 调度规则

1. 从候选池头部开始提交任务，最多保持 `preflightConcurrency` 个 running；
2. 任一任务完成后，记录结果并增量写 `rpa-preflight.json`；
3. 如果该素材预检通过，写 normalized records，再进入 selected；
4. 如果该素材失败，写 rejected 和失败原因；
5. 当 selected 数量达到 `targetTopN` 后，停止提交新的候选；
6. 对尚未提交的剩余候选记录为 `cancelled` 或不写入 rejected，但必须在 summary 字段中说明 stoppedAfterSatisfied；
7. 对已经 running 的任务，等待其完成到一个短 grace window，或按现有 per-material deadline 结束；
8. 最终按候选池原始顺序重建 selected 列表和 ready list。

### 排序要求

并发执行不能让“先返回”的低排名素材插队。例子：

- 候选顺序：A、B、C、D；
- 并发数：2；
- B 先通过，A 后通过；
- ready list 必须仍为 A、B。

如果 A 失败、B 通过、C 通过，则 ready list 为 B、C。

## `rpa-preflight.json` 契约

schema 升级为 `2`，同时包含诊断数据复用字段和并行预检可观测字段。

示例：

```json
{
  "schemaVersion": 2,
  "requestedTopN": 9,
  "candidateCount": 50,
  "candidateLimit": 50,
  "preflightConcurrency": 3,
  "shopId": "N9OMoRJG4y",
  "dateRange": {
    "type": "CUSTOM",
    "customStartDate": "2026-07-21",
    "customEndDate": "2026-07-26",
    "actualStartDate": "2026-07-21",
    "actualEndDate": "2026-07-26"
  },
  "selectedMaterials": [
    {
      "materialId": "7576501115602812938",
      "rpaAvailable": true,
      "failureReason": null,
      "videoUrl": "https://example.invalid/video.mp4",
      "diagnosisRecordsPath": "diagnosis-records/7576501115602812938.json",
      "diagnosisCsvPath": "diagnosis-csv/7576501115602812938.csv",
      "preflightStatus": "selected",
      "timingsSec": {
        "queuedAtSec": 0.0,
        "startedAtSec": 0.1,
        "finishedAtSec": 144.2,
        "totalSec": 144.1
      }
    }
  ],
  "rejectedMaterials": [
    {
      "materialId": "7646355189088714778",
      "rpaAvailable": false,
      "failureReason": "rpa_request_failed",
      "preflightStatus": "rejected",
      "timingsSec": {
        "queuedAtSec": 0.0,
        "startedAtSec": 0.0,
        "finishedAtSec": 300.1,
        "totalSec": 300.1
      }
    }
  ],
  "satisfied": true,
  "stoppedAfterSatisfied": true,
  "timingsSec": {
    "totalSec": 612.4
  }
}
```

字段说明：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `schemaVersion` | number | 使用 `2`，表示包含 records path 和并行观测字段 |
| `preflightConcurrency` | number | 本次预检实际使用的并发数 |
| `candidateLimit` | number | 本次最多扫描的候选数量 |
| `diagnosisRecordsPath` | string | 相对运行目录的 normalized records JSON 路径 |
| `diagnosisCsvPath` | string/null | files envelope 场景保留的原始 CSV 路径 |
| `preflightStatus` | string | `selected`、`rejected`、`running`、`cancelled` 之一 |
| `timingsSec` | object | 用于验证实际并行和定位慢素材 |
| `stoppedAfterSatisfied` | boolean | 达到目标 Top N 后是否停止提交新候选 |

## 数据落盘

预检通过时必须先持久化 records，再更新 selected：

1. RPA 返回 records 或 CSV；
2. files envelope 场景先保留 CSV；
3. 解析得到 records list；
4. 校验目标素材 `material_info.videoUrl`；
5. 原子写入 `diagnosis-records/<materialId>.json`；
6. 写入 `rpa-preflight.json.selectedMaterials[]`；
7. 重建 `top_materials_rpa_ready.json`。

如果第 5 步失败，该素材必须视为预检失败，不能进入 ready list。

## 正式阶段复用

正式阶段传入 `--rpa-preflight-json` 后，在下载视频前校验：

1. `rpa-preflight.json.satisfied=true`；
2. `schemaVersion=2`；
3. `preflightConcurrency` 仅作记录，不影响正式阶段并发；
4. ready list 中所有素材都存在于 `selectedMaterials`；
5. 每个 selected 素材都有可读 `diagnosisRecordsPath`；
6. records JSON root 是 list；
7. records 中存在目标素材 `material_info.videoUrl`；
8. 日期范围与当前 CLI 参数一致；
9. shopId 与当前解析后的 shopId 一致。

校验失败时，默认直接失败，不启动正式批次。只有显式传入 `--allow-rpa-refetch` 时，才允许回退到重新调用 RPA。

## 中断与恢复

预检阶段需要保留增量可恢复能力：

- 每个素材完成后立即写 `rpa-preflight.json`；
- 已 selected 且 records 校验通过的素材，后续重跑时可以直接复用；
- 已 rejected 的素材默认不重复预检，除非用户删除回执或显式要求刷新；
- `running` 状态只代表上次中断时未完成，重跑时应重新判定为可提交；
- 若发现 records 文件损坏，则该素材重新进入预检或标记失败。

并行预检的中断恢复不要求恢复 in-flight 进程，只要求恢复已落盘的确定结果。

## 实现边界

### `scripts/rpa_adapter.py`

调整：

- 预检函数返回 records 或 records path 所需信息，而不只返回 pass/fail；
- files envelope 场景继续保留 CSV；
- payload 场景也写 normalized records JSON；
- 错误分类保持 `rpa_request_failed`、`rpa_payload_invalid`、`missing_material_info`、`missing_video_url`。

### `scripts/extract_peak_frames.py`

调整：

- `_run_rpa_preflight()` 从串行循环改为受控并行调度；
- 复用 `--concurrency` 控制预检并发；
- selected 和 ready list 按候选池顺序输出；
- `rpa-preflight.json` 增量写入 selected、rejected、timings 和 preflightConcurrency；
- 预检成功后写 `diagnosis-records/<materialId>.json`；
- 增加 `--rpa-preflight-json` 和 `--allow-rpa-refetch`；
- `_process_rpa_material()` 支持从本地 records path 读取 records；
- `--preflight-only` 路径不得调用 `_process_rpa_material()`、`download_video()` 或 `process_records()`。

### `SKILL.md`

更新 TOP 素材流程：

1. 导出候选池；
2. 使用 `--preflight-only --concurrency 3` 做并行预检补位；
3. 检查 `rpa-preflight.json.satisfied`；
4. 正式阶段传入 `top_materials_rpa_ready.json` 和 `rpa-preflight.json`；
5. 正式阶段默认复用预检 records；
6. 正式阶段可继续使用 `--concurrency 3` 控制视频下载和截帧并发。

### `references/rpa-output-contract.md`

补充：

- `rpa-preflight.json` schemaVersion 2；
- 预检并发字段与状态字段；
- 预检成功即 records 可复用；
- 正式阶段复用 records 的校验规则；
- `--allow-rpa-refetch` 的使用边界。

## 测试要求

至少覆盖：

1. `--preflight-only --concurrency 3` 时最多 3 个预检任务同时运行；
2. 并发完成顺序不同于候选顺序时，ready list 仍按候选顺序输出；
3. 达到 `targetTopN` 后不再提交新的候选；
4. 已经提交的 in-flight 任务完成后不会破坏 selected 顺序；
5. 预检成功时写入 `diagnosis-records/<materialId>.json`；
6. files envelope 场景同时保留 CSV 和 normalized records JSON；
7. records 写入失败时素材不进入 selected；
8. `rpa-preflight.json` 记录 `preflightConcurrency` 和素材 timings；
9. 正式阶段传入 `--rpa-preflight-json` 时不调用 `fetch_material_diagnosis()`；
10. 正式阶段从 records JSON 读取 `material_info.videoUrl` 并继续下载视频和截帧；
11. `rpa-preflight.json.satisfied=false` 时正式阶段拒绝启动；
12. ready list 中素材不在 selectedMaterials 时拒绝启动；
13. 旧 schemaVersion 1 回执默认拒绝复用；
14. `--allow-rpa-refetch` 开启时允许回退到现有 RPA 拉取；
15. `--preflight-only` 下不会调用 `_process_rpa_material()`、`download_video()` 或 `process_records()`。

## 风险与取舍

- 并行预检会提高 RPA 网关瞬时压力，因此默认并发仍为 `1`，文档推荐先使用 `3`。
- 并行调度可能让完成顺序和候选顺序不一致，因此 ready list 必须按候选顺序重建。
- 达到 Top N 后，已经提交的 in-flight 请求可能仍消耗 RPA 调用；这是受控并行的代价。
- 预检复用 records 会牺牲“正式阶段重新获取最新诊断数据”的实时性，换取批次稳定性和更少 RPA 调用。
- 本轮只保证预检代码路径不生成 JPEG，不治理外部进程残留导致的异常产物。

## 成功标准

真实 Top N 批次应满足：

1. 预检阶段可以观察到最多 `--concurrency` 个素材同时处于 running；
2. Top N ready 素材顺序与资产表候选顺序一致；
3. 预检成功素材都有本地 records；
4. 正式阶段默认不再调用 RPA 诊断连接器；
5. 正式阶段仍生成 JPEG、summary、batch manifest；
6. `--verify` 只验证 ready 素材；
7. `--preflight-only` 本身不生成 JPEG；
8. RPA 调用次数约等于预检扫描候选数，而不是预检扫描数加正式 ready 数。
