# Qianchuan Standard Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose one three-input Qianchuan workflow while retaining the proven RPA wave, postprocessing, recovery, and report-validation mechanisms as internal implementation details.

**Architecture:** Add `standard_workflow.py` as an orchestration library and `run_standard_analysis.py` as its thin two-phase adapter. `prepare` runs selection, streaming RPA, automatic verification, and fact-package publication; the model then writes the existing validated AI JSON; `finalize` renders and verifies the report. The skill owns this handoff and exposes only date range, Top N, and advertiser ID to users.

**Tech Stack:** Python 3.9+, unittest, existing asset-table client, RPA adapter, streaming `ThreadPoolExecutor` scheduler, FFmpeg, JSON artifacts, HTML renderer.

---

## File Structure

- Create: `scripts/standard_workflow.py` - request/path models, prepare/finalize orchestration, atomic `run-summary.json` updates, and stage-specific errors.
- Create: `scripts/run_standard_analysis.py` - minimal internal CLI adapter for the skill's prepare/finalize handoff.
- Create: `tests/test_standard_workflow.py` - unit tests with injected script entrypoints; no RPA or asset-table network access.
- Create: `references/qianchuan-operations-runbook.md` - documentation for legacy recovery and debug tools that are intentionally outside the standard path.
- Modify: `SKILL.md` - keep the business contract and AI evidence rules; replace public multi-script choreography with the standard workflow.
- Modify: `tests/test_skill_contract.py` - assert the new public contract and repair the stale partial-frame assertion.

Existing `scripts/export_top_materials.py`, `scripts/extract_peak_frames.py`, `scripts/rpa_batch_runner.py`, `scripts/prepare_diagnostic_analysis.py`, and `scripts/render_final_report.py` stay intact in this release. The standard orchestrator calls their tested public entrypoints; legacy/preflight deletion happens only after the new path has completed two real batches without using them.

### Task 1: Reconcile the Partial-Frame Prompt Contract

**Files:**
- Modify: `tests/test_skill_contract.py:354-369`
- Verify: `prompts/diagnostic-analysis-prompt-v1.md`

- [ ] **Step 1: Reproduce the stale prompt-contract failure**

Run: `./.venv/bin/python -m unittest tests.test_skill_contract.BatchSkillContractTests.test_final_report_prompt_requires_every_highlight_frame_analysis -v`

Expected: FAIL because the test requires the contradicted phrase `缺失任何一帧都不能视为完成`.

- [ ] **Step 2: Replace the stale failing assertion with the accepted coverage contract**

```python
def test_final_report_prompt_requires_every_available_highlight_frame_analysis(self):
    prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

    self.assertIn("每一张高光帧都必须对应一条 `highlightAnalyses`", prompt)
    self.assertIn("成功生成的 JPEG", prompt)
    self.assertIn("missingFrames", prompt)
    self.assertIn("不得只分析 zero_second 和 peak_top_1", prompt)
    self.assertNotIn("缺失任何一帧都不能视为完成", prompt)
```

- [ ] **Step 3: Run the prompt contract suite**

Run: `./.venv/bin/python -m unittest tests.test_skill_contract -v`

Expected: PASS. The prompt test now agrees with `peak_frame_lib`'s zero-second-plus-one-peak material-success rule.

- [ ] **Step 4: Commit the baseline repair**

Run: `git add tests/test_skill_contract.py && git commit -m "test: align partial frame prompt contract"`

### Task 2: Add a Testable Standard Workflow Orchestrator

**Files:**
- Create: `tests/test_standard_workflow.py`
- Create: `scripts/standard_workflow.py`

- [ ] **Step 1: Write failing prepare/finalize tests using injected stage functions**

```python
def test_prepare_runs_selection_batch_verification_and_fact_package(self):
    calls = []
    request = WorkflowRequest("2026-07-19", "2026-07-25", 9, "1770096415226951")
    with tempfile.TemporaryDirectory() as temp_dir:
        handoff = prepare_standard_run(
            request, temp_dir,
            export_main=lambda argv: calls.append(("export", argv)) or 0,
            extract_main=lambda argv: calls.append(("extract", argv)) or 0,
            prepare_facts=lambda **kwargs: calls.append(("facts", kwargs)) or (
                Path(temp_dir) / "diagnostic-analysis/data.json",
                Path(temp_dir) / "diagnostic-analysis/prompt.md", {}
            ),
        )
    self.assertEqual(handoff["status"], "analysis_pending")
    self.assertEqual(calls[0][0], "export")
    self.assertIn("--advertiser-id", calls[0][1])
    self.assertIn("--target-top-n", calls[1][1])
    self.assertIn("--verify", calls[2][1])
    self.assertEqual(calls[3][0], "facts")

def test_finalize_marks_summary_complete_only_after_renderer_marks_manifest_ready(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        write_run_summary(Path(temp_dir) / "run-summary.json", {"status": "analysis_pending"})
        result = finalize_standard_run(
            temp_dir, Path(temp_dir) / "analysis.json",
            render_report=lambda **kwargs: (Path(temp_dir) / "data.json", Path(temp_dir) / "report.html", {}),
            mark_ready=lambda *_paths: True,
        )
    self.assertEqual(result["status"], "complete")
```

