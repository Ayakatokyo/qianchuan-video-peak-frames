# Qianchuan Workflow Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block duplicate standard runs before RPA, stop primary RPA submission when verified records reach Top N, and replace only terminal postprocess failures.

**Architecture:** A request-scoped lock is keyed by advertiser, dates, Top N, and shop ID, then stored in the shared parent of sibling run directories. Therefore an externally generated `qianchuan-run-v2` conflicts with a running `qianchuan-run`. The batch runner separates verified RPA collection from postprocess completion, and only starts a bounded replacement wave after retries and serial compensation fail.

**Tech Stack:** Python 3.12, `argparse`, `hashlib`, `json`, `unittest`, and existing `batch_runtime` lock helpers.

---

### Task 1: Request-Scoped Prepare Lock

**Files:**
- Modify: `scripts/standard_workflow.py:1-179`
- Modify: `scripts/run_standard_analysis.py:1-72`
- Test: `tests/test_standard_workflow.py`

- [ ] **Step 1: Write failing same-request and `-v2` tests.**

```python
def test_prepare_rejects_v2_while_same_request_is_running(self):
    original = root / "qianchuan-run"
    versioned = root / "qianchuan-run-v2"
    lock = standard_workflow.acquire_workflow_lock(self.request(), original)
    try:
        with self.assertRaisesRegex(StandardWorkflowError, "already running"):
            standard_workflow.prepare_standard_run(
                self.request(), versioned,
                export_main=lambda _argv: self.fail("selection must not run"),
                extract_main=lambda _argv: self.fail("RPA must not run"),
            )
    finally:
        lock.release()
    self.assertFalse((versioned / "run-summary.json").exists())
```

Add the same assertion for an already-present summary: retain its original bytes after the locked call returns.

- [ ] **Step 2: Run the red test.**

Run: `python -m unittest tests.test_standard_workflow.StandardWorkflowTests.test_prepare_rejects_v2_while_same_request_is_running`

Expected: fail because no workflow lock exists.

- [ ] **Step 3: Implement deterministic request locking.**

Add `WorkflowLockError(StandardWorkflowError)` and the following helpers in `standard_workflow.py`:

```python
def _workflow_request_key(request, shop_id):
    payload = json.dumps({
        "advertiserId": request.advertiser_id,
        "startDate": request.start_date,
        "endDate": request.end_date,
        "topN": request.top_n,
        "shopId": shop_id,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def workflow_lock_path(request, output_dir, shop_id):
    return Path(output_dir).parent / ".qianchuan-workflow-locks" / (
        _workflow_request_key(request, shop_id) + ".lock"
    )
```

Use `acquire_run_lock(workflow_lock_path(...), paths["summary"])` before selection. Keep it until facts or an owner-written failure summary completes. In the `WorkflowLockError` branch, re-raise without `_write_failure`; this preserves any existing summary. Acquire the same lock before `finalize` renders the report.

- [ ] **Step 4: Run the workflow suite.**

Run: `python -m unittest tests.test_standard_workflow`

Expected: exit code `0`; neither selection nor RPA runs for the `-v2` duplicate.

- [ ] **Step 5: Commit.**

Run: `git add scripts/standard_workflow.py scripts/run_standard_analysis.py tests/test_standard_workflow.py && git commit -m "fix: lock standard workflow by request"`

### Task 2: Running State and Read-Only Status

**Files:**
- Modify: `scripts/standard_workflow.py`
- Modify: `scripts/run_standard_analysis.py`
- Test: `tests/test_standard_workflow.py`

- [ ] **Step 1: Write the red status test.**

```python
def test_status_is_read_only_and_reports_manifest_counts(self):
    standard_workflow.write_run_summary(summary_path, {
        "schemaVersion": 1, "status": "running", "activeStage": "diagnosis_batch",
        "request": self.request_payload(), "artifacts": {}, "failure": None,
    })
    manifest_path.write_text(json.dumps({"stageCounts": {
        "rpaAcceptedCount": 2, "postprocessSucceededCount": 1,
        "reportReadyCount": 0,
    }}), encoding="utf-8")
    before = (summary_path.read_bytes(), manifest_path.read_bytes())
    status = standard_workflow.standard_run_status(root)
    self.assertEqual(status["workflowStatus"], "running")
    self.assertEqual(status["activeStage"], "diagnosis_batch")
    self.assertEqual(status["rpaAcceptedCount"], 2)
    self.assertEqual(before, (summary_path.read_bytes(), manifest_path.read_bytes()))
```

- [ ] **Step 2: Run the red test.**

