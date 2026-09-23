# RPA/Postprocess Scheduler Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an experiment-only, real-RPA benchmark that compares the production full wave barrier against an RPA-only wave barrier using one fixed Top 27 candidate snapshot.

**Architecture:** Keep `run_standard_analysis.py prepare` unchanged. Extend the batch runner with an internal `full`/`rpa_only` wave policy and lifecycle timestamps, then add a dedicated CLI that exports one candidate pool, executes the two isolated variants sequentially, and derives a comparison report from their manifests.

**Tech Stack:** Python 3.13 stdlib, existing RPA adapter, `ThreadPoolExecutor`, `unittest`, JSON artifacts.

---

### Task 1: Add The RPA-Only Wave Policy

**Files:**
- Modify: `scripts/rpa_batch_runner.py:475-546`
- Modify: `scripts/schemas.py:265-311`
- Test: `tests/test_rpa_batch_runner.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: Write a failing scheduler-order test**

Add `test_rpa_only_wave_starts_after_current_rpa_completes_without_waiting_for_postprocess` to `WaveBatchRunnerTests`. Start `m-1,m-2` with `rpa_concurrency=2` and `rpa_wave_barrier_mode="rpa_only"`; block `m-1` postprocess on an event. Assert `m-3,m-4` RPA starts before releasing that event, while preserving the existing full-barrier test unchanged.

```python
self.assertEqual(rpa_starts[:2], ["m-1", "m-2"])
self.assertEqual(set(rpa_starts[2:]), {"m-3", "m-4"})
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_rpa_batch_runner.WaveBatchRunnerTests.test_rpa_only_wave_starts_after_current_rpa_completes_without_waiting_for_postprocess`

Expected: failure because the current runner treats this test as the full barrier and does not start `m-3,m-4` before postprocessing finishes.

- [ ] **Step 3: Implement the internal policy**

In `run_wave_batch`, resolve `wave_policy = getattr(args, "rpa_wave_barrier_mode", "full")` and reject values other than `full` and `rpa_only`. Keep the current full mode conditions intact. In `rpa_only` mode, advance `wave_index` and clear the completed RPA wave when `pending_wave` becomes empty, even when `postprocess_futures` still contains that wave's work; permit opening the next RPA wave in that state. Continue waiting for all postprocess futures before exiting the batch loop. Do not route this mode through the legacy `rpa_wave_barrier=False` continuous-fill branch.

Add `schedulingMode` to the v2 manifest with values `full_wave_barrier` or `rpa_only_wave_barrier`; validate that optional field in `schemas.py`. Preserve `waveBarrier: true` for both policies because both retain the RPA wave boundary.

- [ ] **Step 4: Run focused scheduler and schema tests**

Run: `python3 -m unittest tests.test_rpa_batch_runner tests.test_schemas`

Expected: all tests pass, including the new rpa-only ordering test and existing production barrier tests.

- [ ] **Step 5: Commit the policy change**

```bash
git add scripts/rpa_batch_runner.py scripts/schemas.py tests/test_rpa_batch_runner.py tests/test_schemas.py
git commit -m "feat: add rpa-only wave benchmark policy"
```

### Task 2: Persist Comparable Lifecycle Timing

**Files:**
- Modify: `scripts/rpa_batch_runner.py:208-395`
- Modify: `scripts/schemas.py:70-150`
- Test: `tests/test_rpa_batch_runner.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: Write failing timestamp tests**

Add a test that completes an accepted RPA result and asserts the final manifest entry has non-negative `recordsPersistedAtSec` and `postprocessSubmittedAtSec`, with `rpaStartedAtSec <= recordsPersistedAtSec <= postprocessSubmittedAtSec`. Add a schema test that rejects a negative `recordsPersistedAtSec`.

```python
self.assertLessEqual(entry["rpaStartedAtSec"], entry["recordsPersistedAtSec"])
self.assertLessEqual(entry["recordsPersistedAtSec"], entry["postprocessSubmittedAtSec"])
```

- [ ] **Step 2: Run the focused timestamp tests and verify failure**

Run: `python3 -m unittest tests.test_rpa_batch_runner.WaveBatchRunnerTests.test_accepted_rpa_records_and_postprocess_submission_are_timestamped tests.test_schemas.ManifestSchemaTests.test_manifest_rejects_negative_records_persisted_timestamp`

Expected: failures because the lifecycle fields do not yet exist or are not validated.

- [ ] **Step 3: Implement timestamp capture and validation**

In `complete_rpa`, after `has_accepted_record(...)` confirms the retained records and before `submit_postprocess`, set `recordsPersistedAtSec` from `elapsed(batch_started_at)`. In `submit_postprocess`, set `postprocessSubmittedAtSec` from the same batch clock before placing the future in the executor. Add both fields to the existing non-negative lifecycle-number validation tuple in `schemas.py`.

- [ ] **Step 4: Run focused timing tests**

Run: `python3 -m unittest tests.test_rpa_batch_runner tests.test_schemas`

