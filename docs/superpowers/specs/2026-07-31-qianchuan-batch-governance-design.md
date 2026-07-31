# 千川批次状态治理与半成品修复设计

## 目标

为 Top-N 千川批次建立统一、可恢复、可修复的执行状态口径，避免把 RPA 获取成功、诊断记录落盘、后处理成功和最终报告可用混为一个“成功”。

## 范围

本次优化覆盖四个阶段状态：

1. `rpaSelected`：RPA 返回可解析 records、目标素材信息和非空 `videoUrl`。
2. `recordsPersisted`：规范化诊断 records 已以当前批次路径原子落盘。
3. `postprocessSucceeded`：视频下载、峰值抽帧和 summary JSON/Markdown 均成功。
4. `reportReady`：事实包、AI 结论和最终报告数据/HTML 均已通过对应验证；这是批次级状态，`reportReadyCount` 取值为 0 或 1。

manifest 顶层输出上述阶段数量，并继续保留候选、提交、selected、rejected、cancelled 数量和素材级状态。报告层只读取这些状态，不重新推断批次成功。

## 运行保护

每个批次使用唯一 `runId`，并在输出目录创建原子运行锁。锁记录 `runId`、PID、启动时间和 manifest 路径。已有有效锁时拒绝第二个进程；锁已失效且 manifest 存在 `running` 或 `submitted` 状态时，命令进入可识别的恢复路径，不能静默覆盖或创建同目录新批次。正常结束、失败收口和恢复完成后释放锁。

恢复仍复用现有 `--resume` 的批次绑定校验，禁止改变 shop、日期、目标 Top-N 或候选素材集合。锁机制只负责并发与陈旧运行识别，不假设远端 RPA 请求可以被本地重新接管。

## 半成品修复

新增 `repair-manifest` CLI 子流程，输入现有批次输出目录和候选/批次 manifest，只处理本地已存在的产物：

- 有 `diagnosis-csv/<materialId>.csv`、无 records：解析并原子写入规范化 records。
- 有 records、无完整 summary：复用现有后处理流水线，不重新请求 RPA。
- 有有效 summary/JPEG、manifest 缺失或状态过期：按文件和 summary 内容回填素材状态及路径。
- 修复后重新计算阶段计数，并运行本地批次验证；缺失或损坏产物保持明确失败状态。

repair-manifest 默认不修改原始 CSV、JPEG 或 summary，只写 manifest、records 和必要的修复收据；每次修复记录动作、素材 ID、输入产物和结果。

## 错误处理与兼容性

现有 schema v2 字段继续兼容。新增字段采用明确默认值，旧 manifest 可被读取但不能被误报为 `reportReady`。RPA 请求失败、后处理失败和报告验证失败分别保留，不通过补位掩盖后处理失败。

## 验收标准

- 同一输出目录不能被两个活跃进程同时运行。
- 中断后的陈旧批次能被识别并通过恢复/修复继续处理，不会静默覆盖 manifest。
- `batch-manifest.json` 和验证收据能同时表达四个阶段的数量。
- CSV、records、summary/JPEG 三类半成品均可在不重新调用 RPA 的情况下修复。
- 修复后的 manifest 可被现有最终报告流程消费；不完整产物不能被标记为 `reportReady`。
- 全量自动化测试通过。
