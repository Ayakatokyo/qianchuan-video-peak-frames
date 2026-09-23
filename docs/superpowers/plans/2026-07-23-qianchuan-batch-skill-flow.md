# Qianchuan Batch Skill Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `SKILL.md` require asset-table TOP-material selection before batch RPA diagnosis, while preserving the selection JSON as a run artifact.

**Architecture:** The two existing CLIs remain independent. `SKILL.md` becomes the batch orchestrator: it establishes one explicit date range, writes a run-local `top_materials.json`, validates that artifact, then passes it to the existing RPA peak-frame CLI. A small static contract test protects the documented sequence and the single-material boundary.

**Tech Stack:** Markdown, Python 3 `unittest`, existing command-line scripts.

---

### Task 1: Add A Batch Skill Contract Test

**Files:**
- Create: `tests/test_skill_contract.py`
- Test: `tests/test_skill_contract.py`

- [ ] **Step 1: Write the failing test for asset-first batch ordering**

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"


class BatchSkillContractTests(unittest.TestCase):
    def test_batch_workflow_exports_and_reuses_run_local_top_materials(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        batch_start = skill.index("## Batch TOP Materials Run")
        export_start = skill.index(
            "python scripts/export_top_materials.py", batch_start
        )
        rpa_start = skill.index(
            "python scripts/extract_peak_frames.py", batch_start
        )

        self.assertLess(export_start, rpa_start)
        self.assertIn('--output "/path/to/run/top_materials.json"', skill)
        self.assertIn(
            '--top-materials-json "/path/to/run/top_materials.json"', skill
        )
        self.assertIn("--custom-start-date 2026-06-01", skill)
        self.assertIn("--custom-end-date 2026-06-07", skill)
```

- [ ] **Step 2: Run the new test to verify it fails against the current skill**

Run: `.venv/bin/python -m unittest tests.test_skill_contract -v`

Expected: FAIL with `ValueError: substring not found`, because the current heading is `## TOP Materials RPA Run` and the document does not prescribe the run-local output paths.

- [ ] **Step 3: Extend the test with the single-material boundary**

Add this method to `BatchSkillContractTests`:

```python
    def test_single_material_rpa_is_documented_as_debugging_only(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        batch_start = skill.index("## Batch TOP Materials Run")
        single_start = skill.index("## Single-Material RPA Debug Run")

        self.assertLess(batch_start, single_start)
        self.assertIn(
            "not an alternative selection method for a TOP-material batch run",
            skill,
        )
```

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/test_skill_contract.py
git commit -m "test: define asset-first batch skill contract"
```

### Task 2: Make The Skill Batch Flow Asset-First

**Files:**
- Modify: `SKILL.md:12-63`
- Test: `tests/test_skill_contract.py`

- [ ] **Step 1: Replace the generic workflow and TOP RPA section with an ordered batch workflow**

Update `SKILL.md` so the workflow begins with this rule and has a `## Batch TOP Materials Run` section before all single-material RPA guidance:

````markdown
For an online TOP-material batch, always select materials from the asset table before invoking RPA. Use one explicit inclusive start/end date range for both operations. Do not reuse a selection JSON from another run or period.

## Batch TOP Materials Run

Choose one empty run directory. The selection JSON is a required, retained batch artifact.

```bash
python scripts/export_top_materials.py \
  --start-date 2026-06-01 \
  --end-date 2026-06-07 \
  --top-n 50 \
  --output "/path/to/run/top_materials.json"
```

Before RPA, inspect `top_materials.json`. Stop when `materials` is empty, an entry lacks `materialId`, or `actualStartDate`/`actualEndDate` differs from the requested range.

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/peak-frames"
```

Retain `top_materials.json`, normalized records, peak-frame summaries, JPEGs, and warnings. The current RPA `result_type=files` CSV-envelope response is blocking until its adapter integration is completed; do not report it as successful.
````

- [ ] **Step 2: Move the existing direct RPA example below the batch section**

Rename its heading and introduce it with this text:

```markdown
## Single-Material RPA Debug Run

Use this only to diagnose or replay one already known material. `--material-id` is not an alternative selection method for a TOP-material batch run.
```

Keep the existing `--source rpa --material-id ...` command and connector explanation unchanged below that introduction.

- [ ] **Step 3: Update Quick Reference and Common Mistakes**

Replace the online-source quick-reference row with:

```markdown
| TOP-material batch | Run asset-table selection first, then pass its run-local `top_materials.json` to RPA with the same CUSTOM dates |
| Single-material debug | Pass `--source rpa --shop-id ... --material-id ... --date-range-type ...` |
```

Add these bullets under `## Common Mistakes`:

```markdown
- Do not call batch RPA before asset-table selection or use `--material-id` to bypass it.
- Do not use different ranking and diagnosis date ranges.
- Do not overwrite or discard the run-local `top_materials.json`.
- Treat an RPA `result_type=files` response as a blocking adapter limitation until CSV-envelope integration is available.
```

- [ ] **Step 4: Run the skill contract test and verify it passes**

Run: `.venv/bin/python -m unittest tests.test_skill_contract -v`

Expected: `OK`; both tests pass.

- [ ] **Step 5: Commit the skill guidance**

```bash
git add SKILL.md tests/test_skill_contract.py
git commit -m "docs: make batch skill asset-table first"
```

### Task 3: Run Regression Verification

**Files:**
- Verify: `SKILL.md`
- Verify: `tests/test_skill_contract.py`
- Verify: `tests/test_peak_frames.py`
- Verify: `tests/test_top_materials.py`
- Verify: `tests/test_rpa_csv_parser.py`

- [ ] **Step 1: Run the full Python suite**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: `OK` with no failures or errors.

- [ ] **Step 2: Inspect the documented batch command sequence**

Run: `sed -n '1,180p' SKILL.md`

Expected: `export_top_materials.py` appears in `## Batch TOP Materials Run` before `extract_peak_frames.py`; both commands use the same shown start/end dates and the same run-local `top_materials.json` path.

- [ ] **Step 3: Confirm the outstanding adapter boundary is stated accurately**

Run: `rg -n "result_type=files|CSV-envelope|Single-Material RPA Debug Run" SKILL.md`

Expected: the skill marks file-envelope handling as blocking and names direct `--material-id` usage as debugging only.

- [ ] **Step 4: Commit verification-only documentation adjustment if inspection found one**

Run: `git status --short`

Expected: no staged or unstaged changes related to this plan. Do not create an empty commit.

## Plan Self-Review

- Spec coverage: Task 2 implements asset-first ordering, matched dates, retained run-local selection JSON, stopping conditions, the debug-only single-material boundary, and the known CSV-envelope limitation. Task 1 makes the critical order and boundary regressions observable. Task 3 verifies the full suite and final document.
- Placeholder scan: no incomplete implementation steps or unspecified commands remain.
- Consistency: the exact heading and path strings asserted in Task 1 are created in Task 2.
