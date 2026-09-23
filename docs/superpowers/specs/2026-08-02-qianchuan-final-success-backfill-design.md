# 千川最终成功数补位与 RPA 超时治理设计

## 目标

Top-N 批次的完成条件改为 `postprocessSucceededCount >= targetTopN`。素材只有完成 RPA 诊断、records 落盘、视频下载、峰值截帧和两个 summary 后，才计入目标数量。

保留两个独立并发通道：RPA 继续按波次并行，后处理继续独立并行。任何单个 RPA 请求不得让整个波次等待超过 10 分钟。

## 已确认参数

- `rpaConcurrency=3`：每个 RPA 波次最多三个请求。
- `postprocessConcurrency=3`：与 RPA 线程池独立。
- RPA 观测软超时：8 分钟。仅记录慢请求、心跳和素材状态，不终止请求。
- RPA 硬超时：10 分钟。终止隔离子进程，记录 `rpa_request_failed`。
- late-artifact 回收窗口：30 秒。
- 后处理本地重试：最多一次，仅复用已落盘 records，不重新调用 RPA。

## 调度语义

1. RPA 波次屏障仍只约束 RPA：当前波所有 RPA 成员都完成、失败、硬超时或完成迟到产物回收后，才允许提交下一波。
2. 主调度器在等待 RPA future 的任何时刻都必须收集已完成的后处理 future，并原子写回 manifest；不得只在波次边界做 `timeout=0` 的一次性收集。
3. 每个 RPA selected 素材立即进入后处理队列，不等待下一 RPA 波次。
4. 每个后处理失败先在本地重试一次；重试仍失败后标记终态 `failed`。
5. 退出条件为成功后处理数达到目标，或候选池耗尽且没有 RPA/后处理任务在运行。仅 RPA selected 数达到目标不得结束批次。
6. 后处理失败和 RPA 失败都会释放一个成功名额；调度器从候选池中按原始 rank 提交后续素材补位。
7. 为防止过量 RPA 调用，下一波最多提交 `targetTopN - postprocessSucceeded - postprocessInFlight` 个素材，并受 `rpaConcurrency` 限制。RPA 波内失败在下一波补位。

## 状态与 manifest

素材 RPA 状态：`pending -> submitted -> selected | rejected | timed_out`。

素材后处理状态：`pending -> queued -> running -> succeeded | retrying -> failed`。

manifest 继续保留 RPA selected 与后处理成功的独立计数，并新增或明确以下可观测字段：

- `rpaStartedAtSec`、`rpaSoftTimeoutAtSec`、`rpaFinishedAtSec`、`rpaHardTimeoutExceeded`；
- `postprocessAttemptCount`、`postprocessStartedAtSec`、`postprocessFinishedAtSec`；
- `lastStateChangedAtSec`、`lastProgressAtSec`；
- 批次级 `postprocessInFlightCount`、`remainingSuccessfulCount`。

每次 RPA 或后处理 future 完成、超时、重试或被提交时均原子持久化 manifest。`postprocessSucceededCount` 只由通过 summary/JPEG 校验的终态素材计算。

## 恢复

`--resume` 先扫描已有 records 和有效 summary：有效 summary 直接恢复为 `postprocessSucceeded`；有 records 但无有效 summary 的素材进入本地后处理重试；无 records 的 `submitted`/`running` 素材重新进入 RPA 候选队列。恢复后仍以最终后处理成功数作为补位依据。

## 失败处理

- RPA 硬超时、RPA 拒绝：记录原因，在下一波从下一 rank 补位。
- 后处理首次失败：保留 records，进行一次本地重试。
- 后处理二次失败：保留错误、产物和 RPA selected 状态，在后续波次补位。
- 候选池耗尽：返回非零，输出最终成功数、失败列表与未满足数量。

## 验收标准

- 一个永久挂起的 RPA 请求在 10 分钟和 30 秒回收窗口内结束，不阻塞后续波次。
- 8 分钟时记录慢请求，但正常在 10 分钟前返回的请求仍可成功。
- RPA 等待期间完成的后处理会立即回填 manifest，不再出现有效 JPEG/summary 与 `postprocessSucceeded=false` 长期不一致。
- 首批中出现后处理失败时，候选素材按 rank 补位，直到 20 个 `postprocessSucceeded` 或候选耗尽。
- RPA 和后处理任务可同时处于运行状态，RPA 波次之间不重叠。
- `--resume` 不重复 RPA 调用已有有效 summary 的素材，并能将陈旧 `submitted` 条目重新调度。