Expected: all tests pass; timing fields appear only for accepted, postprocess-eligible entries.

- [ ] **Step 5: Commit lifecycle telemetry**

```bash
git add scripts/rpa_batch_runner.py scripts/schemas.py tests/test_rpa_batch_runner.py tests/test_schemas.py
git commit -m "feat: record batch lifecycle timestamps"
```

### Task 3: Build The Isolated Benchmark CLI

**Files:**
- Create: `scripts/rpa_scheduler_benchmark.py`
- Test: `tests/test_rpa_scheduler_benchmark.py`

- [ ] **Step 1: Write failing benchmark orchestration tests**

Create tests using injected `export_main`, `run_batch`, and a temporary root. Verify `run_benchmark` calls the exporter exactly once with `--top-n 27`, passes the identical candidate-pool SHA-256 to `barrier` then `overlap`, uses distinct `barrier/peak-frames` and `overlap/peak-frames` output directories, sets policy values `full` and `rpa_only`, and does not invoke facts or report modules. Add a test that rejects an existing benchmark root before making any external call.

```python
self.assertEqual([call["policy"] for call in calls], ["full", "rpa_only"])
self.assertEqual(calls[0]["candidateHash"], calls[1]["candidateHash"])
self.assertFalse(existing_root_export.called)
```

- [ ] **Step 2: Run the new test module and verify failure**

Run: `python3 -m unittest tests.test_rpa_scheduler_benchmark`

Expected: import failure because `rpa_scheduler_benchmark` does not yet exist.

- [ ] **Step 3: Implement the benchmark module**

Create a CLI with required `--start-date`, `--end-date`, `--top-n`, `--advertiser-id`, and `--output-dir`, plus optional `--shop-id`, `--rpa-concurrency` and `--postprocess-concurrency` defaulting to 3. Reject an existing benchmark root. Export one `candidate-pool.json` with `export_top_materials.main`, compute SHA-256 from its bytes, resolve one shop ID, then create one `argparse.Namespace` per variant for `extract_peak_frames._run_wave_rpa_batch` under `barrier/` and `overlap/` roots. Acquire the existing wave lock for each variant and do not pass `--resume` or `--verify`.

Write `benchmark-request.json` without environment values, per-variant `benchmark-summary.json`, and root `benchmark-comparison.json` plus Markdown. The summaries read only final manifests and calculate terminal counts, start/end times, and prior-wave RPA/postprocess overlap from recorded timestamps. Mark `comparable=false` when candidate hash, requested settings, or terminal outcome counts differ; retain both raw values. Return nonzero after writing artifacts when either variant misses Top 9 successful postprocess outputs.

- [ ] **Step 4: Run the benchmark test module**

Run: `python3 -m unittest tests.test_rpa_scheduler_benchmark`

Expected: all tests pass, including output isolation and single-export checks.

- [ ] **Step 5: Commit the benchmark CLI**

```bash
git add scripts/rpa_scheduler_benchmark.py tests/test_rpa_scheduler_benchmark.py
git commit -m "feat: add rpa scheduler benchmark"
```

### Task 4: Verify And Run The Real Benchmark

**Files:**
- Modify: `docs/superpowers/plans/2026-08-24-rpa-postprocess-scheduler-benchmark.md`

- [ ] **Step 1: Run the complete affected automated suite**

Run: `python3 -m unittest tests.test_rpa_batch_runner tests.test_schemas tests.test_peak_frames tests.test_rpa_scheduler_benchmark`

Expected: exit 0 with all tests passing. Confirm `tests.test_peak_frames.CliTests.test_rpa_cli_rejects_removed_compatibility_flags` still proves the production CLI cannot select no-barrier scheduling.

- [ ] **Step 2: Confirm runtime prerequisites without printing secrets**

Run a shell check that reports only whether `ENV_BACKEND_HOST`, `YUCE_AUTHORIZATION`, and either `YUCE_SESSION_ID` or the supplied cookie-style session variable are present. Map the latter to the adapter-required `YUCE_SESSION_ID` only in the process environment. Query authorized accounts only when no explicit shop ID was supplied; abort before RPA if account resolution is ambiguous.

- [ ] **Step 3: Execute the two real RPA variants sequentially**

Run the benchmark CLI with the approved 2026-07-30 to 2026-08-04 interval, target 9, approved advertiser ID, and a new timestamped output directory outside the repository. Do not invoke `run_standard_analysis.py prepare`, `finalize`, diagnostic analysis, or report rendering. Keep both run directories after completion or interruption.

- [ ] **Step 4: Inspect evidence and report results**

Read `benchmark-comparison.json`, both manifests, and both summaries. Report candidate hash, selected/successful counts, full-RPA terminal time, full-postprocess terminal time, observed overlap, duration delta, and the comparability verdict. Do not report a speedup when `comparable` is false.

- [ ] **Step 5: Commit plan tracking state**

```bash
git add -f docs/superpowers/plans/2026-08-24-rpa-postprocess-scheduler-benchmark.md
git commit -m "docs: plan scheduler benchmark"
```