Run: `python -m unittest tests.test_standard_workflow.StandardWorkflowTests.test_status_is_read_only_and_reports_manifest_counts`

Expected: fail because `standard_run_status` is absent.

- [ ] **Step 3: Implement checkpoints and CLI status.**

Extend `_summary` with `active_stage=None`; add `_write_running(request, paths, stage)` with `status="running"`. While holding the request lock, write stages `selection`, `diagnosis_batch`, `verification`, `facts`, and `report` before invoking each stage.

Implement a pure `standard_run_status(output_dir)` that reads the summary, sibling request locks, and `peak-frames/batch-manifest.json`, then returns:

```python
{
    "workflowStatus": status_or_not_started,
    "activeStage": active_stage_or_none,
    "lock": matching_lock_payload_or_none,
    "rpaAcceptedCount": accepted_count,
    "postprocessSucceededCount": succeeded_count,
    "reportReadyCount": report_count,
}
```

Add `status --output-dir` in `run_standard_analysis.py`; it prints that object as JSON and performs no mutation.

- [ ] **Step 4: Run the workflow suite.**

Run: `python -m unittest tests.test_standard_workflow`

Expected: exit code `0`; active state is readable and `finalize` retry retains `analysis_pending`.

- [ ] **Step 5: Commit.**

Run: `git add scripts/standard_workflow.py scripts/run_standard_analysis.py tests/test_standard_workflow.py && git commit -m "feat: expose standard workflow status"`

### Task 3: Primary RPA Collection Gate

**Files:**
- Modify: `scripts/batch_runtime.py:48-82`
- Modify: `scripts/schemas.py:44-90` and `scripts/schemas.py:277-320`
- Modify: `scripts/rpa_batch_runner.py:14-493`
- Modify: `scripts/extract_peak_frames.py:883-889`
- Test: `tests/test_batch_runtime.py`
- Test: `tests/test_schemas.py`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write two failing tests.**

```python
def test_stage_counts_separates_accepted_records_from_postprocess(self):
    counts = batch_runtime.stage_counts(self.entries_with_one_valid_record())
    self.assertEqual(counts["rpaRequestCount"], 2)
    self.assertEqual(counts["rpaAcceptedCount"], 1)
    self.assertEqual(counts["postprocessSucceededCount"], 0)

def test_streaming_never_submits_m3_while_m1_m2_are_postprocessing(self):
    # target=2; m-1/m-2 fetch valid CSV; block m-1 frame extraction.
    # Before unblock, assert observed RPA IDs == ["m-1", "m-2"].
```

Use existing streaming CSV/result helpers and `threading.Event` so the second assertion happens while postprocess is still active.

- [ ] **Step 2: Run red tests.**

Run: `python -m unittest tests.test_batch_runtime tests.test_peak_frames.CliTests.test_streaming_never_submits_m3_while_m1_m2_are_postprocessing`

Expected: scheduling test fails because current capacity uses postprocess success.

- [ ] **Step 3: Implement accepted-record lifecycle.**

Add `rpaRequestCount` and `rpaAcceptedCount` to `stage_counts`. An accepted record requires `rpaStatus == "selected"`, `rpaCsvVerified is True`, `recordsPersisted is True`, and a valid retained records JSON.

Persist:

```python
"rpaCollection": {
    "status": "collecting",
    "targetCount": args.target_top_n,
    "acceptedCount": 0,
}
```

Validate collection statuses `collecting`, `complete`, `backfilling`, plus material `backfillReason` values `None`, `rpa_collection_failure`, and `postprocess_failed_after_retries`.

Replace primary capacity with:

```python
def primary_collection_slots(include_rpa=0):
    return max(0, args.target_top_n - len(accepted_entries()) - include_rpa)
```

`accepted_entries()` includes selected entries with verified CSV and persisted records. Use the formula in both wave scheduling modes. When acceptance reaches Top N, persist `rpaCollection.status="complete"` and continue polling postprocess but do not call `submit_rpa` again. A replacement for a rejected primary RPA result has `backfillReason="rpa_collection_failure"`.

- [ ] **Step 4: Run scheduling and schema suites.**

Run: `python -m unittest tests.test_batch_runtime tests.test_schemas tests.test_rpa_batch_runner tests.test_peak_frames`

Expected: exit code `0`; delayed frame extraction never causes an early candidate RPA request.

- [ ] **Step 5: Commit.**

Run: `git add scripts/batch_runtime.py scripts/schemas.py scripts/rpa_batch_runner.py scripts/extract_peak_frames.py tests/test_batch_runtime.py tests/test_schemas.py tests/test_peak_frames.py && git commit -m "fix: stop RPA after verified collection"`

