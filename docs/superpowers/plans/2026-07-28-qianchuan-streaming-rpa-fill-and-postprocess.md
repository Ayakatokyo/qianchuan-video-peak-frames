# Qianchuan Streaming RPA Fill and Postprocess Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Implement bounded, streaming RPA backfill and immediate postprocessing for Top-N Qianchuan materials.

**Architecture:** Replace the two-phase preflight then batch path with one scheduler in scripts/extract_peak_frames.py. It owns independent RPA and postprocess thread pools, persists a candidate state after every transition, and constructs all retained/report inputs by candidate rank, never future-completion order.

**Tech Stack:** Python 3, unittest, unittest.mock, ThreadPoolExecutor, JSON artifacts, existing RPA adapter, existing peak-frame extraction pipeline.

---

## File Structure

- scripts/extract_peak_frames.py: CLI configuration, streaming scheduler, per-material worker functions, manifest and compatibility artifact writing.
- tests/test_peak_frames.py: deterministic controlled-future scheduling tests plus state and artifact regression cases.
- SKILL.md: operator-facing one-command batch workflow.
- references/rpa-output-contract.md: RPA state and manifest contract.
- tests/test_skill_contract.py: documentation contract tests.

### Task 1: Define streaming batch inputs and manifest state

**Files:**
- Modify: scripts/extract_peak_frames.py:47-127, _new_batch_manifest, _load_resume_manifest, _verify_rpa_batch, main
- Test: tests/test_peak_frames.py

- [ ] **Step 1: Write failing CLI and manifest tests**

Add parser/default and one-success tests:

~~~python
args = extract_peak_frames.build_parser().parse_args([])
assert args.rpa_concurrency == 3
assert args.postprocess_concurrency == 3

manifest = json.loads((output / "batch-manifest.json").read_text())
assert manifest["schemaVersion"] == 2
assert manifest["targetTopN"] == 1
assert manifest["candidateLimit"] == 3
assert manifest["rpaConcurrency"] == 3
assert manifest["postprocessConcurrency"] == 3
assert [row["materialId"] for row in manifest["submittedMaterials"]] == ["m-1"]
assert [row["materialId"] for row in manifest["selectedMaterials"]] == ["m-1"]
assert manifest["rejectedMaterials"] == []
assert manifest["cancelledMaterials"] == []
~~~

Also add parser-error tests rejecting --preflight-only, --rpa-preflight-json, and --allow-rpa-refetch in a Top-N streaming command while preserving the legacy single --material-id path.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: python -m unittest tests.test_peak_frames.CliTests.test_streaming_batch_manifest_records_channel_configuration tests.test_peak_frames.CliTests.test_streaming_batch_rejects_legacy_preflight_switches -v

Expected: FAIL because schema version 1 and the preflight orchestration are still active.

- [ ] **Step 3: Implement explicit channel configuration and state schema**

Add --rpa-concurrency and --postprocess-concurrency positive integer arguments, each defaulting to 3. Keep --concurrency as a compatibility alias: when explicitly supplied it sets both channel values unless an explicit dedicated flag overrides it. Capture raw argv immediately after parsing to distinguish defaults from flags.

Replace _new_batch_manifest(args, material_ids) with a streaming manifest constructor receiving ranked candidate objects. Its schema version is 2. It retains rank, materialId, materialName, totalCost, rpaStatus, postprocessStatus, failureReason, diagnosis paths, summary paths, error fields, and timings for each candidate. Its top-level state includes targetTopN, candidateLimit, rpaConcurrency, postprocessConcurrency, submittedMaterials, selectedMaterials, rejectedMaterials, and cancelledMaterials. Resume and verify must bind target, candidate IDs, shop, and date range.

- [ ] **Step 4: Run the focused tests to verify success**

Run: python -m unittest tests.test_peak_frames.CliTests.test_streaming_batch_manifest_records_channel_configuration tests.test_peak_frames.CliTests.test_streaming_batch_rejects_legacy_preflight_switches -v

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add scripts/extract_peak_frames.py tests/test_peak_frames.py
git commit -m "feat: define streaming RPA batch manifest"
~~~

### Task 2: Implement the bounded streaming scheduler

**Files:**
- Modify: scripts/extract_peak_frames.py:_fetch_material_preflight_for_cli, _process_rpa_material, _run_rpa_batch, main
- Test: tests/test_peak_frames.py

- [ ] **Step 1: Write the four required failing tests**

Build helpers that create 27 descending-totalCost candidates, mock successful diagnosis records with material_info and interaction_series, and return _write_batch_result(output, material_id) from postprocessing. Use a lock-protected active RPA counter and threading.Event controls.

~~~python
assert run_streaming_batch(ids_1_to_27, target=9) == 0
assert submitted == ids_1_to_27[:9]

# Ranks 8 and 9 fail with missing_video_url.
assert run_streaming_batch(ids_1_to_27, target=9) == 0
assert submitted == ids_1_to_27[:11]
assert ids_1_to_27[11] not in submitted

