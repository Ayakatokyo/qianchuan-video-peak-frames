# Qianchuan RPA Preflight Reuse and Parallel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist successful RPA preflight records, reuse them in formal batch extraction by default, and run preflight with bounded ordered concurrency.

**Architecture:** Preflight will return normalized records together with its availability outcome, atomically persist those records before selecting a material, and write a schema-version-2 receipt. The formal batch will validate a supplied receipt and resolve each selected material to its local records; only `--allow-rpa-refetch` may use the existing connector fallback. A bounded thread-pool scheduler will submit at most `--concurrency` preflight jobs, persist each completion, and rebuild output lists in candidate order.

**Tech Stack:** Python 3, `unittest`, `unittest.mock`, `ThreadPoolExecutor`, JSON artifacts, existing RPA adapter and peak-frame pipeline.

---

### Task 1: Return diagnostic records from preflight

**Files:**
- Modify: `scripts/rpa_adapter.py:fetch_material_preflight`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing adapter tests**

```python
result = rpa_adapter.fetch_material_preflight(...)
assert result["rpaAvailable"] is True
assert result["records"] == records
assert result["diagnosisCsvPath"] is None
```

Add a files-envelope test asserting that the existing CSV is retained while the result exposes records for persistence.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_peak_frames.RpaAdapterTests -v`

Expected: new assertions fail because successful outcomes do not expose `records` or CSV path metadata.

- [ ] **Step 3: Implement minimal adapter outcome enrichment**

Keep failure classifications unchanged. On success, add normalized `records`; report the deterministic CSV target path only when `diagnosis_csv_dir` is used for a files envelope.

- [ ] **Step 4: Run adapter tests**

Run: `python -m unittest tests.test_peak_frames.RpaAdapterTests -v`

Expected: PASS.

### Task 2: Persist schema-v2 preflight artifacts

**Files:**
- Modify: `scripts/extract_peak_frames.py:_material_entry_with_preflight`, `_preflight_receipt`, `_run_rpa_preflight`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing preflight persistence tests**

```python
receipt = json.loads((output / "rpa-preflight.json").read_text())
selected = receipt["selectedMaterials"][0]
assert receipt["schemaVersion"] == 2
assert selected["diagnosisRecordsPath"] == "diagnosis-records/m-1.json"
assert json.loads((records_dir / "m-1.json").read_text()) == records_for("m-1")
assert selected["preflightStatus"] == "selected"
assert "timingsSec" in selected
```

Add a records-write failure test asserting the material is rejected and absent from the ready list. Patch `_process_rpa_material`, `download_video`, and `process_records` to assert that `--preflight-only` invokes none of them.

- [ ] **Step 2: Run the new tests to verify failure**

Run: `python -m unittest tests.test_peak_frames.RpaCliTests.test_rpa_preflight_persists_reusable_records -v`

Expected: FAIL because the receipt is version 1 and no records path is written by preflight.

- [ ] **Step 3: Implement atomic selected-record persistence**

Before a material can become selected, call `_write_diagnosis_records`, add its run-relative records and optional CSV paths, status, and timing metadata, then atomically rewrite both preflight artifacts. Convert persistence errors to rejected outcomes.

- [ ] **Step 4: Run the focused preflight tests**

Run: `python -m unittest tests.test_peak_frames.RpaCliTests -v`

Expected: PASS.

### Task 3: Add bounded, candidate-ordered parallel preflight

**Files:**
- Modify: `scripts/extract_peak_frames.py:_run_rpa_preflight`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing scheduler tests**

```python
assert max_active <= 3
assert max_active > 1
assert [row["materialId"] for row in receipt["selectedMaterials"]] == ["m-a", "m-b"]
assert [row["materialId"] for row in ready["materials"]] == ["m-a", "m-b"]
assert receipt["preflightConcurrency"] == 3
assert receipt["stoppedAfterSatisfied"] is True
```

Make lower-ranked tasks complete first and verify selected/ready order remains asset-table order. Add a counting test proving no candidate is submitted after the requested Top N is satisfied.

- [ ] **Step 2: Run the new scheduler tests to verify failure**

Run: `python -m unittest tests.test_peak_frames.RpaCliTests.test_rpa_preflight_parallelism_preserves_candidate_order -v`

Expected: FAIL because preflight is currently serial.

- [ ] **Step 3: Implement bounded completion scheduling**

Use a `ThreadPoolExecutor(max_workers=args.concurrency)` only to run preflight fetches. Fill available slots from the candidate head, process completed futures, persist their terminal outcome, and stop filling slots when enough selected records exist. Wait for already-submitted futures, mark unsubmitted candidates cancelled in the receipt, and rebuild selected/rejected/ready arrays by original candidate index.

- [ ] **Step 4: Run scheduler regression tests**

Run: `python -m unittest tests.test_peak_frames.RpaCliTests.test_rpa_preflight_parallelism_preserves_candidate_order tests.test_peak_frames.RpaCliTests.test_rpa_preflight_stops_submitting_after_satisfaction -v`

Expected: PASS.

### Task 4: Consume and validate a schema-v2 preflight receipt

**Files:**
- Modify: `scripts/extract_peak_frames.py:build_parser`, `_process_rpa_material`, `main`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing formal-batch reuse tests**

```python
with patch("extract_peak_frames.fetch_material_diagnosis") as refetch:
    result = extract_peak_frames.main([... , "--rpa-preflight-json", str(receipt)])
