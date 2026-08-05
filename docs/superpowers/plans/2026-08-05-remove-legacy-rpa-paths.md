# Remove Legacy RPA Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Top-N wave scheduling the only RPA execution path while retaining three concurrent RPA calls per wave by default.

**Architecture:** `extract_peak_frames.py` will accept RPA only with a ranked candidate file and a Top-N target, then hand off to a renamed wave scheduler. The standalone preflight receipt path, legacy material batch, their flags, and modules reachable only through them are removed. RPA calls remain process-isolated and wave-barrier scheduling remains intact.

**Tech Stack:** Python 3.9, `argparse`, `unittest`, JSON manifest contracts.

---

## File Structure

- Modify: `scripts/extract_peak_frames.py` - one Top-N RPA CLI branch and wave helper names.
- Modify: `scripts/rpa_batch_runner.py` - active scheduler renamed; legacy scheduler deleted.
- Modify: `scripts/batch_verification.py` - active wave-verification API renamed.
- Modify: `scripts/schemas.py` - unused preflight-receipt validator removed.
- Delete: `scripts/rpa_preflight.py`, `scripts/material_processing.py`.
- Modify: `tests/test_peak_frames.py`, `tests/test_rpa_batch_runner.py`, `tests/test_schemas.py`.
- Modify: `SKILL.md`, `references/rpa-output-contract.md`, `references/qianchuan-operations-runbook.md`, `tests/test_skill_contract.py`.

### Task 1: Lock the new CLI boundary

**Files:**
- Modify: `tests/test_peak_frames.py:CliTests`
- Modify: `scripts/extract_peak_frames.py:build_parser` and `main`

- [ ] **Step 1: Write the failing tests**

```python
def test_rpa_cli_rejects_removed_compatibility_flags(self):
    parser = extract_peak_frames.build_parser()
    for flag in (
        "--preflight-only",
        "--rpa-preflight-json",
        "--allow-rpa-refetch",
        "--no-rpa-wave-barrier",
    ):
        with self.assertRaises(SystemExit) as raised:
            parser.parse_args([flag])
        self.assertEqual(raised.exception.code, 2)

def test_rpa_cli_rejects_single_material_mode(self):
    parser = extract_peak_frames.build_parser()
    with self.assertRaises(SystemExit) as raised:
        parser.parse_args(["--material-id", "m-1"])
    self.assertEqual(raised.exception.code, 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_cli_rejects_removed_compatibility_flags tests.test_peak_frames.CliTests.test_rpa_cli_rejects_single_material_mode`

Expected: FAIL because the old flags and `--material-id` are still accepted.

- [ ] **Step 3: Write minimal CLI implementation**

Remove `--material-id`, `--preflight-only`, `--rpa-preflight-json`, `--allow-rpa-refetch`, and `--no-rpa-wave-barrier`. Require the RPA command shape below and set the barrier internally:

```python
if args.source == "rpa":
    if not args.top_materials_json or not args.target_top_n:
        parser.error(
            "--source rpa requires --top-materials-json and --target-top-n"
        )
    args.rpa_wave_barrier = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_cli_rejects_removed_compatibility_flags tests.test_peak_frames.CliTests.test_rpa_cli_rejects_single_material_mode`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add scripts/extract_peak_frames.py tests/test_peak_frames.py`

Run: `git commit -m "refactor: restrict rpa cli to top-n waves"`

### Task 2: Remove old schedulers and rename the active path

**Files:**
- Modify: `scripts/extract_peak_frames.py:_stream_entry` through `_run_streaming_rpa_batch`
- Modify: `scripts/rpa_batch_runner.py:stream_entry` and `run_streaming_batch`
- Modify: `scripts/batch_verification.py:verify_streaming_batch`
- Modify: `tests/test_peak_frames.py`, `tests/test_rpa_batch_runner.py`, `tests/test_schemas.py`
- Delete: `scripts/rpa_preflight.py`, `scripts/material_processing.py`

- [ ] **Step 1: Write the failing wave API test**

In `tests/test_rpa_batch_runner.py`, rename the fixture and call site to the intended API:

```python
@staticmethod
def _wave_entry(material, rank):
    return rpa_batch_runner.wave_entry(material, rank, lambda: {})

return rpa_batch_runner.run_wave_batch(
    args, candidate_payload,
    wave_entry=self._wave_entry,
    resume_state=lambda *_args: ({}, {}, {}, {}, []),
    attempt_rpa=attempt_rpa,
    attempt_postprocess=attempt_postprocess,
    merge_timings=self._merge_timings,
    new_manifest=self._new_manifest,
    write_json=self._write_json,
    manifest_path="manifest",
    ready_payload=lambda _payload, materials: {"materials": materials},
    ready_path="ready",
    entry_result=lambda entry: entry["summaryJson"],
    elapsed=lambda _started: 0.0,
    accounted_indices=rpa_batch_runner.accounted_indices,
    ordered=rpa_batch_runner.ordered,
)
```

Rename the blocking assertion to `test_wave_does_not_submit_next_candidate_until_current_wave_finishes`, preserving its check that a fourth candidate is blocked before the first wave completes.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_rpa_batch_runner.StreamingBatchRunnerTests.test_wave_does_not_submit_next_candidate_until_current_wave_finishes`

