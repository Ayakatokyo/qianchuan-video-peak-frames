# 千川第四部分“本批次分类” Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在第四部分新增“本批次分类”外层 Tab，并让 AI 视觉理解、证据校验、Presentation 投影和 HTML 展示形成可追溯的另一套分类体系。

**Architecture:** 保留现有 `highlightDashboard.groups` 标准三分类作为标准面板数据源，新增 `customClassification` 作为 AI 生成的本批次分类数据源。后端只校验和投影合法 `highlight:*` 引用，HTML 增加外层二 Tab并复用现有帧卡片、灯箱、素材跳转和分页能力。

**Tech Stack:** Python 3.10+、pytest、Markdown prompt、JSON facts/schema、HTML/JavaScript/CSS、Node `--check`。

**Spec:** `docs/superpowers/specs/2026-09-04-qianchuan-highlight-batch-classification-design.md`

## Global Constraints

- 对外名称固定为“本批次分类”，不得出现“自定义规则分类”作为报告展示名称。
- 标准分类的 `portrait`、`product`、`other` 数据、文案、顺序和既有交互必须保持兼容，并在同一个标准分类面板内展示。
- AI 不生成帧数、占比、覆盖率或帧集合；所有确定性数值由 Presentation 根据成功帧 registry 计算。
- 同一套本批次分类中每张成功高光帧必须恰好属于一个 group；未归类帧必须显式记录。
- 每个有效本批次分组固定输出 3 条 `commonConclusion` 和 3 条 `derivativeDirections`。
- 不重复执行 RPA，不覆盖 `runs/` 下既有报告、分析 JSON、原始 CSV 和高光帧。
- 第一部分当前 T+1 上新口径与 `newMaterialCount7d=52` 保持不变，本计划不调整日期窗口。

---

### Task 1: 扩展 Chapter 04 AI 契约与失败测试

**Files:**
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`
- Modify: `scripts/final_report_lib.py`
- Test: `tests/test_final_report.py`
- Test: `tests/test_diagnostic_analysis.py`

**Interfaces:**
- Consumes: Chapter 04 `highlight:*` evidence references and current v5 conclusion payload.
- Produces: normalized conclusion payload containing optional `customClassification` with `ruleId`, `ruleName`, `ruleDescription`, `ruleVersion`, `inputFrameManifestHash`, `groups`, `unclassifiedFrameRefs`, and `availability`.

- [ ] **Step 1: Write failing contract tests**

Add tests that pass a valid standard dashboard plus an invalid `customClassification` and assert `FinalReportError` for: unknown frame reference, duplicate frame reference across groups, a placeholder group name `其他1`, and a valid group with fewer than three common conclusions or directions. Add a positive fixture with two groups covering all successful frame refs exactly once.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest -q tests/test_final_report.py tests/test_diagnostic_analysis.py`

Expected: FAIL because the current validator ignores or rejects the new field and has no custom-classification coverage checks.

- [ ] **Step 3: Update the prompt contract**

In the Chapter 04 section of `prompts/diagnostic-analysis-prompt-v1.md`, require one `customClassification` object named “本批次分类”. State that group names must describe observable visual/content mechanisms, cannot use `其他1`/`其他2`, must use only successful Chapter 04 JPEG evidence, and must output exactly 3 common conclusions plus 3 derivative directions per group. State that AI must not output computed frame counts or percentages.

- [ ] **Step 4: Implement minimal validation**

Extend the conclusion normalizer and quality gate in `scripts/final_report_lib.py` with a helper whose behavior is:

```python
def _validate_custom_classification(raw, expected_frame_refs):
    # return normalized object or raise FinalReportError
    # validate rule metadata, >=2 non-empty groups, unique semantic names,
    # exact successful-frame coverage, explicit unclassified refs,
    # group-local evidenceRefs, and 3+3 insight arrays
```

Keep the field optional for v4/v5 legacy payloads. For an invalid optional custom classification, return a normalized `availability="partial"` object only when the invalid references can be isolated; never mutate the standard `groups`.

- [ ] **Step 5: Run the focused tests and verify they pass**

Run: `python -m pytest -q tests/test_final_report.py tests/test_diagnostic_analysis.py`

Expected: PASS, including legacy v4/v5 fixtures and all new custom-classification cases.

