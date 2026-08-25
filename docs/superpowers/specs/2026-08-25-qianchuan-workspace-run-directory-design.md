# 千川工作空间运行目录设计

## 目标

让完整素材分析的所有运行产物落在调用方工作目录，而非技能安装目录：平台执行时写入当前对话工作空间，本地 Codex 执行时写入本地技能仓库。原始 CSV、结构化 JSON、RPA CSV/records、JPEG 高光帧、事实包和最终报告必须归属同一个运行目录。

## 已确认的运行环境

平台 Bash 的当前工作目录是本次对话工作空间根目录，并且是文件工具相对路径的基准。技能脚本不会自行选择运行根目录，只按 `--output-dir` 写入文件。因此目录策略由技能调用命令决定。

## 设计

标准流程所有命令均显式使用：

```bash
--output-dir "$PWD/runs/<run-id>"
```

`run-id` 由执行方一次生成，格式为 `<YYYYMMDD>-<shop-id>-<advertiser-id>-<start-date>-<end-date>-top<top-n>`；`prepare`、`status`、AI 产物写入和 `finalize` 必须复用完全相同的路径。

不修改 CLI 的 `--output-dir` 必填契约，也不在脚本内检测 `.system` 或平台环境变量。这样本地、平台和恢复流程都只有一个明确且可验证的目录来源：调用方的当前工作目录。

## 落盘与可见性

- 平台：`$PWD/runs/<run-id>` 位于对话工作空间，过程数据可在该工作空间中查看，不进入 `.system/skills/...`。
- 本地 Codex：从技能仓库启动命令时，`$PWD/runs/<run-id>` 即仓库现有 `runs/` 目录，保持本地测试习惯。
- 最终交付仍按平台归档流程复制到 `files/`；这不替代或移动运行目录中的审计产物。

## 失败与恢复

报表下载、RPA 或报告阶段失败时，已写入的产物和 `run-summary.json` 保留在同一 `$PWD/runs/<run-id>`。恢复、`status` 和 `finalize` 必须传入该原路径，禁止重建到技能目录或新路径。

## 验证

测试覆盖技能文档中的标准 `prepare`、`status` 和 `finalize` 命令均使用 `$PWD/runs/<run-id>`，且不再包含 `<skill-root>/runs` 或 `.system/skills`。现有工作流测试继续验证下游全部产物均相对传入的 `--output-dir` 落盘。