Expected: FAIL because `wave_entry` and `run_wave_batch` do not exist.

- [ ] **Step 3: Write minimal implementation**

Rename `stream_entry` to `wave_entry` and `run_streaming_batch` to `run_wave_batch`; rename the injection parameter to `wave_entry`. Rename matching active helpers in `extract_peak_frames.py` and `batch_verification.py` from `streaming` to `wave`, then call `rpa_batch_runner.run_wave_batch()` from `_run_wave_rpa_batch()`.

Delete `run_legacy_batch()` and these old-path helpers from `extract_peak_frames.py`: `_preflight_receipt`, `_preflight_path`, `_run_rpa_preflight`, `_run_rpa_batch`, `_attempt_rpa_material`, `_process_rpa_material`, `_load_preflight_records`, and the receipt-only `main()` branch. Delete `scripts/rpa_preflight.py` and `scripts/material_processing.py` after `git grep` confirms no remaining imports. Delete `validate_preflight_receipt` and its schema tests.

Keep `rpa_runtime.py` and per-material availability/recovery calls: the active wave runner invokes them once per candidate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_rpa_batch_runner tests.test_peak_frames.CliTests.test_wave_defaults_to_three_concurrent_rpa_requests tests.test_peak_frames.CliTests.test_wave_rpa_channel_never_exceeds_three_in_flight`

Expected: PASS. Rename the two current `CliTests` to the names used above, retaining their three-concurrent and full-wave assertions.

- [ ] **Step 5: Commit**

Run: `git add -u scripts tests`

Run: `git add scripts/extract_peak_frames.py scripts/rpa_batch_runner.py scripts/batch_verification.py scripts/schemas.py tests/test_peak_frames.py tests/test_rpa_batch_runner.py tests/test_schemas.py`

Run: `git commit -m "refactor: remove legacy rpa execution paths"`

### Task 3: Align active contracts and documentation

**Files:**
- Modify: `SKILL.md`, `references/rpa-output-contract.md`, `references/qianchuan-operations-runbook.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: Write the failing skill-contract test**

```python
def test_rpa_contract_documents_only_wave_top_n_execution(self):
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    contract = (ROOT / "references" / "rpa-output-contract.md").read_text(
        encoding="utf-8"
    )
    for removed_flag in (
        "--preflight-only",
        "--rpa-preflight-json",
        "--allow-rpa-refetch",
        "--no-rpa-wave-barrier",
    ):
        self.assertNotIn(removed_flag, skill)
        self.assertNotIn(removed_flag, contract)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_skill_contract.StandardWorkflowSkillContractTests.test_rpa_contract_documents_only_wave_top_n_execution`

Expected: FAIL because the RPA contract currently documents the compatibility flags.

- [ ] **Step 3: Write minimal documentation changes**

Delete the compatibility-tools paragraph from `references/rpa-output-contract.md`. Change active scheduler wording from “streaming” to “wave”, retaining historical filenames under `docs/superpowers/`. Update the runbook so recovery starts from manifest, CSV, and records artifacts and does not reference preflight receipt reuse or legacy batch. Update `SKILL.md` only if it names a removed command.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_skill_contract.StandardWorkflowSkillContractTests.test_rpa_contract_documents_only_wave_top_n_execution`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add SKILL.md references/rpa-output-contract.md references/qianchuan-operations-runbook.md tests/test_skill_contract.py`

Run: `git commit -m "docs: document wave-only rpa execution"`

### Task 4: Full regression and removal audit

**Files:**
- Verify: `scripts/`, `tests/`, `SKILL.md`, `references/`

- [ ] **Step 1: Search live references to deleted paths**

Run: `rg -n "(run_legacy_batch|rpa_preflight|material_processing|preflight-only|rpa-preflight-json|allow-rpa-refetch|no-rpa-wave-barrier|run_streaming_batch|stream_entry)" scripts tests SKILL.md references`

Expected: no results. Historical plans and specs are intentionally excluded.

- [ ] **Step 2: Run full project tests**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: PASS with zero failures.

- [ ] **Step 3: Audit final change set**

Run: `git status --short`

Run: `git diff HEAD~3 --check`

Expected: only intended commits and no whitespace errors.

- [ ] **Step 4: Commit an audit correction only if needed**

Run: `git add -A`

Run: `git commit -m "test: verify wave-only rpa workflow"`

Skip this step when the audit required no correction.