assert run_streaming_batch(ids_1_to_27, target=9, rpa_concurrency=3) == 0
assert max_active_rpa == 3
assert max_active_rpa <= 3

# Ranks 2 and 3 remain blocked after rank 1 becomes RPA-ready.
assert postprocess_started.wait(timeout=1)
assert rpa_release.is_set() is False
~~~

Name the tests test_streaming_top9_all_success_submits_only_first_n, test_streaming_top9_two_rejections_submit_only_rank10_and_rank11, test_streaming_rpa_channel_never_exceeds_three_in_flight, and test_streaming_starts_postprocess_before_other_rpa_jobs_finish. Inspect manifest arrays in each test, not only mock call order.

- [ ] **Step 2: Run tests to verify failure**

Run: python -m unittest tests.test_peak_frames.CliTests.test_streaming_top9_all_success_submits_only_first_n tests.test_peak_frames.CliTests.test_streaming_top9_two_rejections_submit_only_rank10_and_rank11 tests.test_peak_frames.CliTests.test_streaming_rpa_channel_never_exceeds_three_in_flight tests.test_peak_frames.CliTests.test_streaming_starts_postprocess_before_other_rpa_jobs_finish -v

Expected: FAIL because current control flow completes RPA preflight before it starts formal processing.

- [ ] **Step 3: Extract per-candidate RPA and postprocess workers**

Implement _attempt_stream_rpa(args, candidate). It calls _fetch_material_preflight_for_cli, keeps existing reason strings for connector failures, atomically writes diagnosis-records/<materialId>.json before returning selected, and returns a fully populated selected or rejected entry.

Implement _attempt_stream_postprocess(args, entry). It uses the selected entry's in-memory records and the current download/process_records logic without calling fetch_material_diagnosis again. Preserve rpaFetchSec, diagnosisRecordWriteSec, videoDownloadSec, frameExtractionSec, and totalSec timing fields.

- [ ] **Step 4: Implement completion-driven scheduling**

Replace the Top-N portion of main() with _run_streaming_rpa_batch(args, top_materials_payload). Start independent ThreadPoolExecutors using rpa_concurrency and postprocess_concurrency. Submit source-ranked RPA candidates while selected count is below target and there is RPA capacity. Process FIRST_COMPLETED RPA futures as follows:

~~~python
if entry["rpaStatus"] == "selected" and len(selected) < target:
    selected[index] = entry
    postprocess_futures[post_pool.submit(
        _attempt_stream_postprocess, args, entry
    )] = index
else:
    rejected[index] = entry
~~~

Persist the manifest after every RPA and postprocess transition. RPA rejection immediately allows the next rank to submit. When selected reaches target, prohibit all new RPA submissions; drain already submitted work and record successful extras as rejected with failureReason top_n_already_selected. Mark unsubmitted candidates cancelled with failureReason target_satisfied. Do not postprocess extras.

A postprocess failure retains selected RPA state, sets postprocessStatus to failed, and ends nonzero without substituting a new candidate. Candidate exhaustion before the RPA-selected count reaches target also exits with code 5 and retains all artifacts.

- [ ] **Step 5: Run tests to verify success**

Run: python -m unittest tests.test_peak_frames.CliTests.test_streaming_top9_all_success_submits_only_first_n tests.test_peak_frames.CliTests.test_streaming_top9_two_rejections_submit_only_rank10_and_rank11 tests.test_peak_frames.CliTests.test_streaming_rpa_channel_never_exceeds_three_in_flight tests.test_peak_frames.CliTests.test_streaming_starts_postprocess_before_other_rpa_jobs_finish -v

Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add scripts/extract_peak_frames.py tests/test_peak_frames.py
git commit -m "feat: stream RPA fill and postprocess"
~~~

### Task 3: Preserve rank order and partial artifacts

**Files:**
- Modify: scripts/extract_peak_frames.py:_run_streaming_rpa_batch, _verify_rpa_batch
- Test: tests/test_peak_frames.py

- [ ] **Step 1: Write failing rank and terminal-state tests**

Make rank 3 complete before rank 1, reject rank 2, and fill with rank 4:

~~~python
assert [row["materialId"] for row in manifest["selectedMaterials"]] == ["m-1", "m-3", "m-4"]
assert [row["materialId"] for row in ready_payload["materials"]] == ["m-1", "m-3", "m-4"]
assert [row["materialId"] for row in manifest["rejectedMaterials"]] == ["m-2"]
~~~

Add a target-three exhausted candidate-pool test where only two candidates pass RPA. Assert exit code 5, two retained diagnosis JSON files, two selected rows, concrete rejection reasons, and the appropriate cancelled rows. Add a postprocess failure test asserting selected postprocessStatus is failed with InputDataError and no later candidate is submitted as a replacement.

- [ ] **Step 2: Run tests to verify failure**

