# 千川标准工作流并发与 RPA 收集治理设计

## 目标

在不依赖上层 Agent 或调度器改造的前提下，让同一运行目录的标准工作流具备以下保证：

1. 同一 `output-dir` 同时只能有一个 `prepare` 或 `finalize` 写入运行状态和产物。
2. 目标数量的 RPA 已取得已验证 CSV 并完成 records 落盘后，不能再因后处理尚未结束而提交候补素材 ID。
3. 只有后处理重试和串行补偿均失败时，才能按缺口数量请求候补素材；每个候补请求必须保留补位原因。
4. AI 事实包只能从 `top_materials_rpa_ready.json` 构建，且只能在工作流状态为 `analysis_pending` 时进入 AI 分析。

不在本次范围内：修改宿主 Agent、调度平台、RPA 服务接口，或自动调用外部大模型。

## 现状与问题

`extract_peak_frames.py` 已在 RPA 批处理期间持有 `.batch-run.lock`。但标准入口在运行 selection 时未持有覆盖整个流程的锁；第二次 `prepare` 可以先在 selection 失败，再由失败处理覆盖已有 `run-summary.json`。

RPA 流式调度当前以 `postprocessSucceededCount` 判断剩余容量。RPA 已经返回并落盘、但素材仍在下载视频或抽帧时，该计数仍小于目标，调度器可能继续向 RPA 提交候补 ID。

## 设计

### 1. 标准工作流锁与状态查询

在运行根目录新增 `.standard-workflow.lock`。`prepare` 在 selection 前获取该锁，并一直持有到事实包写入或失败摘要写入完成；`finalize` 在读取摘要前获取相同锁，并持有到报告产物和最终摘要写入完成。

锁使用现有 `batch_runtime.acquire_run_lock` 的原子创建和进程存活语义。锁关联 `run-summary.json`，并包含 `runId`、进程 ID、主机名、启动时间和摘要路径。

- 活动锁存在时，新的 `prepare` 或 `finalize` 返回明确的 `workflow is already running` 错误码。
- 因锁冲突退出的调用不得创建、更新或覆盖 `run-summary.json`、候选清单、manifest 或 AI 目录。
- 陈旧锁不自动删除；调用方只能通过只读 `status` 命令确认状态，再按运维手册执行恢复。这样不会把仍在运行但暂时失去可观测性的任务误判为已中断。
- 新增 `run_standard_analysis.py status --output-dir PATH`。该命令只读取 `.standard-workflow.lock`、`run-summary.json` 和 `peak-frames/batch-manifest.json`，输出一个 JSON 对象，包含 `workflowStatus`、`activeStage`、`lock`、`rpaAcceptedCount`、`postprocessSucceededCount` 和 `reportReadyCount`，不修改文件。

锁持有者在每个阶段转换时原子写入 `run-summary.json` 的运行状态：`running` 加 `activeStage`，阶段依次为 `selection`、`diagnosis_batch`、`verification`、`facts`、`report`。成功状态仍为 `analysis_pending` 或 `complete`；失败状态仍为 `failed`，并保留失败阶段和信息。

### 2. 两阶段 RPA 调度

RPA 运行保留现有波次与并发下载/抽帧能力，但分为两个明确阶段。

**阶段 A：RPA 收集**

- 一条素材仅在当前调用产生已验证 CSV、records 成功落盘后才计入 `rpaAcceptedCount`，对应 `rpaStatus=selected`、`rpaCsvVerified=true`、`recordsPersisted=true`。
- 后处理状态不参与阶段 A 的候补提交判断。
- 当 `rpaAcceptedCount == targetTopN`，停止提交任何未请求候选素材。尚未完成的后处理任务继续执行。
- RPA 请求本身最终失败、CSV 未验证、records 无法物化、或重试后仍缺少必要媒体字段时，不计入 `rpaAcceptedCount`，并在阶段 A 内请求下一个候选素材。

**阶段 B：后处理收口与补位**