Add a third test where the streaming batch entrypoint returns `5`: assert `StandardWorkflowError.stage == "diagnosis_batch"`, `run-summary.json` has `status == "failed"`, and fact-package publication is never called.

Add a fourth test with `patch.dict(os.environ, {"QIANCHUAN_RPA_SHOP_ID": "shop-1"})`; assert the batch argv includes `--shop-id`, `shop-1`. With the variable absent, assert no `--shop-id` is added so the existing single-authorized-shop resolver remains the fallback.

- [ ] **Step 2: Run the new test module and confirm failure**

Run: `./.venv/bin/python -m unittest tests.test_standard_workflow -v`

Expected: FAIL because `standard_workflow` and its request/orchestration API do not exist.

- [ ] **Step 3: Implement the request, layout, and atomic summary contract**

```python
@dataclass(frozen=True)
class WorkflowRequest:
    start_date: str
    end_date: str
    top_n: int
    advertiser_id: str

class StandardWorkflowError(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage

def workflow_paths(output_dir):
    root = Path(output_dir)
    return {
        "root": root, "top_materials": root / "top_materials.json",
        "peak_frames": root / "peak-frames", "records": root / "diagnosis-records",
        "analysis": root / "diagnostic-analysis", "report": root / "final-report",
        "summary": root / "run-summary.json",
    }
```

Validate ISO dates, `top_n > 0`, and non-empty advertiser ID before any stage function runs. Write a JSON summary atomically after every terminal state. Its stable shape is:

```json
{
  "schemaVersion": 1,
  "status": "analysis_pending",
  "request": {"startDate": "2026-07-19", "endDate": "2026-07-25", "topN": 9, "advertiserId": "1770096415226951"},
  "artifacts": {"topMaterials": "top_materials.json", "readyMaterials": "peak-frames/top_materials_rpa_ready.json", "diagnosticData": "diagnostic-analysis/diagnostic-analysis-data.json", "diagnosticPrompt": "diagnostic-analysis/diagnostic-analysis-prompt.md", "report": null},
  "failure": null
}
```

- [ ] **Step 4: Implement `prepare_standard_run` using existing public stage functions**

```python
candidate_top_n = request.top_n * 3
_run_stage("selection", export_main, [
    "--start-date", request.start_date, "--end-date", request.end_date,
    "--advertiser-id", request.advertiser_id, "--top-n", str(candidate_top_n),
    "--output", str(paths["top_materials"]),
])
batch_args = [
    "--source", "rpa", "--top-materials-json", str(paths["top_materials"]),
    "--target-top-n", str(request.top_n), "--candidate-top-n", str(candidate_top_n),
    "--date-range-type", "CUSTOM", "--custom-start-date", request.start_date,
    "--custom-end-date", request.end_date, "--diagnosis-records-dir", str(paths["records"]),
    "--output-dir", str(paths["peak_frames"]),
]
shop_id = os.environ.get("QIANCHUAN_RPA_SHOP_ID", "").strip()
if shop_id:
    batch_args.extend(["--shop-id", shop_id])
_run_stage("diagnosis_batch", extract_main, batch_args)
_run_stage("verification", extract_main, [*batch_args, "--verify"])
data_path, prompt_path, _ = prepare_facts(
    top_materials_json=paths["peak_frames"] / "top_materials_rpa_ready.json",
    diagnosis_records_dir=paths["records"], peak_output_dir=paths["peak_frames"],
    output_dir=paths["analysis"],
)
```

`_run_stage` must treat every nonzero CLI return as a failure, update the summary to `failed` with only the stage name and error message, then raise `StandardWorkflowError`. Do not call `--preflight-only`, `--resume`, `--allow-rpa-refetch`, legacy batch mode, or a manual repair path.

