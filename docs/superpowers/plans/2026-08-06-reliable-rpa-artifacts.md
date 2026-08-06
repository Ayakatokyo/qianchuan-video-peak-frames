# Reliable RPA Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every completed local RPA CSV/records artifact through a durable per-material receipt, then recover it on `--resume` without invoking RPA.

**Architecture:** A receipt module owns atomic JSON publication, SHA-256 validation, and reconstruction of selected wave entries. The isolated RPA worker writes `artifact_downloaded` immediately after a verified CSV exists, then `records_persisted` after the normalized records file is atomically visible. The parent scheduler maintains the manifest as a derived view; resume validates receipts before it schedules new wave work.

**Tech Stack:** Python 3 standard library, `unittest`, existing `path_safety`, `rpa_runtime`, `extract_peak_frames`, and wave scheduler.

---

### Task 1: Define the durable receipt contract

**Files:**
- Create: `scripts/rpa_artifact_receipts.py`
- Create: `tests/test_rpa_artifact_receipts.py`

- [ ] **Step 1: Write failing receipt lifecycle tests**

```python
def test_publish_records_persisted_receipt_includes_csv_digest_and_paths(self):
    receipt = publish_receipt(..., state="records_persisted", records_path=records)
    self.assertEqual(receipt["state"], "records_persisted")
    self.assertEqual(receipt["csvSha256"], hashlib.sha256(csv.read_bytes()).hexdigest())

def test_load_reconciled_receipt_rejects_changed_csv(self):
    publish_receipt(..., state="records_persisted", records_path=records)
    csv.write_text("changed", encoding="utf-8")
    with self.assertRaises(ReceiptError):
        load_records_persisted_receipt(...)
```

- [ ] **Step 2: Run test to verify RED**

Run: `pytest tests/test_rpa_artifact_receipts.py -q`

Expected: FAIL because `rpa_artifact_receipts` does not exist.

- [ ] **Step 3: Implement atomic receipt publication and validation**

```python
RECEIPT_SCHEMA_VERSION = 1
RECEIPT_STATES = ("submitted", "artifact_downloaded", "records_persisted")

def publish_receipt(receipts_dir, *, material_id, client_attempt_id, state,
                    csv_path=None, records_path=None):
    # Validate state and artifacts, compute SHA-256, then os.replace JSON staging file.

def load_records_persisted_receipt(receipts_dir, *, material_id, base_dir):
    # Require matching material, regular CSV/records files, digest, and retained record.
```

- [ ] **Step 4: Run test to verify GREEN**

Run: `pytest tests/test_rpa_artifact_receipts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/rpa_artifact_receipts.py tests/test_rpa_artifact_receipts.py
git commit -m "feat: add durable rpa artifact receipts"
```

### Task 2: Publish receipts inside the isolated worker

**Files:**
- Modify: `scripts/rpa_runtime.py`
- Modify: `scripts/extract_peak_frames.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing lifecycle-order test**

```python
def test_isolated_worker_publishes_csv_receipt_before_records_receipt(self):
    outcome = run_isolated_preflight(..., publish_receipt=published.append)
    self.assertEqual([state for state, _ in published],
                     ["artifact_downloaded", "records_persisted"])
```

- [ ] **Step 2: Run test to verify RED**

Run: `pytest tests/test_peak_frames.py -q -k receipt`

Expected: FAIL because `run_isolated_preflight` has no receipt callback.

- [ ] **Step 3: Add worker publication hooks and submission identifiers**

```python
def _worker_entry(..., persist_records=None, publish_receipt=None):
    outcome = worker(params)
    if outcome.get("rpaAvailable") and outcome.get("diagnosisCsvPath"):
        publish_receipt(params, outcome, "artifact_downloaded")
    # Retain records atomically, then publish records_persisted.
```

- [ ] **Step 4: Run lifecycle and RPA module tests**

Run: `pytest tests/test_peak_frames.py tests/test_rpa_batch_runner.py -q`

Expected: PASS, including the default three-material wave tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/rpa_runtime.py scripts/extract_peak_frames.py tests/test_peak_frames.py
git commit -m "feat: publish rpa receipts from isolated worker"
```

### Task 3: Make receipt state the resume recovery source

**Files:**
- Modify: `scripts/extract_peak_frames.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing resume tests**

```python
def test_wave_resume_restores_valid_receipt_before_any_rpa_submission(self):
    restored = _resume_wave_state(args, candidates, entries)
    self.assertEqual(restored[1][0]["rpaStatus"], "selected")
    self.assertEqual(restored[4], [0])

def test_wave_resume_rejects_malformed_receipt_without_calling_rpa(self):
    with self.assertRaises(RpaAdapterError, match="local RPA artifact receipt"):
        _resume_wave_state(args, candidates, entries)
```

- [ ] **Step 2: Run test to verify RED**

Run: `pytest tests/test_peak_frames.py -q -k "receipt and resume"`

Expected: FAIL because resume only trusts manifest/records paths.

- [ ] **Step 3: Reconcile receipts before manifest state**

```python
def _resume_wave_state(args, candidates, entries):
    # Reconcile records_persisted receipts for previous submitted work.
    # Valid receipts rebuild selected state and queue only missing postprocess.
    # Invalid or missing expected receipts raise RpaAdapterError; no fetch occurs.
```

- [ ] **Step 4: Run resume, worker, and scheduler suites**

Run: `pytest tests/test_peak_frames.py tests/test_rpa_batch_runner.py tests/test_rpa_artifact_receipts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_peak_frames.py tests/test_peak_frames.py
git commit -m "feat: reconcile rpa receipts on resume"
```

### Task 4: Verify the complete branch

**Files:**
- Modify: `docs/superpowers/plans/2026-08-06-reliable-rpa-artifacts.md`

- [ ] **Step 1: Mark completed implementation steps in this plan**

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Inspect the final diff and branch status**

Run: `git diff feature/qianchuan-top50-rpa-integration...HEAD --check && git status --short --branch`

Expected: no whitespace errors; only receipt implementation and plan changes.

- [ ] **Step 4: Commit the implementation plan status**

```bash
git add docs/superpowers/plans/2026-08-06-reliable-rpa-artifacts.md
git commit -m "docs: record rpa artifact receipt implementation"
```