assert result == 0
refetch.assert_not_called()
```

Add failure cases for unsatisfied, version-1, shop/date mismatch, ready materials absent from selected, unreadable records, invalid records root, and missing matching `videoUrl`. Add one `--allow-rpa-refetch` test showing an invalid local entry can use the existing connector path.

- [ ] **Step 2: Run the reuse test to verify failure**

Run: `python -m unittest tests.test_peak_frames.RpaCliTests.test_rpa_batch_reuses_preflight_records_without_refetch -v`

Expected: FAIL because the CLI does not accept `--rpa-preflight-json`.

- [ ] **Step 3: Implement receipt validation and material-local records resolution**

Add `--rpa-preflight-json` and `--allow-rpa-refetch`. Validate all schema-v2 contract fields before `_run_rpa_batch`; resolve receipt-relative records paths, parse and verify records per material, and pass the validated records into `_process_rpa_material`. Keep the existing fetch path as the explicit fallback only.

- [ ] **Step 4: Run all reuse contract tests**

Run: `python -m unittest tests.test_peak_frames.RpaCliTests -v`

Expected: PASS.

### Task 5: Document the schema-v2 workflow

**Files:**
- Modify: `SKILL.md`
- Modify: `references/rpa-output-contract.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Write failing documentation contract assertions**

```python
self.assertIn("--rpa-preflight-json", skill)
self.assertIn("--allow-rpa-refetch", skill)
self.assertIn("schemaVersion": 2, contract)
self.assertIn("preflightConcurrency", contract)
```

- [ ] **Step 2: Run the documentation tests to verify failure**

Run: `python -m unittest tests.test_skill_contract -v`

Expected: FAIL because the formal example does not yet consume the receipt.

- [ ] **Step 3: Update examples and output contract**

Document `--preflight-only --concurrency 3`, persisted records, schema v2 fields, default no-refetch behavior, explicit fallback, and the no-JPEG preflight boundary.

- [ ] **Step 4: Run documentation tests**

Run: `python -m unittest tests.test_skill_contract -v`

Expected: PASS.

### Task 6: Full verification

**Files:**
- Verify: `tests/test_peak_frames.py`
- Verify: `tests/test_skill_contract.py`

- [ ] **Step 1: Run the complete relevant suite**

Run: `python -m unittest tests.test_peak_frames tests.test_skill_contract -v`

Expected: PASS with no test failures.

- [ ] **Step 2: Inspect the final change set**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only the planned source, tests, documentation, and plan files are modified.
