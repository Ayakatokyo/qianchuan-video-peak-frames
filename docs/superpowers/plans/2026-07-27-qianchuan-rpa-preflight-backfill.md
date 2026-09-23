# Qianchuan RPA Preflight Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an RPA preflight-and-backfill stage that filters out materials without currently usable RPA diagnosis data before the formal batch frame-extraction run begins.

**Architecture:** Keep asset-table export and formal RPA frame extraction separate, then insert a lightweight preflight layer between them. The adapter owns low-level RPA diagnosis probing, while the CLI owns candidate scanning, failure classification, backfill selection, and publication of `rpa-preflight.json` plus `top_materials_rpa_ready.json`.

**Tech Stack:** Python 3.9+, `unittest`, existing `scripts/export_top_materials.py`, `scripts/extract_peak_frames.py`, `scripts/rpa_adapter.py`, JSON batch artifacts.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `scripts/rpa_adapter.py` | Lightweight RPA preflight probe that validates record availability and `material_info.videoUrl` presence |
| `scripts/extract_peak_frames.py` | Batch preflight orchestration, candidate backfill, ready-list publication, and early-stop enforcement |
| `tests/test_peak_frames.py` | Regression and new CLI tests for preflight, backfill, and early stop |
| `SKILL.md` | Updated operational workflow describing candidate export, preflight, and formal RPA execution |
| `references/rpa-output-contract.md` | Clarify preflight pass/fail signals and artifact expectations |

### Task 1: Add failing adapter tests for preflight signals

**Files:**
- Modify: `tests/test_peak_frames.py`
- Modify: `scripts/rpa_adapter.py`

- [ ] **Step 1: Write a failing test for successful preflight detection**

Add a focused `RpaAdapterTests` case asserting a new helper returns pass metadata when records include matching `material_info.videoUrl`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_peak_frames.RpaAdapterTests.test_preflight_material_availability_passes_with_video_url -v
```

Expected: FAIL because the new helper does not exist.

- [ ] **Step 3: Write a failing test for missing `material_info`**

Assert the helper classifies the failure as `missing_material_info`.

- [ ] **Step 4: Write a failing test for empty `videoUrl`**

Assert the helper classifies the failure as `missing_video_url`.

- [ ] **Step 5: Implement the minimal adapter helper**

Add a pure helper plus a wrapper that reuses `fetch_material_diagnosis()` and returns structured preflight outcomes.

- [ ] **Step 6: Run the adapter-focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_peak_frames.RpaAdapterTests.test_preflight_material_availability_passes_with_video_url \
  tests.test_peak_frames.RpaAdapterTests.test_preflight_material_availability_rejects_missing_material_info \
  tests.test_peak_frames.RpaAdapterTests.test_preflight_material_availability_rejects_empty_video_url -v
```

Expected: PASS.

### Task 2: Add failing CLI tests for candidate backfill

**Files:**
- Modify: `tests/test_peak_frames.py`
- Modify: `scripts/extract_peak_frames.py`

- [ ] **Step 1: Write a failing test that backfills after the top candidate fails preflight**

Use a candidate pool of at least four materials where the first fails preflight, the next three pass, and assert the ready-list contains the latter three in ranking order.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_preflight_backfills_after_failed_top_candidate -v
```

Expected: FAIL because no preflight/backfill stage exists.

- [ ] **Step 3: Write a failing test for preflight-only early stop**

Assert that when the candidate pool is exhausted before filling the requested target count, the CLI exits before formal frame extraction and writes `rpa-preflight.json` with `satisfied=false`.

- [ ] **Step 4: Write a failing test for formal execution using the ready list**

Assert that after preflight succeeds, formal batch processing is invoked only for `selectedMaterials`, not for rejected candidates.

- [ ] **Step 5: Implement minimal CLI preflight orchestration**

Add candidate scanning, backfill selection, ready-list writing, and early-stop enforcement before formal RPA processing begins.

- [ ] **Step 6: Run the new focused CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_peak_frames.CliTests.test_rpa_preflight_backfills_after_failed_top_candidate \
  tests.test_peak_frames.CliTests.test_rpa_preflight_stops_before_formal_batch_when_candidates_exhausted \
  tests.test_peak_frames.CliTests.test_rpa_formal_batch_consumes_only_ready_materials -v
```

Expected: PASS.

### Task 3: Add batch artifact and contract coverage

**Files:**
- Modify: `tests/test_peak_frames.py`
- Modify: `scripts/extract_peak_frames.py`
- Modify: `references/rpa-output-contract.md`

- [ ] **Step 1: Write a failing test for `rpa-preflight.json` structure**

Assert the artifact includes `requestedTopN`, `candidateCount`, `selectedMaterials`, `rejectedMaterials`, and `satisfied`.

- [ ] **Step 2: Write a failing test for `top_materials_rpa_ready.json` content**

Assert it preserves the selected material metadata in the same contract shape as the original top-material file.

- [ ] **Step 3: Implement artifact writing**

Use atomic writes and preserve existing date-range plus filter metadata.

- [ ] **Step 4: Update the RPA contract reference**

Document preflight pass/fail criteria, failure classes, and the distinction between candidate and ready lists.

- [ ] **Step 5: Run the focused artifact tests and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_peak_frames.CliTests.test_rpa_preflight_writes_preflight_receipt \
  tests.test_peak_frames.CliTests.test_rpa_preflight_writes_ready_top_materials_json -v
```

Expected: PASS.

### Task 4: Update skill workflow and regression coverage

**Files:**
- Modify: `SKILL.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Write a failing contract test for the updated workflow**

Assert the skill now documents candidate export, RPA preflight/backfill, and formal consumption of `top_materials_rpa_ready.json`.

- [ ] **Step 2: Run the skill contract test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_skill_contract.SkillContractTests.test_batch_workflow_preflights_and_backfills_before_formal_rpa -v
```

Expected: FAIL because the skill text has not been updated.

- [ ] **Step 3: Update `SKILL.md`**

Document:

- export candidate pool
- run RPA preflight
- inspect `rpa-preflight.json`
- execute formal batch only from the ready list
- stop early if `satisfied=false`

- [ ] **Step 4: Run the focused contract test and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_skill_contract.SkillContractTests.test_batch_workflow_preflights_and_backfills_before_formal_rpa -v
```

Expected: PASS.

### Task 5: Run regression verification

**Files:**
- Verify: `tests/test_peak_frames.py`
- Verify: `tests/test_skill_contract.py`

- [ ] **Step 1: Run the full peak-frame test suite**

Run:

```bash
.venv/bin/python -m unittest tests.test_peak_frames -v
```

Expected: PASS.

- [ ] **Step 2: Run the skill contract suite**

Run:

```bash
.venv/bin/python -m unittest tests.test_skill_contract -v
```

Expected: PASS.

- [ ] **Step 3: Run formatting and diff checks**

Run:

```bash
git diff --check
```

Expected: silent output.
