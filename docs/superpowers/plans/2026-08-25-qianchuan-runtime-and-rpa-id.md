# 千川运行环境门禁与 RPA ID 隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确保技能所有执行入口先完成依赖检查，并修复报表店铺 ID 被错误传入 RPA 的问题。

**Architecture:** CLI 先解析命令并执行依赖 bootstrap，再延迟加载完整工作流模块。标准工作流继续把用户店铺 ID传给报表选择，但诊断批处理省略 `--shop-id`，由既有授权账户解析逻辑选择唯一千川 ID。

**Tech Stack:** Python 3、`unittest`、现有 `dependency_bootstrap.py`、RPA 授权列表客户端。

---

### Task 1: 全流程依赖门禁

**Files:**
- Modify: `tests/test_standard_workflow.py`
- Modify: `scripts/run_standard_analysis.py`
- Modify: `SKILL.md`

- [ ] **Step 1: 写失败测试，验证 CLI 导入阶段不加载工作流。**

测试将模拟缺少依赖，并验证 `ensure_prepare_dependencies()` 在工作流加载前被调用；安装失败时返回 `2` 且不触发工作流函数。

- [ ] **Step 2: 运行测试确认当前顺序失败。**

Run: `python -m unittest tests.test_standard_workflow.StandardWorkflowCliTests.test_dependency_bootstrap_runs_before_workflow_import -v`

- [ ] **Step 3: 延迟加载工作流模块并为所有命令执行依赖预检。**

移除 `run_standard_analysis.py` 顶层 `standard_workflow` 导入；在解析命令后先调用 `ensure_prepare_dependencies()`，再导入并调用对应工作流函数。`--help` 和参数解析错误不安装依赖。

- [ ] **Step 4: 更新技能文档。**

在 `SKILL.md` 的标准流程第一步明确：所有命令执行前检查 `requests`、`imageio-ffmpeg`，缺失先安装，失败停止。

- [ ] **Step 5: 运行 CLI 和依赖回归测试。**

Run: `python -m unittest tests.test_dependency_bootstrap tests.test_standard_workflow tests.test_skill_contract -v`

### Task 2: 店铺 ID 与千川 ID 隔离

**Files:**
- Modify: `tests/test_standard_workflow.py`
- Modify: `scripts/standard_workflow.py`

- [ ] **Step 1: 写失败测试，验证诊断批处理不接收报表店铺 ID。**

保留选择参数中的 `--shop-id shop-1`，断言首次 `extract_main` 参数不存在 `--shop-id`，而报表选择参数仍包含它。

- [ ] **Step 2: 运行测试确认当前实现失败。**

Run: `python -m unittest tests.test_standard_workflow.StandardWorkflowTests.test_prepare_does_not_pass_report_shop_id_to_rpa -v`

- [ ] **Step 3: 移除诊断批处理参数中的 `--shop-id`。**

保留 `extract_peak_frames._resolve_shop_id()` 自动调用 RPA 授权账户列表；单账户时使用其 `shop_id`（千川 ID），无账户/多账户时保留明确错误。报表选择和运行摘要继续使用用户输入的店铺 ID。

- [ ] **Step 4: 添加 RPA 入参解析回归。**

使用 mock 授权列表返回 `408IGuUKaf`，验证 RPA adapter 的请求体 `shop_id` 使用该值而非 `4456758065503561`；不修改 RPA 请求协议。

- [ ] **Step 5: 运行迁移回归。**

Run: `python -m unittest tests.test_standard_workflow tests.test_peak_frames tests.test_rpa_adapter tests.test_qianchuan_report_client tests.test_top_materials tests.test_asset_metrics tests.test_skill_contract -v`

### Task 3: 真实门禁与提交

- [ ] **Step 1: 检查当前解释器依赖。**

Run: `python -c 'import requests, imageio_ffmpeg; print("dependencies_ready")'`

- [ ] **Step 2: 运行完整测试。**

Run: `python -m unittest discover -s tests -v`

- [ ] **Step 3: 扫描 ID 传递和旧路径引用。**

Run: `rg -n 'diagnosis_batch|--shop-id|asset_table_client|AssetTableClient' scripts tests SKILL.md`

- [ ] **Step 4: 提交。**

Run: `git add SKILL.md scripts/run_standard_analysis.py scripts/standard_workflow.py tests/test_standard_workflow.py tests/test_skill_contract.py docs/superpowers/specs/2026-08-25-qianchuan-report-metadata-probe-design.md && git add -f docs/superpowers/plans/2026-08-25-qianchuan-runtime-and-rpa-id.md && git commit -m "fix: gate workflow dependencies and separate rpa shop id"`
