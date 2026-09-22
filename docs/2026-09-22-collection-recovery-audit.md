# 千川素材采集与恢复优化记录

本次在 `codex/migrate-direct-material-data` 分支、`efdb907` 基线上修改技能源代码。针对最新采集复盘中的两个问题：采集未完成就进入后处理，以及重复恢复导致耗时增加。附件中的处理建议作为待验证线索，未执行其中的客户任务命令。

## 证据与根因

读取材料：`千川素材采集执行复盘报告 (1).md`、`千川素材采集Bash执行日志汇总.md`（Downloads 中的原附件）。

| 证据 | 判断与处理 |
| --- | --- |
| 复盘描述 24/30 时调用标准 resume，先下载、截帧；基线 `resume_standard_run` 确实先循环 repair，再调用采集引擎 | 是代码顺序缺陷。37 条 Bash 汇总未独立保留最早该次 resume 的完整命令，具体首次动作依赖复盘陈述；缺陷另以离线 24/30 用例验证。 |
| 日志 #25：此前进度 30/30，当前 manifest 后处理成功数却降为 4；#27 显示 26 条 summary 路径含重复运行目录前缀 | 相对 root 被重复拼接，已完成素材被误判缺失。统一绝对路径，并从规范素材目录核验已有 summary/JPEG 后恢复路径。 |
| 日志包含 240、100、200、240 秒四次 sleep，共 780 秒 | 存在 13 分钟显式等待，部分可能与真实后台工作重叠，不能全部计为可节省墙钟时间。优先使用 status 的动作与阶段提示，单次观察等待不超过 60 秒。 |
| 日志 #22 明确锁 PID 已死；#31–32 最终进入 analysis_pending，records/后处理均为 30 | 中断应恢复，不能持续等待已结束进程；最终采集闭合不代表此前异常退出都属于“后台交接成功”。 |
| 代码复查：正常引擎候选耗尽、有效数量不足时，仍会进入部分后处理 | 新增同一数量屏障，数量不足保留 records 并返回未完成，清除旧 ready 标记。 |

最近提交的衔接问题：`1fe165e` 优化了分阶段调度；`fc42668` 增加恢复入口中的两条一批本地修复循环，但该循环位于 RPA 恢复之前；`efdb907` 主要优化分析读取、表达与复核，并未改变这个恢复顺序。单纯继续缩短分析提示词无法解决本次采集问题。

## 已落地修改

标准恢复顺序统一为：一次 receipt/CSV/records/路径对账（零 RPA、零下载截帧）→ 批处理引擎续接已保存远程任务、补齐目标有效 records → 下载与 FFmpeg 队列 → 校验 → 事实包。

- `standard_workflow.py`：移除恢复入口的多轮后处理循环，仅调用一次 records-only repair；后续工作交给原有批处理引擎。
- `repair_manifest.py`：新增 `--records-only`；独立后处理须先核验目标数量的不同素材及其 records/videoUrl，目标未知或数量不足时阻止执行。连续修复支持运行目录相对路径、工作目录相对路径和绝对路径，已污染 summary 路径仅通过规范目录和实际产物校验恢复。
- `rpa_batch_runner.py`：候选耗尽但初始采集不足时返回未完成，不下载部分素材；原有后处理终态失败后的补位机制保留。
- `workflow_status.py`：直接给出 `nextAction`、`recoveryStage`、`targetTopN`、`rpaRemainingCount`，区分活跃任务等待、采集中断恢复、报告 finalize 接续和当前模型分析。
- `SKILL.md` 与两份运维契约：移除“先后处理再补 RPA”的旧说明，以及标准 prepare 前额外执行 detail/schema/validate 的重复步骤；明确恢复可提交尚未提交的候选，但不重提已提交素材。

`postprocessRecovery.rpaCalls=0` 仅指前置本地对账，不代表整个 resume 不调用 RPA。实际采集统计仍看 batch manifest。

## 哪些步骤可以省，哪些必须保留

源码 `scripts/` 仍是 36 个 Python 文件，本次没有新增运行脚本。文件数量本身不能证明分钟级耗时；明确存在的冗余是两套后处理调度、循环全批对账、路径错误导致的返工、重复排查与前置连接器探测。把全部模块合并成一个大脚本不会自动消除这些工作。

以 30 条都待后处理且每次处理 2 条为例，旧恢复入口需调用 repair 15 次，每次扫描全批；现在前置对账只调用 1 次，再由既有队列连续完成后处理。这是代码执行次数的变化，不能解读为整体提速 15 倍。RPA 默认并发 3、下载并发 2、FFmpeg 并发 2 保持原资源限制。

保留必要的 receipt/records 校验、远程 ID 复用、成功帧完整性、失败留痕、事实包与最终报告校验。不能为减少步骤而跳过这些证据门槛。

## 验证结果

新增 20 个参数化离线用例，覆盖 24/30 恢复顺序、数量不足/目标未知拦截、两次路径修复幂等与零重复视频工作、独立 CLI 退出码、状态动作与阶段提示。外部 RPA 和视频执行使用测试替身，磁盘上的 records/manifest/summary/JPEG 校验实际执行。

相关套件：`test_collection_recovery`、`test_repair_manifest`、`test_rpa_batch_runner`、`test_standard_workflow`、`test_skill_contract`，129 项通过。最终全量结果：696 passed、3 failed、1 skipped，另有 65 subtests passed；`git diff --check` 通过。

用 `git archive HEAD` 独立导出 `efdb907`，使用同一解释器执行全量基线：676 passed、3 failed、1 skipped，65 subtests passed。两边失败完全相同：

1. `test_prompt_requires_full_chapter01_fact_coverage`：旧提示词文字断言。
2. `test_report_materializes_persisted_video_as_relative_preview_reference`：报告预览视频内容与断言不一致。
3. `test_report_rejects_unreadable_preview_video`：预期解码拒绝未发生。

这些既有失败仍需另行处理；本次不能宣称整个报告链路全绿。

未运行在线 RPA、未修改历史客户运行目录、未同步平台安装副本或发布。线上节省分钟数尚未实测；长任务工具的会话句柄、进程回收和观测通道问题仍需运行框架侧处理，技能补丁不能代替该部分修复。
