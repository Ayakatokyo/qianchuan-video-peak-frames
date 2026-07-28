# Final Report Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the final Qianchuan report from a standalone single-file HTML template with embedded JSON data.

**Architecture:** Python remains responsible for reading and validating business facts, producing `final-report-data.json`, and injecting the same JSON into the HTML template. The template owns report markup, CSS, and tab interaction, so future UI changes happen mostly in one HTML file.

**Tech Stack:** Python standard library, existing `unittest` tests, static HTML/CSS/JavaScript.

---

### Task 1: Lock Template Injection Behavior

**Files:**
- Modify: `tests/test_final_report.py`

- [ ] **Step 1: Write the failing test**

Add assertions to the existing render tests that generated HTML includes:

```python
self.assertIn('<script type="application/json" id="report-data">', html_text)
self.assertIn('"materials"', html_text)
self.assertNotIn("__REPORT_DATA_JSON__", html_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportRenderTests -v
```

Expected: FAIL because current report HTML does not embed the data script.

### Task 2: Move Report Shell Into Template

**Files:**
- Create: `templates/final-report-template.html`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: Add template file**

Create a standalone HTML template containing CSS, JavaScript, and the placeholder:

```html
<script type="application/json" id="report-data">__REPORT_DATA_JSON__</script>
```

- [ ] **Step 2: Implement minimal renderer change**

Change `scripts/final_report_lib.py` so `_render_html_report(data, asset_base_dir)`:

```python
template = _load_report_template()
safe_data = _report_data_for_template(data, asset_base_dir)
return template.replace("__REPORT_DATA_JSON__", _json_for_html_script(safe_data))
```

Keep existing validation and `final-report-data.json` output unchanged.

- [ ] **Step 3: Run focused tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report -v
```

Expected: PASS.

### Task 3: Regenerate Current Report And Verify

**Files:**
- Generated: `runs/2026-07-28-last7-top6-1770096415226951-verifyfix/final-report/final-report.html`

- [ ] **Step 1: Re-render latest report**

Run:

```bash
.venv/bin/python scripts/render_final_report.py \
  --top-materials-json runs/2026-07-28-last7-top6-1770096415226951-verifyfix/peak-frames/top_materials_rpa_ready.json \
  --peak-output-dir runs/2026-07-28-last7-top6-1770096415226951-verifyfix/peak-frames \
  --diagnosis-records-dir runs/2026-07-28-last7-top6-1770096415226951-verifyfix/diagnosis-records \
  --analysis-json runs/2026-07-28-last7-top6-1770096415226951-verifyfix/diagnostic-analysis/diagnostic-ai-analysis.json \
  --output-dir runs/2026-07-28-last7-top6-1770096415226951-verifyfix/final-report
```

- [ ] **Step 2: Run regression checks**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile scripts/*.py
git diff --check
```

Expected: all commands pass.
