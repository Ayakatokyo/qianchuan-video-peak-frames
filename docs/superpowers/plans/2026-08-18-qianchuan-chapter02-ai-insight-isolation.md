# Qianchuan Chapter 02 AI Insight Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep the existing three AI-generated audience insights while isolating Chapter 02's prompt, projection, and HTML path from Chapter 01.

**Architecture:** Reuse `diagnostic-analysis-data.json.audience` as the shared deterministic source. Keep schema v5 and `batchInsights[topic=audience]`; add chapter-scoped prompt rules and project those existing records into `presentation.chapters.chapter02.insights`, while removing the unnecessary `chapter01.batchInsights` bridge. Legacy reports fall back directly to their own `final-report-data.json.batchInsights.audience`.

**Tech Stack:** Python `unittest`, prompt contract tests, `qianchuan_presentation.py`, inline HTML template JavaScript, existing report generation and ZIP verification scripts.

---

### Task 1: Lock the isolation contract with failing tests

**Files:**
- Modify: `tests/test_qianchuan_presentation.py`
- Modify: `tests/test_final_report.py`
- Test: existing presentation and final-report test suites

- [ ] **Step 1: Add Presentation assertions for Chapter 02 insights**

Extend `complete_presentation_data()` with three audience `batchInsights` records whose titles are AI-generated strings and whose evidence refs use `SHOW_COUNT`, `CLICK_COUNT`, and `TRANSACTION_AMOUNT`. Assert `build_presentation_report(data)["chapters"]["chapter02"]["insights"]` preserves titles, conclusions, order, and refs.

- [ ] **Step 2: Add a failing assertion that Chapter 01 has no batch insight bridge**

Assert the projected `chapter01` does not contain `batchInsights`, while the projected `chapter02` does contain the three audience records. Run:

```bash
python -m unittest tests.test_qianchuan_presentation.PresentationTests -v
```

Expected: FAIL because `_chapter01()` still projects `batchInsights` and `_chapter02()` has no `insights` field.

- [ ] **Step 3: Add HTML source assertions for the isolated read path**

In `tests/test_final_report.py`, assert the template defines a Chapter 02 insight reader using `presentationChapter("chapter02")`, renders that reader for `audience-primary`, and does not call `chapter01Insights("audience")` for Chapter 02. Keep a separate assertion that the legacy fallback reads `reportData.batchInsights?.audience` directly.

- [ ] **Step 4: Add failing tests for same-metric evidence groups**

Add a quality-gate fixture with three audience insights and assert mixed evidence such as `SHOW_COUNT` plus `CLICK_COUNT` in one insight is rejected, while one insight per metric with AI-generated titles is accepted. Run the focused final-report tests and record the expected failure before implementation.

### Task 2: Implement chapter-scoped prompt and quality rules

**Files:**
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`
- Modify: `scripts/final_report_lib.py`
- Test: `tests/test_diagnostic_analysis.py`, `tests/test_final_report.py`

- [ ] **Step 1: Split the prompt's audience rules by output field**

Replace the currently global audience wording with two explicit subsections: `portfolioDimensionConclusions.audience` for Chapter 01's 60-120-character combination summary, and `batchInsights[topic=audience]` for Chapter 02's exactly three metric-specific insights. State that titles remain AI-generated, that the three covered metrics are fixed, and that neither field may copy the other's title, conclusion, or action.

- [ ] **Step 2: Add prompt contract tests**

Assert the prompt contains the field-specific names, the three required metrics, AI-generated-title wording, the no-cross-copy rule, and the canonical audience terms. Keep existing tests for the five Chapter 01 dimension conclusions and three audience insights.

- [ ] **Step 3: Add same-metric evidence validation**

In `_validate_v2_analysis_quality()` or its adjacent audience validation helper, collect each audience insight's evidence refs, parse the `audience:{metric}:` prefix, reject an insight containing more than one metric, and require the set of metrics across audience insights to equal `{"SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT"}`. Do not add a new output field or change schema v5.

- [ ] **Step 4: Run focused quality tests**

```bash
python -m unittest tests.test_final_report.FinalReportV4HighlightDashboardTests tests.test_final_report.FinalReportQualityGateTests -v
python -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisAudienceTests -v
```

Expected: all new isolation and evidence-scope tests pass; legacy schema and noncanonical-term tests remain green.

### Task 3: Make Presentation ownership explicit

**Files:**
- Modify: `scripts/qianchuan_presentation.py`
- Test: `tests/test_qianchuan_presentation.py`

- [ ] **Step 1: Project audience insights into Chapter 02**

Filter `conclusion.batchInsights` for `topic == "audience"`, deep-copy each record, preserve AI title/conclusion/evidence refs, and add it as `chapter02.insights`. Update the Chapter 02 source string to `facts.audience + ai.batchInsights[audience]`.

- [ ] **Step 2: Remove Chapter 01's batchInsights field**

Stop copying the complete `conclusion.batchInsights` list into `_chapter01()`. Keep `overallConclusion`, the five ordered points, and their evidence refs unchanged. Do not change Chapter 03's existing `insights` projection in this task.

- [ ] **Step 3: Validate projected shape and compatibility**

Require `chapter02.insights` to be a list and preserve the three AI-generated titles in order when present. Keep old input data accepted; only the new presentation output changes. Run the complete presentation suite.

### Task 4: Remove the HTML Chapter 01 dependency

**Files:**
- Modify: `templates/final-report-template.html`
- Test: `tests/test_final_report.py`

- [ ] **Step 1: Add a dedicated Chapter 02 reader**

Implement `chapter02Insights()` that returns `presentationChapter("chapter02")?.insights` when available, otherwise directly returns `reportData.batchInsights?.audience` for legacy reports. It must never call `chapter01Insights()`.

- [ ] **Step 2: Update report rendering**

Change `renderReport()` to pass `chapter02Insights()` to `renderInsightList("audience-primary", ...)`. Leave Chapter 01 combination rendering and Chapter 03 fallback behavior unchanged.

- [ ] **Step 3: Run HTML contract tests**

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests -v
```

Expected: Chapter 02 source assertions pass and the existing inline-asset, fallback, and section tests remain green.

### Task 5: Rebuild and verify the real report

**Files:**
- Modify: none in source; regenerate the real run artifacts under `runs/qianchuan-run-20260729-20260804-top6-live-20260817/`
- Test: all repository tests and report artifact checks

- [ ] **Step 1: Run the full test suite and static checks**

```bash
python -m unittest discover -s tests -p 'test_*.py'
python -m compileall -q scripts tests
git diff --check
```

Expected: all tests pass, bytecode compilation succeeds, and diff check is clean.

- [ ] **Step 2: Regenerate the real report from the existing verified Top6 facts**

Use the repository's established report-generation entrypoint with the existing diagnostic AI JSON and verified records. Confirm `presentation-report.json` contains `chapter02.insights` with the three AI-generated titles and that `chapter01` has no `batchInsights` field.

- [ ] **Step 3: Verify the generated HTML and ZIP**

Inspect the generated report data and HTML for the direct Chapter 02 path, run ZIP `testzip()` verification, and confirm no new report section, schema version, RPA input, records file, or peak-frame artifact was changed.

- [ ] **Step 4: Commit the implementation**

```bash
git add prompts/diagnostic-analysis-prompt-v1.md scripts/final_report_lib.py scripts/qianchuan_presentation.py templates/final-report-template.html tests/test_diagnostic_analysis.py tests/test_final_report.py tests/test_qianchuan_presentation.py
git commit -m "fix: isolate chapter02 audience insights"
```
