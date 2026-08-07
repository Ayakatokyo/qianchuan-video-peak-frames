# 波次 RPA 中断恢复设计

## 目标

在不恢复旧预检、旧批处理、流式或单素材执行路径的前提下，使当前
Top-N 波次 RPA 工作流在进程中断后可继续执行。恢复不得重跑已有有效
CSV、records 或后处理结果；每波最多保持 3 个 RPA 并行，且波次屏障不变。

## 范围

本设计只修改本地技能运行时的 manifest、receipt、恢复和受控中断语义。

- 保留 `rpaConcurrency=3` 和 `waveBarrier=true` 的默认值。
- 保留现有 CSV 原子下载、records 持久化、回执和后处理路径。
- 不恢复已删除的预检、流式或单素材 RPA CLI。
- 不增加平台接口，也不把本地 `submitted` 推断为平台失败。
- 不在恢复时删除 CSV、records、receipt、manifest 或工作锁。

平台尚未提供按 `clientAttemptId` 或网关 request ID 查询结果的接口。因此，
本地无任何有效产物的已提交请求只能被记为“远端结果未知”；恢复后对该
素材重新提交是有意且受审计的降级，不是平台失败结论。

## 问题根因

当前调度器在提交波次时持久化 `waveFinishedAtSec: null`，但 manifest
schema 把该字段定义为非空数字。因此进程在波次完成前中断时，`--resume`
会在对账前拒绝自己的中间状态。

回执恢复还将 `submitted` 与“必须存在 `records_persisted` receipt”绑定。
实际中断恰好会留下合法的 `submitted` receipt、CSV receipt 或没有本地产物
的 submitted manifest 条目。这些不是损坏数据，必须进入分类恢复流程。

## 状态模型

每个素材同时有调度状态和本地事实状态。调度状态不能替代本地事实。

| 调度状态 | 可信本地事实 | 恢复动作 |
| --- | --- | --- |
| `pending` | 无 | 排入未完成波次 |
| `submitted` | 有效 `records_persisted` receipt | 复用 records，排入后处理 |
| `submitted` | 有效 CSV、无 records | 解析 CSV 并持久化 records，排入后处理 |
| `submitted` | 有效 records、无 CSV receipt | 复用 records，排入后处理 |
| `submitted` | 无有效本地产物 | 保留原 attempt 审计，重置为 `pending`，仅重提该素材 |
| `selected` | 有效 records、无有效 summary | 仅重试本地后处理 |
| `selected` | 有效 records 和 summary/JPEG | 标记后处理成功，不调用 RPA |
| `rejected` / `cancelled` | 任意 | 保持终态；后续候选按既有补位规则处理 |

“有效 CSV”必须通过当前 material ID、常规文件、非空、可解析的诊断 CSV
验证。“有效 records”和“有效 summary/JPEG”继续复用现有验证函数，不以
路径存在作为成功依据。

## Manifest 与 Receipt 契约

### 新写入

- 波次提交时只写 `waveIndex`、`waveSize` 和 `waveStartedAtSec`。
- `waveFinishedAtSec` 与 `waveCsvArtifactFinishedAtSec` 只在整波结束时写入。
- 收到中断时，将批次级 `interruption` 写入 manifest，包含原因、时间和
  尚未完成的素材 ID；在途素材保持 `submitted`。
- 对“无本地产物而重新提交”的素材，在 manifest 的恢复记录中保留旧
  `clientAttemptId`、receipt state、恢复时间和原因 `local_artifact_unavailable`。

### 向后兼容

读取旧 manifest 时，只允许把可选波次完成时间的 `null` 规范化为“字段缺失”。
其他字段的 `null` 或非法值仍是 schema 错误。这样历史中断目录可恢复，但不
会放宽完整 manifest 的数据校验。

每个素材继续使用当前单文件 receipt。发起新的 RPA 调用可以刷新该 receipt，
但旧 attempt 的最小审计证据必须先追加到 manifest，恢复过程不得删除 receipt。

## 恢复流程

`--resume` 的顺序固定如下：

1. 获取恢复锁并读取、规范化 manifest。
2. 对所有素材执行本地对账，形成“复用后处理”“重建 records”“重新提交”三类队列。
3. 原子写回完成的对账结果及恢复审计记录。
4. 先处理可复用 records 的本地后处理；它不会开启新的 RPA 波次。
5. 按原候选 rank 调度未完成的 RPA 素材，每波不超过 3 个，并等待全波终态。
6. 在最终成功数达到 Top-N 后，沿用当前取消未提交候选和批次验证逻辑。

恢复不能启动新的标准 `prepare` 目录，不能对已验证 records/summary 再提交
RPA，也不能手工改写或清理 manifest 作为常规步骤。

## 受控中断与锁

CLI 和批次调度器接收 `SIGTERM` 或 `SIGINT` 时：停止开启新波次，在当前
checkpoint 将 manifest 写为 `interrupted`，并留下在途素材的 `submitted`
状态。运行时在有限的收尾窗口内结束或终止本地 worker，再释放属于当前
`runId` 的锁。未受控终止留下的锁仍可通过 `--resume` 处理。

锁的身份必须同时校验 PID、主机和进程启动标识；仅凭 PID 存活不能判断锁
仍属于原运行。状态查询保持只读，运行中的任务禁止使用 `pkill`、删除锁或
删除 receipt 作为恢复方式。

## 验收测试

实现前先写下列故障注入测试，并确认当前代码失败：

1. 波次提交后中断，manifest 含历史 `waveFinishedAtSec: null`，`--resume`
   可加载并仅恢复未完成素材。
2. `submitted` + `records_persisted` receipt 不调用 RPA，直接进入后处理。
3. `submitted` + 有效 CSV 但无 records 时只生成 records 和后处理，不调用 RPA。
4. `submitted` + 无本地产物时只重提该素材，已完成素材的调用次数保持零。
5. 恢复后的任意时刻 RPA 在途数不超过 3，下一波在前一波结束前不启动。
6. 收到受控 `SIGTERM` 后 manifest 可读取、锁可恢复、无人工 JSON 修复。

代码测试全部通过后，以新的独立目录执行一次真实 Top3：在第一波仍运行时
受控终止，再执行 `--resume`。验收为已有产物未产生新 RPA 请求、剩余素材
完成、`batch-verification.json` 为 `valid=true`。

## 非目标与后续

本地恢复不能确定远端平台是否已经完成一个无本地产物的 submitted 请求。
后续平台侧应提供按 `clientAttemptId` 或网关 request ID 查询任务结果的接口；
届时恢复先查询远端，再决定是否重提。该接口不是本设计的前置条件。