- [ ] **Step 6: Commit the contract change**

```bash
git add prompts/diagnostic-analysis-prompt-v1.md scripts/final_report_lib.py tests/test_final_report.py tests/test_diagnostic_analysis.py
git commit -m "feat: add qianchuan batch highlight classification contract"
```

### Task 2: Project “本批次分类” without duplicating frame facts

**Files:**
- Modify: `scripts/qianchuan_presentation.py`
- Modify: `scripts/report_renderer.py`
- Test: `tests/test_qianchuan_presentation.py`
- Test: `tests/test_final_report.py`

**Interfaces:**
- Consumes: normalized `customClassification` and canonical successful frame registry from Chapter 04 facts.
- Produces: `presentation.chapters.chapter04.classificationModes` with `standard` and optional `batch`, plus `batchClassification` containing projected groups, computed `frameCount`, `percentage`, `coverage`, and availability.

- [ ] **Step 1: Write failing projection tests**

Assert that a valid payload produces the fixed mode order `["standard", "batch"]`, that standard groups remain `portrait/product/other`, that projected batch counts equal `len(frameRefs)`, and that no batch group contains raw `framePath` or duplicated full frame objects. Add tests for absent custom classification and partial custom classification.

- [ ] **Step 2: Run the projection tests and verify they fail**

Run: `python -m pytest -q tests/test_qianchuan_presentation.py tests/test_final_report.py`

Expected: FAIL because `_chapter04()` currently returns only `categoryOrder` and `groups`.

- [ ] **Step 3: Implement the projection**

Extend `_chapter04(data, materials)` so it keeps the current standard `groups` map unchanged and adds a `classificationModes` list plus `batchClassification`. Compute each group’s `frameCount` and `percentage` from the validated refs and the canonical successful frame-ref set. Copy only AI metadata and insight objects; resolve frame cards later from the existing Chapter 05 canonical frame facts.

- [ ] **Step 4: Preserve renderer compatibility**

Keep the existing report-data fields used by old reports. Add the new mode only when the normalized payload has a valid or partial custom classification. Ensure `report_renderer.py` continues to use local relative assets and does not copy signed URLs or raw frame paths into the new projection.

- [ ] **Step 5: Run the projection tests and verify they pass**

Run: `python -m pytest -q tests/test_qianchuan_presentation.py tests/test_final_report.py`

Expected: PASS with old and new payloads.

- [ ] **Step 6: Commit the projection change**

```bash
git add scripts/qianchuan_presentation.py scripts/report_renderer.py tests/test_qianchuan_presentation.py tests/test_final_report.py
git commit -m "feat: project batch highlight classification"
```

### Task 3: Add the two-mode Chapter 04 UI

**Files:**
- Modify: `templates/final-report-template.html`
- Modify: `assets/report/unified-report.css`
- Modify: `assets/report/unified-report.js`
- Test: `tests/test_qianchuan_presentation.py`
- Test: `tests/test_final_report.py`

**Interfaces:**
- Consumes: `presentation.chapters.chapter04.classificationModes`, standard groups, batch classification groups, and canonical frame facts.
- Produces: accessible Chapter 04 markup with top-level tabs `标准分类` and `本批次分类`; standard content remains one panel and batch content has its own panel.

- [ ] **Step 1: Write failing HTML/JS contract tests**

Add assertions against rendered HTML/source that require exactly the visible labels `标准分类` and `本批次分类`, a single standard panel containing the three standard category blocks, a batch panel with rule metadata, and no visible label `自定义规则分类`. Add a JS source assertion for keyboard selection updating `aria-selected` and `hidden` without removing standard group data.

- [ ] **Step 2: Run the UI tests and verify they fail**

Run: `python -m pytest -q tests/test_final_report.py tests/test_qianchuan_presentation.py`

Expected: FAIL because the current renderer exposes portrait/product/other as the only category tabs and has no batch mode.

- [ ] **Step 3: Implement the outer Tab controller**

Add a top-level `activateHighlightModeTab(tab)` and use it from `renderHighlightDashboard()`. Render two `role="tab"` buttons in stable order. The standard panel calls the existing category rendering path and does not alter its facts or labels; the batch panel is created only when `batchClassification` exists and otherwise shows a truthful empty state.

