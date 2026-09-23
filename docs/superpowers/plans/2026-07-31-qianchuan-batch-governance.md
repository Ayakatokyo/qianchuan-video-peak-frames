# 千川批次状态治理与半成品修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add explicit batch success-stage counts, run locking/stale-run recovery, and local-only manifest repair without repeating RPA requests.

**Architecture:** Keep the existing streaming scheduler and schema v2 manifest. Extract stage-count and lock/receipt helpers into focused modules, add a repair CLI that reuses the existing CSV parser and postprocess pipeline, and make report readiness an explicit derived state backed by local artifacts and validation.

**Tech Stack:** Python 3.9+, `argparse`, atomic JSON writes, `fcntl` where available with an exclusive-create fallback, `unittest`.

---

### Task 1: Define stage-count contract

**Files:**
- Modify: `scripts/extract_peak_frames.py`
- Modify: `scripts/final_report_lib.py`
- Test: `tests/test_peak_frames.py`
- Test: `tests/test_final_report.py`

- [x] Write failing tests asserting manifest/report summaries expose `rpaSelectedCount`, `recordsPersistedCount`, `postprocessSucceededCount`, and `reportReadyCount` independently.
- [x] Run the focused tests and confirm they fail because the new fields do not exist.
- [x] Add one shared status-count helper and populate the streaming manifest and final report pipeline summary without changing existing status arrays.
- [x] Run focused tests, then verify old schema-v2 fixtures remain valid.

### Task 2: Add run identity and lock protection

**Files:**
- Create: `scripts/batch_runtime.py`
- Modify: `scripts/extract_peak_frames.py`
- Modify: `tests/test_peak_frames.py`

- [x] Write failing tests for unique run IDs, active-lock rejection, stale-lock detection, and lock release after normal completion.
- [x] Run the focused tests and confirm failure on missing lock behavior.
- [x] Implement atomic lock acquisition with a JSON lock receipt containing run ID, PID, start time, and manifest path; classify stale locks without deleting them silently.
- [x] Integrate lock acquisition/release around streaming execution and expose a recovery-required error for stale `running/submitted` manifests.
- [x] Run focused lock and streaming resume tests.

### Task 3: Implement local repair library

**Files:**
- Create: `scripts/repair_manifest.py`
- Modify: `scripts/rpa_csv_parser.py`
- Modify: `scripts/extract_peak_frames.py`
- Test: `tests/test_repair_manifest.py`

- [x] Write failing tests for CSV-to-records repair, records-to-postprocess repair, summary/JPEG-to-manifest repair, and refusal to call RPA.
- [x] Run the focused repair tests and confirm the command/library is absent or rejects the cases.
- [x] Implement repair discovery and per-material actions using existing parsers, record writers, postprocess functions, and summary/JPEG validation.
- [x] Recompute stage counts, write a repair receipt atomically, and preserve explicit failures for missing/corrupt artifacts.
- [x] Run all repair tests and the existing peak-frame tests.

### Task 4: Wire CLI and documentation contract

**Files:**
- Modify: `scripts/extract_peak_frames.py`
- Modify: `SKILL.md`
- Modify: `references/rpa-output-contract.md`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_peak_frames.py`

- [x] Write failing CLI and contract tests for `repair-manifest`, lock behavior, stage counts, and recovery guidance.
- [x] Run focused tests and confirm failure.
- [x] Add the CLI entry point and document exact inputs, outputs, exit codes, and no-RPA guarantee.
- [x] Run focused CLI/contract tests.

### Task 5: Full verification

**Files:**
- No new production files.

- [x] Run `python -m py_compile scripts/*.py` for touched modules.
- [x] Run `git diff --check`.
- [x] Run `python -m unittest discover -s tests -p 'test_*.py' -v`.
- [x] Exercise repair against a temporary fixture and verify the resulting manifest, receipt, and stage counts.
- [x] Review the diff for unrelated changes and report remaining limitations.