- [ ] **Step 5: Implement `finalize_standard_run` with mandatory report-ready validation**

```python
data_path, html_path, _ = render_report(
    top_materials_json=paths["peak_frames"] / "top_materials_rpa_ready.json",
    peak_output_dir=paths["peak_frames"], diagnosis_records_dir=paths["records"],
    output_dir=paths["report"], analysis_json=analysis_json, copy_assets=True,
)
if not mark_ready(paths["peak_frames"] / "batch-manifest.json", data_path, html_path):
    raise StandardWorkflowError("report", "final report failed manifest validation")
```

Require an existing `analysis_pending` summary and a regular JSON analysis file. On success set `status` to `complete` and populate `artifacts.report`; on any validation/render failure set `status` to `failed`. Never render a draft report from this standard path.

- [ ] **Step 6: Run the new module and focused existing suites**

Run: `./.venv/bin/python -m unittest tests.test_standard_workflow tests.test_top_materials tests.test_peak_frames -v`

Expected: PASS. Existing tests confirm the wrapper has not altered ranking, wave scheduling, postprocess compensation, or frame extraction behavior.

- [ ] **Step 7: Commit the orchestration library**

Run: `git add scripts/standard_workflow.py tests/test_standard_workflow.py && git commit -m "feat: add standard qianchuan workflow"`

### Task 3: Add the Thin Prepare/Finalize CLI Adapter

**Files:**
- Create: `scripts/run_standard_analysis.py`
- Modify: `tests/test_standard_workflow.py`

- [ ] **Step 1: Add failing CLI tests**

```python
def test_prepare_cli_accepts_only_business_inputs(self):
    parser = run_standard_analysis.build_parser()
    args = parser.parse_args([
        "prepare", "--start-date", "2026-07-19", "--end-date", "2026-07-25",
        "--top-n", "9", "--advertiser-id", "1770096415226951", "--output-dir", "run",
    ])
    self.assertEqual(args.command, "prepare")
    self.assertFalse(hasattr(args, "rpa_concurrency"))

def test_finalize_cli_requires_ai_analysis_json(self):
    self.assertEqual(run_standard_analysis.main(["finalize", "--output-dir", "run"]), 2)
```

- [ ] **Step 2: Run the CLI tests and confirm failure**

Run: `./.venv/bin/python -m unittest tests.test_standard_workflow.StandardWorkflowCliTests -v`

Expected: FAIL because `run_standard_analysis` does not exist.

- [ ] **Step 3: Implement the CLI with no reliability switches**

```python
def build_parser():
    parser = argparse.ArgumentParser(description="Run the standard Qianchuan analysis workflow.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("start-date", "end-date", "top-n", "advertiser-id", "output-dir"):
        prepare.add_argument(f"--{name}", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--output-dir", required=True)
    finalize.add_argument("--analysis-json", required=True)
    return parser
```

Convert `top_n` to a positive integer. `prepare` constructs `WorkflowRequest` and prints only `run_summary`, `diagnostic_analysis_data`, and `diagnostic_analysis_prompt`. `finalize` prints only `run_summary`, `final_report_data`, and `final_report_html`. Map `StandardWorkflowError` to exit code `2`; retain the failed summary for operations.

- [ ] **Step 4: Run the CLI and workflow tests**

Run: `./.venv/bin/python -m unittest tests.test_standard_workflow -v`

Expected: PASS.

- [ ] **Step 5: Commit the adapter**

Run: `git add scripts/run_standard_analysis.py tests/test_standard_workflow.py && git commit -m "feat: add standard workflow cli"`

### Task 4: Replace the Public Skill Choreography with the Standard Flow

**Files:**
- Modify: `SKILL.md`
- Create: `references/qianchuan-operations-runbook.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Write failing skill-contract tests for the new public interface**

```python
def test_standard_workflow_is_the_only_batch_command_documented(self):
    skill = SKILL_PATH.read_text(encoding="utf-8")
    self.assertIn("run_standard_analysis.py prepare", skill)
    self.assertIn("run_standard_analysis.py finalize", skill)
    self.assertNotIn("python scripts/export_top_materials.py", skill)
    self.assertNotIn("python scripts/extract_peak_frames.py", skill)
    self.assertNotIn("repair_manifest.py", skill)

