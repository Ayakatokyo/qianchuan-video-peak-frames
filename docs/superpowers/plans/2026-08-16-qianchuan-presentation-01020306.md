# 千川 PresentationReport 01/02/03/06 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project report chapters 01, 02, 03, and 06 into validated PresentationReport v1 contracts and migrate the HTML readers to prefer those contracts with legacy fallback.

**Architecture:** Extend `qianchuan_presentation.py` with chapter-specific pure projections from existing final-report data. The renderer retains legacy data, while HTML uses the chapter projection when available and otherwise keeps present behavior. AI schemas and quality gates remain unchanged.

**Tech Stack:** Python 3, `unittest`, static HTML/JavaScript renderer, standard-library `zipfile`.

---

### Task 1: Project the four chapter contracts

**Files:**
- Modify: `scripts/qianchuan_presentation.py`
- Modify: `tests/test_qianchuan_presentation.py`

- [ ] Add a fixture containing `topMaterialRoi.metrics`, v5 `portfolioDimensionConclusions`, `audience.metrics`/`preferences`, `scripts.labelSummary`, `batchInsights`, and `nextActions`.
- [ ] Add failing assertions for `chapter01.tabs == ["commercial", "audience", "script", "highlight", "interaction"]`, audience bucket data, script level-one/level-two hierarchy, and high-priority action-card data.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation.PresentationTests -v`; expect absent projected chapter fields.
- [ ] Add `CHAPTER01_TABS`, shared mapping/list/ref helpers, plus `_chapter01(data)`, `_chapter02(data, material_ids)`, `_chapter03(data, material_ids)`, and `_chapter06(data, material_ids)`.
- [ ] Every projection emits `id`, `title`, `source`, `evidenceRefs`, `availability`, stable empty collections, and material references restricted to report-level material IDs.
- [ ] Change `report.sections` to the fixed `chapter01` through `chapter06` sequence and replace old placeholders with these helpers.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation -v`; expect PASS.
- [ ] Commit `scripts/qianchuan_presentation.py` and `tests/test_qianchuan_presentation.py` with `feat: project qianchuan presentation chapters`.

### Task 2: Validate contracts and degradation behavior

**Files:**
- Modify: `scripts/qianchuan_presentation.py`
- Modify: `tests/test_qianchuan_presentation.py`

- [ ] Add failing tests that set an unknown action priority and unknown action/bucket/script material ID, and remove source facts or evidence refs.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation.PresentationTests -v`; expect validation failures until checks exist.
- [ ] Extend `validate_presentation_report()` to check fixed chapter section order, chapter 01 tab order, all projected material IDs, and action priority in `high`, `medium`, `low`.
- [ ] Mark projections `partial` for missing evidence/source facts and `unavailable` only for no usable chapter content; preserve report generation.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation -v`; expect PASS.

### Task 3: Migrate HTML reads while retaining fallback

**Files:**
- Modify: `templates/final-report-template.html`
- Modify: `tests/test_qianchuan_presentation.py`

- [ ] Add static template tests requiring a `presentationChapter(id)` accessor plus readers for chapters 01, 02, 03, and 06.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation.PresentationTests -v`; expect failure because those readers are absent.
- [ ] Add `presentationChapter(id)`, accepting `available` and `partial` chapter states.
- [ ] Update `renderTopDimensionConclusions`, the audience insights/dashboard, script insights/dashboard, and `renderNextActions` to select presentation fields first and use present legacy expressions as fallback.
- [ ] Preserve keyboard tabs, chart click drill-down, material links, labels, and empty states.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation tests.test_final_report -v`; expect PASS.
- [ ] Commit template and tests with `feat: render qianchuan presentation chapters`.

### Task 4: Require Presentation JSON in distributed reports

**Files:**
- Modify: `scripts/report_renderer.py`
- Modify: `tests/test_final_report.py`

- [ ] Add a failing archive assertion for `final-report/presentation-report.json`.
- [ ] Run `python -m unittest tests.test_final_report.ReportRendererTests -v`; expect the archive requirement test to fail.
- [ ] Add `presentation-report.json` to the renderer required-file list and archive contents alongside HTML and legacy JSON.
- [ ] Run `python -m unittest tests.test_final_report -v`; expect PASS.

### Task 5: Verify source, artifact, and browser behavior

**Files:**
- No production changes expected.

- [ ] Run `python -m py_compile scripts/qianchuan_presentation.py scripts/report_renderer.py scripts/final_report_lib.py`.
- [ ] Run `python -m unittest discover -s tests -p 'test_*.py' -v`; expect exit code 0.
- [ ] Regenerate the Top18 report using the repository’s established report command and parse the new `presentation-report.json` with `python -m json.tool`.
- [ ] Rebuild the zip and run `unzip -l` to confirm HTML, legacy JSON, and Presentation JSON are included.
- [ ] Open the generated report in the browser; inspect 01 five tabs, 02 metric and drill-down behavior, 03 script drill-down, 06 priority order, and desktop/mobile layout.
- [ ] Run `git diff --check`, then commit implementation with `feat: migrate qianchuan presentation chapters`.
