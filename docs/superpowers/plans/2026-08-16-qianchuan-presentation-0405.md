# 千川 PresentationReport 04/05 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a validated Qianchuan PresentationReport v1 projection for chapters 04 and 05, persist it beside the legacy report data, and let the HTML consume it with legacy fallback.

**Architecture:** `qianchuan_presentation.py` transforms the existing final report object without changing AI normalization or quality gates. `generate_final_report()` writes both JSON artifacts. The renderer exposes the presentation object while preserving all legacy fields so the current template remains compatible during the first migration slice.

**Tech Stack:** Python 3, unittest, existing HTML/JS template pipeline.

---

### Task 1: Presentation projection contract

**Files:**
- Create: `scripts/qianchuan_presentation.py`
- Test: `tests/test_qianchuan_presentation.py`

- [ ] Write tests for v1 top-level shape, chapter04 fixed category order, chapter05 material tabs, stable IDs, and unavailable states for missing frames/legacy AI.
- [ ] Run `python -m unittest tests.test_qianchuan_presentation -v` and confirm failure because the module is absent.
- [ ] Implement `build_presentation_report(data)` and `validate_presentation_report(report)` with deterministic projection helpers.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Persist presentation artifact

**Files:**
- Modify: `scripts/final_report_lib.py:generate_final_report`
- Test: `tests/test_final_report.py`

- [ ] Add a failing assertion that `generate_final_report()` writes `presentation-report.json` containing `presentationSchemaVersion: 1`.
- [ ] Run the focused test and confirm failure.
- [ ] Import the projection module, build presentation from `data`, atomically write the new JSON, and return the existing tuple unchanged.
- [ ] Run the focused test and confirm it passes.

### Task 3: Renderer schema exposure and fallback

**Files:**
- Modify: `scripts/report_renderer.py:report_data_for_template`
- Modify: `templates/final-report-template.html` only where chapter 04/05 readers initialize data
- Test: `tests/test_final_report.py`

- [ ] Add a failing renderer test proving `template_data.presentation` is present when supplied and absent-data rendering still exposes legacy `materials`/`conclusion`.
- [ ] Run the focused test and confirm failure.
- [ ] Pass through validated presentation data and add a small template accessor that prefers `reportData.presentation.chapters.chapter04/05` while retaining current fallback paths.
- [ ] Run renderer and existing final-report tests.

### Task 4: Full verification

**Files:**
- No new files.

- [ ] Run `python -m unittest discover -s tests -p 'test_*.py' -v`.
- [ ] Inspect generated JSON shape and HTML script payload in a temporary report fixture.
- [ ] Record any unrelated pre-existing failures separately; do not weaken existing AI quality gates.