- 等待阶段 A 已接收素材的并行后处理完成；保留当前的并行重试和串行补偿。
- 若 `postprocessSucceededCount == targetTopN`，生成 ready 清单并取消剩余未请求候选。
- 只有某条已接收素材完成所有后处理恢复后仍为 `failed`，才计算缺口并请求同等数量的下一候选素材。补位 RPA 请求最多为当前缺口数，且必须等待该批候补的后处理与补偿完成后再计算下一轮缺口。
- 每个因阶段 B 提交的候选在 manifest 中写入 `backfillReason=postprocess_failed_after_retries`；因阶段 A 的 RPA 失败补位写入 `backfillReason=rpa_collection_failure`。未请求的尾部候选保持 `rpaStatus=cancelled`、`failureReason=target_satisfied`。

这使得“RPA 请求数”与“可交付素材数”分开：正常成功时前者恰好等于 `targetTopN`；后处理真实失败时，前者才可能超过目标数量，且每一条额外请求可追溯。

### 3. Manifest、ready 清单与事实包契约

在 streaming manifest 顶层新增：

```json
{
  "rpaCollection": {
    "status": "collecting | complete | backfilling",
    "targetCount": 18,
    "acceptedCount": 18,
    "primaryCollectionCompletedAtSec": 123.45
  }
}
```

`stageCounts` 保留 `rpaSelectedCount` 和 `postprocessSucceededCount`，但新增 `rpaAcceptedCount` 与 `rpaRequestCount`，避免将已选中、已请求和已完成后处理混为同一含义。

`top_materials_rpa_ready.json` 只能由 `postprocessStatus=succeeded` 的最终前 `targetTopN` 素材生成。`prepare_diagnostic_analysis` 和 `finalize` 只读取此 ready 清单；若其不存在、数量不足或与 manifest 的成功素材集合不一致，标准工作流失败并保留可诊断状态，不能从 `top_materials.json` 自动降级或拼接事实包。

### 4. AI 阶段门禁

`prepare` 仅在 ready 清单、验证回执、事实包和提示词全部成功写入后，把摘要改为 `analysis_pending`。AI 调用约束为：

```text
prepare exit code == 0
AND run-summary.status == analysis_pending
AND diagnostic-analysis-data.json exists
AND diagnostic-analysis-prompt.md exists
```

否则不得创建 `diagnostic-ai-analysis.json`，也不得调用 `finalize`。技能文档将明确禁止扫描候选池或手工指定 `missingRanks`；事实包中的素材集合是唯一分析范围。

## 失败与恢复

- 活动锁：调用 `status`，等待锁释放，不重试 `prepare`。
- 陈旧锁或中断：调用 `status`，按运维手册核验 CSV、records、manifest 和峰值帧；恢复操作不得新发 RPA，除非 manifest 的恢复策略明确要求。
- 后处理失败：先完成并行重试和串行补偿；仅在两者均失败后才进入受限补位。
- AI JSON 不合格：维持 `analysis_pending`，只允许修正 AI JSON 并重试 `finalize`。

## 验收标准

1. 活动 `prepare` 期间对相同目录发起第二个 `prepare`，第二个调用失败，且摘要字节内容和修改时间不变。
2. `targetTopN=2`、前两条 RPA 均成功、第一条后处理延迟时，只提交前两条 ID，不提交第三条。
3. 前两条 RPA 成功、其中一条在所有后处理恢复后失败时，恰好请求一条候补，并在 manifest 中记录 `postprocess_failed_after_retries`。
4. RPA 本身失败时，阶段 A 请求候补并记录 `rpa_collection_failure`；RPA 已接收数量达到目标后，不因后处理尚未完成而继续请求候补。
5. `status` 对活动锁、陈旧锁、`analysis_pending` 和 `complete` 都是只读的，且返回与 manifest/摘要一致的计数。
6. AI 事实包中的素材 ID 集合严格等于 ready 清单和最终成功 manifest 集合；候选池中的其他素材不能出现。