- [ ] **Step 4: Implement batch cards and reuse frame behavior**

Add `renderBatchClassificationPanel(batchClassification, factsByReference)` to render rule metadata, group summaries, insight text, frame cards and pagination. Reuse `renderDashboardFrame`, `renderHighlightFramePage`, `renderHighlightPagination`, `openFrameLightbox`, and `activateMaterialById`; do not duplicate frame path lookup logic.

- [ ] **Step 5: Add scoped responsive styles**

Add only `.highlight-batch-*` and outer mode-tab selectors. Preserve existing standard-classification selectors and ensure the new rule text, group labels and frame grid wrap within desktop, tablet and mobile widths.

- [ ] **Step 6: Run UI tests and static checks**

Run: `python -m pytest -q tests/test_final_report.py tests/test_qianchuan_presentation.py`; `node --check assets/report/unified-report.js`; `node --check templates/final-report-template.html`.

Expected: PASS and no JavaScript syntax errors. If the HTML template is not accepted by Node directly, run the project’s existing template extraction check and record the exact command/result.

- [ ] **Step 7: Commit the UI change**

```bash
git add templates/final-report-template.html assets/report/unified-report.css assets/report/unified-report.js tests/test_final_report.py tests/test_qianchuan_presentation.py
git commit -m "feat: add qianchuan batch classification tab"
```

### Task 4: Generate and validate a fresh real report

**Files:**
- Modify: none in source; generate only a new isolated directory under `runs/20260904-top15-20260825-20260830/` with a date-stamped report name.
- Test: `tests/test_highlight_export.py`

**Interfaces:**
- Consumes: existing latest `chapter04-highlight-input.json`, existing 89 successful frame assets, current prompt contract, and a validated AI JSON containing `customClassification`.
- Produces: fresh `final-report.html`, `final-report-data.json`, `presentation-report.json`, and ZIP; existing report directories remain unchanged.

- [ ] **Step 1: Create a real-batch validation fixture from existing facts**

Use the latest batch’s canonical frame refs and JPEGs. If no model-call entry point is available, create the real-batch AI payload by applying the approved prompt contract to the already inspected frame set and record that the AI JSON is a replay fixture; do not invent frame paths or counts.

- [ ] **Step 2: Run the standard validation and render into a new directory**

Use the project’s standard finalize/render entry point with the existing run directory as input and a new output directory. Do not overwrite `final-report/` or prior dated report directories.

- [ ] **Step 3: Verify the report data contract**

Check that the new JSON contains both mode IDs, standard categories remain unchanged, batch group frame refs are mutually exclusive, all 89 successful frames are covered, and every batch group has 3 common conclusions plus 3 derivative directions.

- [ ] **Step 4: Verify the HTML contract**

Parse the generated HTML and assert the labels `标准分类` and `本批次分类`, absence of `自定义规则分类`, a standard panel with portrait/product/other content, a batch panel with rule metadata, valid image references, Excel download link, and no credentials or signed URLs.

- [ ] **Step 5: Verify ZIP and run the full suite**

Run: `python -m pytest -q`; `python -m compileall -q scripts tests`; `node --check assets/report/unified-report.js`; `git diff --check`.

Inspect the ZIP contents and confirm it contains the expected report assets without credentials, debug files, unrelated historical reports, or an unexpected duplicate Excel file.

- [ ] **Step 6: Commit the verified implementation**

```bash
git add prompts/diagnostic-analysis-prompt-v1.md scripts/final_report_lib.py scripts/qianchuan_presentation.py scripts/report_renderer.py templates/final-report-template.html assets/report/unified-report.css assets/report/unified-report.js tests
git commit -m "feat: complete qianchuan batch highlight classification"
```

### Plan self-review

- The design requirement for the name “本批次分类” is covered in the prompt, schema, projection, UI labels and acceptance checks.
- Standard portrait/product/other content remains a single standard panel and is never projected as three report-level tabs.
- All deterministic counts and frame coverage are computed outside AI and outside browser-side inference.
- Legacy v4/v5 payloads have an explicit no-batch fallback and are not forced through the new field.
- Existing Excel, lightbox, pagination, material navigation and ZIP behaviors have explicit regression checks.
