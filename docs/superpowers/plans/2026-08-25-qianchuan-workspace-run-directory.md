# 千川工作空间运行目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让平台执行的全部千川运行产物落在对话工作空间，而本地 Codex 运行仍落在仓库 `runs/` 目录。

**Architecture:** 保留 `run_standard_analysis.py` 的显式 `--output-dir` 参数，将调用文档统一为 `$PWD/runs/<run-id>`。平台的 `$PWD` 已确认是对话工作空间根目录；本地从仓库运行时 `$PWD` 是仓库根目录。所有下游路径继续相对 `--output-dir` 生成，不修改 RPA、报表或 AI 实现。

**Tech Stack:** Markdown、Python `unittest`。

---

### Task 1: 锁定调用方工作目录契约

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `SKILL.md`
- Modify: `references/qianchuan-operations-runbook.md`

- [ ] **Step 1: 写失败的技能契约测试。**

在 `StandardWorkflowSkillContractTests` 中新增：

```python
def test_skill_scopes_runs_to_the_caller_working_directory(self):
    skill = SKILL_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    self.assertIn('$PWD/runs/<run-id>', skill)
    self.assertIn('$PWD/runs/<run-id>', runbook)
    self.assertNotIn('<skill-root>/runs', skill)
    self.assertNotIn('.system/skills', skill)
```

- [ ] **Step 2: 运行测试，确认当前技能文档失败。**

Run: `python -m unittest tests.test_skill_contract.StandardWorkflowSkillContractTests.test_skill_scopes_runs_to_the_caller_working_directory -v`

Expected: FAIL，因为当前 `SKILL.md` 使用 `<skill-root>/runs/<run-id>`，且运维手册尚未声明 `$PWD` 目录策略。

- [ ] **Step 3: 以最小改动更新标准工作流命令。**

在 `SKILL.md` 的 `prepare`、`status`、`fetch-metadata` 和 `finalize` 示例中，将每个运行目录替换为：

```bash
$PWD/runs/<run-id>
```

将 AI JSON 参数写为：

```bash
--analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

在“标准流程”前加入一段明确说明：平台中 `$PWD` 是当前对话工作空间根目录，本地 Codex 从仓库运行时 `$PWD` 是仓库根目录；同一 `run-id` 必须贯穿 `prepare`、`status`、AI 写入和 `finalize`。不得将输出路径设为 `.system/skills/...` 或技能根路径。

在 `references/qianchuan-operations-runbook.md` 开头增加同一目录规则，并将示例中的 `/path/to/run` 说明为“已创建的 `$PWD/runs/<run-id>`”；保留各修复命令的子目录后缀。

- [ ] **Step 4: 运行契约测试，确认通过。**

Run: `python -m unittest tests.test_skill_contract.StandardWorkflowSkillContractTests.test_skill_scopes_runs_to_the_caller_working_directory tests.test_skill_contract.StandardWorkflowSkillContractTests.test_skill_documents_only_the_standard_prepare_and_finalize_commands tests.test_skill_contract.StandardWorkflowSkillContractTests.test_active_workflow_does_not_expose_removed_cli_wrappers -v`

Expected: PASS。

- [ ] **Step 5: 提交文档与契约测试。**

```bash
git add SKILL.md references/qianchuan-operations-runbook.md tests/test_skill_contract.py
git commit -m "fix: scope qianchuan runs to caller workspace"
```

### Task 2: 完整回归与静态路径验证

**Files:**
- Verify: `SKILL.md`
- Verify: `references/qianchuan-operations-runbook.md`
- Verify: `scripts/standard_workflow.py`
- Verify: `tests/test_skill_contract.py`

- [ ] **Step 1: 扫描错误路径和目录传播边界。**

Run:

```bash
rg -n '<skill-root>/runs|\.system/skills|\$PWD/runs/<run-id>|--output-dir' SKILL.md references/qianchuan-operations-runbook.md tests/test_skill_contract.py
```

Expected: 标准命令和运行手册包含 `$PWD/runs/<run-id>`；`SKILL.md` 中没有 `<skill-root>/runs` 或 `.system/skills`。

- [ ] **Step 2: 验证实现仍只依赖显式输出目录。**

Run:

```bash
rg -n 'def workflow_paths|Path\(output_dir\)|source_data|diagnosis-records|peak-frames' scripts/standard_workflow.py
```

Expected: 所有产物路径由 `output_dir` 的 `Path` 派生，没有技能目录或平台 `.system` 路径。

- [ ] **Step 3: 运行全量测试。**

Run: `python -m unittest discover -s tests -v`

Expected: `OK`，无失败或错误。

- [ ] **Step 4: 检查工作树与提交内容。**

Run:

```bash
git status --short
git show --stat --oneline HEAD
git diff HEAD^ HEAD --check
```

Expected: 仅包含目录策略的技能文档、运维手册与契约测试；无空白错误。