def test_operations_runbook_owns_legacy_recovery_details(self):
    runbook = (ROOT / "references/qianchuan-operations-runbook.md").read_text(encoding="utf-8")
    self.assertIn("repair_manifest.py", runbook)
    self.assertIn("--resume", runbook)
    self.assertIn("单素材 RPA 调试", runbook)
```

Move the existing long-form assertions about detailed AI conclusion quality to `DIAGNOSTIC_PROMPT_PATH`; leave `SKILL.md` assertions focused on business inputs, T+2 date conversion, actual JPEG review, and the standard handoff.

- [ ] **Step 2: Run the skill-contract suite and confirm failure**

Run: `./.venv/bin/python -m unittest tests.test_skill_contract -v`

Expected: FAIL because the current skill documents export, extraction, verify, resume, and repair as the normal flow.

- [ ] **Step 3: Rewrite `SKILL.md` around three user-visible phases**

Keep the frontmatter and the existing date conversion rule. Replace the multi-command batch section with:

```text
1. 将用户周期换算为明确闭区间，并以周期、Top 素材数量、广告主 ID 调用 prepare。
2. 读取事实包与提示词，实际查看其中每张 JPEG，生成 diagnostic-ai-analysis.json。
3. 调用 finalize；仅交付 run-summary.json 为 complete 的 final-report/ 目录。
```

Document only the prepare and finalize commands used by the skill implementation. State that candidate expansion, RPA waves, retries, records recovery, verification and frame extraction are automatic. Preserve the requirements that the model sees every available JPEG, uses evidence references verbatim, and never writes operational failures into business conclusions.

- [ ] **Step 4: Move operational procedures into a dedicated runbook**

Add concise sections for stale run recovery, `repair_manifest.py`, `--resume`, retained CSV/records inspection, and single-material RPA replay. The runbook must start with: "仅在标准工作流失败且需要排障时使用；不要把这些命令作为常规报告流程。" It may reference the old commands but must not modify their behavior.

- [ ] **Step 5: Run contract tests**

Run: `./.venv/bin/python -m unittest tests.test_skill_contract -v`

Expected: PASS. The normal skill path has two internal commands and three business phases; recovery documentation is isolated.

- [ ] **Step 6: Commit the public-flow documentation**

Run: `git add SKILL.md references/qianchuan-operations-runbook.md tests/test_skill_contract.py && git commit -m "docs: simplify qianchuan skill workflow"`

### Task 5: Verify the Standard Path and Gate Legacy Retirement

**Files:**
- Modify only when verification identifies a defect: files from Tasks 1-4.
- Do not delete: `scripts/rpa_preflight.py`, `scripts/repair_manifest.py`, or the legacy branch in `scripts/extract_peak_frames.py` in this task.

- [ ] **Step 1: Run static and full regression verification**

Run: `./.venv/bin/python -m compileall -q scripts && ./.venv/bin/python -m unittest discover -s tests -v`

Expected: exit 0 and all tests PASS.

- [ ] **Step 2: Execute one real standard prepare run in a new directory**

Run: `./.venv/bin/python scripts/run_standard_analysis.py prepare --start-date 2026-07-26 --end-date 2026-08-01 --top-n 6 --advertiser-id 1770096415226951 --output-dir runs/2026-08-03-standard-workflow-validation`

Expected: `run-summary.json.status == "analysis_pending"`; its ready-material count equals the requested Top N; the manifest has a valid automatic verification receipt. Do not reuse an existing run directory.

- [ ] **Step 3: Perform the model handoff and finalize the same run**

Read the generated prompt and fact package, inspect every referenced JPEG, write `diagnostic-ai-analysis.json`, then run:

Run: `./.venv/bin/python scripts/run_standard_analysis.py finalize --output-dir runs/2026-08-03-standard-workflow-validation --analysis-json runs/2026-08-03-standard-workflow-validation/diagnostic-analysis/diagnostic-ai-analysis.json`

Expected: `run-summary.json.status == "complete"`, `final-report/final-report.html` exists, copied highlight assets exist, and manifest `reportReadyCount == 1`.

- [ ] **Step 4: Record legacy-retirement evidence, but do not delete yet**

Append the standard-run ID and outcome to `references/qianchuan-operations-runbook.md`. Only after two successful real standard runs and confirmation that no scheduled caller invokes preflight/legacy modes should a follow-up plan remove `rpa_preflight.py`, `rpa_runtime.py`, the legacy scheduler, and their tests.

- [ ] **Step 5: Commit any verification-only runbook record**

Run: `git add references/qianchuan-operations-runbook.md && git commit -m "docs: record standard workflow validation"`
