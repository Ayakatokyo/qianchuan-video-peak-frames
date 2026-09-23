# 移除旧 RPA 预检与旧批处理路径设计

## 目标

将 RPA Top-N 执行入口收敛为唯一的波次批处理路径，删除旧预检回执和旧批处理兼容逻辑，降低运行语义与文档的复杂度。

## 保留的运行模型

标准流程继续从 `top_materials.json` 读取按排名排序的候选池。调度器每一波最多发起 `rpaConcurrency` 个 RPA 请求，默认值为 3；只有当前波次的全部请求达到终态后，才会提交下一波。RPA 成功、视频后处理和失败补位的既有契约保持不变。

RPA Top-N 路径固定使用波次屏障。`--rpa-concurrency` 与 `--postprocess-concurrency` 保留为可配置参数；不再提供关闭波次屏障的选项。

## 删除范围

删除以下不再由标准流程使用的兼容能力：

- CLI 参数 `--preflight-only`、`--rpa-preflight-json`、`--allow-rpa-refetch` 与 `--no-rpa-wave-barrier`。
- `scripts/rpa_preflight.py` 及其测试。
- `scripts/rpa_batch_runner.py` 中的 `run_legacy_batch()`。
- `scripts/extract_peak_frames.py` 中生成或读取预检回执、旧批处理、以及单素材 RPA 调试入口所需的辅助函数。
- 单素材 `--source rpa --material-id` 调试路径。

`--source local` 保持可用。`rpa_runtime.py`、`rpa_adapter.py`、波次调度器和当前 RPA 后处理实现继续保留。波次调度器会改用体现其真实语义的名称，避免 `streaming` 造成误解。

## CLI 边界

RPA 模式只支持 Top-N 波次运行：必须提供 `--top-materials-json`、`--target-top-n` 和日期范围参数，店铺 ID 继续通过 `--shop-id` 或既有环境配置提供。已删除的参数由 argparse 作为未知参数拒绝。

本地模式继续接受现有本地诊断记录和视频输入；它不调用 RPA，也不受这次兼容性收缩影响。

## 产物与恢复

波次路径持续写入 `batch-manifest.json`、`diagnosis-records/<materialId>.json`、可选的 `diagnosis-csv/<materialId>.csv` 和 `top_materials_rpa_ready.json`。已有的运行恢复依然以 manifest 与本地产物为依据；不会再接受旧预检回执作为新的运行输入。

## 测试与文档

先新增失败测试，覆盖：

- RPA Top-N 命令仍以默认 3 并发的完整波次调度。
- 四个已删除的兼容参数被 CLI 拒绝。
- 单素材 RPA 参数组合被拒绝，而本地模式仍保持可用。

实现后删除旧路径专属测试，保留并更新波次、补位、RPA 输出契约和标准工作流测试。同步更新 `SKILL.md`、`references/rpa-output-contract.md`、`docs/skill-flow.md` 与运维文档；历史设计记录保留为历史，不改写。

## 兼容性

这是有意的破坏性变更。依赖旧预检、预检回执复用、非波次调度或单素材 RPA CLI 的调用方必须迁移到标准 Top-N 波次入口。