### Task 4: Terminal-Only Postprocess Backfill

**Files:**
- Modify: `scripts/rpa_batch_runner.py:442-493`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write the red terminal-failure test.**

```python
def test_streaming_backfills_only_after_terminal_postprocess_failure(self):
    # target=2: m-1/m-2 RPA succeed; m-1 fails two parallel attempts
    # and serial compensation; m-2/m-3 postprocess succeed.
    self.assertEqual(rpa_starts, ["m-1", "m-2", "m-3"])
    self.assertEqual(by_id["m-3"]["backfillReason"], "postprocess_failed_after_retries")
    self.assertEqual(manifest["stageCounts"]["rpaRequestCount"], 3)
```

- [ ] **Step 2: Run it.**

Run: `python -m unittest tests.test_peak_frames.CliTests.test_streaming_backfills_only_after_terminal_postprocess_failure`

Expected: fail before the postprocess-closure backfill loop exists.

- [ ] **Step 3: Implement bounded backfill after compensation.**

After the existing serial compensation pass, calculate `deficit = target_top_n - len(successful_entries())`. If positive, submit at most that many unaccounted candidates as one replacement wave, annotating each `postprocess_failed_after_retries`; wait for that wave's RPA and postprocess futures, run serial compensation only for its newly terminal failures, then recalculate. Never resubmit an entry whose `compensationStatus` is already `succeeded` or `failed`.

If successes reach Top N, cancel remaining candidates with `failureReason="target_satisfied"` and publish the ready list. If candidates exhaust, remove stale ready JSON using `Path(ready_path).unlink(missing_ok=True)` and return partial failure.

- [ ] **Step 4: Run RPA suites.**

Run: `python -m unittest tests.test_rpa_batch_runner tests.test_peak_frames`

Expected: exit code `0`; only terminal failures cause precisely deficit-sized replacement waves.

- [ ] **Step 5: Commit.**

Run: `git add scripts/rpa_batch_runner.py tests/test_peak_frames.py tests/test_rpa_batch_runner.py && git commit -m "fix: defer RPA backfill until recovery completes"`

### Task 5: Require Complete Ready Data Before AI

**Files:**
- Modify: `scripts/standard_workflow.py:129-179`
- Modify: `scripts/batch_verification.py:75-176`
- Modify: `SKILL.md:21-64`
- Modify: `references/qianchuan-operations-runbook.md:1-43`
- Modify: `tests/test_standard_workflow.py`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Write the red partial-ready test.**

```python
def test_prepare_does_not_build_facts_from_partial_ready_materials(self):
    self.write_ready_materials(root, ["m-1"])
    self.write_success_manifest(root, ["m-1"], target_top_n=2)
    with self.assertRaisesRegex(StandardWorkflowError, "ready materials"):
        standard_workflow.prepare_standard_run(
            self.request(), root, export_main=lambda _argv: 0,
            extract_main=lambda _argv: 0,
            prepare_facts=lambda **_kwargs: self.fail("facts must not run"),
        )
```

- [ ] **Step 2: Run it.**

Run: `python -m unittest tests.test_standard_workflow.StandardWorkflowTests.test_prepare_does_not_build_facts_from_partial_ready_materials`

Expected: fail because ready membership is not cross-checked.

- [ ] **Step 3: Implement ready validation and operational instructions.**

Implement `validate_ready_materials(paths, top_n)`: require exactly Top N unique IDs in ready JSON; require the same set as the first rank-ordered Top N selected manifest entries with `postprocessStatus="succeeded"`; require a valid verification receipt for that set. Call it after `--verify` and before `prepare_facts`, raising `StandardWorkflowError("facts", "ready materials ...")` on mismatch.

Update batch verification to report ready-list mismatch. Update `SKILL.md` and the runbook to require `status`, successful `prepare`, `analysis_pending`, and both fact files before AI; prohibit constructing facts from `top_materials.json` or hand-writing `missingRanks`.

- [ ] **Step 4: Run full verification.**

Run: `python -m compileall -q scripts && python -m unittest discover -s tests -p 'test_*.py' && git diff --check`

Expected: exit code `0`; all duplicate-run, `-v2`, RPA quota, terminal-backfill, and ready-data regressions pass.

- [ ] **Step 5: Commit.**

Run: `git add scripts/standard_workflow.py scripts/batch_verification.py SKILL.md references/qianchuan-operations-runbook.md tests/test_standard_workflow.py tests/test_skill_contract.py && git commit -m "fix: gate AI facts on complete ready materials"`