Run: python -m unittest tests.test_peak_frames.CliTests.test_streaming_rebuilds_selected_order_after_out_of_order_rpa tests.test_peak_frames.CliTests.test_streaming_retains_partial_artifacts_when_candidate_pool_exhausts tests.test_peak_frames.CliTests.test_streaming_postprocess_failure_is_not_rpa_rejection -v

Expected: FAIL until all retained sequences are rebuilt by immutable candidate rank.

- [ ] **Step 3: Implement rank-ordered artifact rebuilding**

Create a helper equivalent to:

~~~python
def _ordered_entries(entries):
    return [entries[index] for index in sorted(entries)]
~~~

Use it for every manifest list and top_materials_rpa_ready.json. Update verification to require selected entries with postprocessStatus succeeded, preserve selected rank order, and include rejected/cancelled counts and reasons in its schema-version-2 receipt. Completion order must not leak into final-report inputs.

- [ ] **Step 4: Run tests to verify success**

Run: python -m unittest tests.test_peak_frames.CliTests.test_streaming_rebuilds_selected_order_after_out_of_order_rpa tests.test_peak_frames.CliTests.test_streaming_retains_partial_artifacts_when_candidate_pool_exhausts tests.test_peak_frames.CliTests.test_streaming_postprocess_failure_is_not_rpa_rejection -v

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add scripts/extract_peak_frames.py tests/test_peak_frames.py
git commit -m "test: cover streaming RPA terminal states"
~~~

### Task 4: Update operator and artifact contracts

**Files:**
- Modify: SKILL.md
- Modify: references/rpa-output-contract.md
- Modify: tests/test_skill_contract.py

- [ ] **Step 1: Write failing documentation assertions**

~~~python
assert "--target-top-n 9" in skill
assert "--candidate-top-n 27" in skill
assert "--rpa-concurrency 3" in skill
assert "--postprocess-concurrency 3" in skill
assert "候选上限，不是 RPA 调用数量承诺" in skill
assert "成功即后处理" in skill
assert "submittedMaterials" in contract
assert "cancelledMaterials" in contract
assert "top_n_already_selected" in contract
~~~

Keep assertions for asset export, dates, resume, verification, files envelopes, and one-material debugging.

- [ ] **Step 2: Run tests to verify failure**

Run: python -m unittest tests.test_skill_contract -v

Expected: FAIL because current docs prescribe a complete --preflight-only stage before extraction.

- [ ] **Step 3: Document the single streaming command and state meanings**

In SKILL.md, document one command using top_materials.json, targetTopN, candidateTopN, rpaConcurrency, and postprocessConcurrency. State that the asset table is total-cost ordered, candidateTopN is an upper bound rather than a request count, RPA success immediately starts later work, and final order follows source rank.

In references/rpa-output-contract.md, replace formal preflight workflow language with streaming selection. Define submitted, selected, rejected, cancelled, top_n_already_selected, target_satisfied, existing RPA failures, and separate postprocess failures. Preserve CSV-envelope and normalized-record durability guarantees.

- [ ] **Step 4: Run tests to verify success**

Run: python -m unittest tests.test_skill_contract -v

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add SKILL.md references/rpa-output-contract.md tests/test_skill_contract.py
git commit -m "docs: describe streaming RPA batch workflow"
~~~

### Task 5: Full verification

**Files:**
- Verify: tests/test_peak_frames.py
- Verify: tests/test_skill_contract.py
- Verify: tests/test_final_report.py

- [ ] **Step 1: Run the affected suites**

Run: python -m unittest tests.test_peak_frames tests.test_skill_contract tests.test_final_report -v

Expected: PASS including legacy single-material, files-envelope, resume, verification, and final-report tests.

- [ ] **Step 2: Run static and behavioral checks**

Run: python -m py_compile scripts/extract_peak_frames.py scripts/rpa_adapter.py && git diff --check && python -m unittest tests.test_peak_frames.CliTests.test_streaming_top9_all_success_submits_only_first_n tests.test_peak_frames.CliTests.test_streaming_top9_two_rejections_submit_only_rank10_and_rank11 tests.test_peak_frames.CliTests.test_streaming_starts_postprocess_before_other_rpa_jobs_finish tests.test_peak_frames.CliTests.test_streaming_rebuilds_selected_order_after_out_of_order_rpa -v

Expected: all checks PASS, proving exact submission bounds, rank-specific fill, immediate postprocessing, and rank-stable outputs.

- [ ] **Step 3: Inspect and commit the completed change**

Run: git status --short && git diff --check

Expected: preserve existing user changes and stage only reviewed implementation, tests, documentation, and this plan.

~~~bash
git add scripts/extract_peak_frames.py scripts/rpa_adapter.py tests/test_peak_frames.py SKILL.md references/rpa-output-contract.md tests/test_skill_contract.py docs/superpowers/plans/2026-07-28-qianchuan-streaming-rpa-fill-and-postprocess.md
git commit -m "feat: stream Qianchuan RPA Top-N processing"
~~~

