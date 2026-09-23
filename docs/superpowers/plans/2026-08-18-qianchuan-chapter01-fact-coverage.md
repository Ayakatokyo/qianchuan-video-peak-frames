# Qianchuan Chapter 01 Fact Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Chapter 01's traceable facts and AI constraints for interaction totals, audience dimensions, scripts, and the material-list label.

**Architecture:** Keep the existing diagnostic fact-pack schema as the single source of truth. Add a deterministic total beside each existing interaction metric distribution, then constrain the AI prompt to consume it without changing the AI output schema. Render the commercial conclusion through the existing key with a renamed presentation lead.

**Tech Stack:** Python 3, pytest, JSON fact packs, HTML presentation templates.

---

### Task 1: Lock down interaction totals

**Files:**
- Modify: `tests/test_diagnostic_analysis.py`
- Modify: `scripts/diagnostic_analysis_lib.py:109-158`

- [ ] **Step 1: Write the failing test**

```python
def test_build_diagnostic_analysis_exposes_interaction_totals_for_all_metrics(tmp_path):
    result = build_diagnostic_analysis(top_payload, records_by_material)

    interaction = result["interaction"]["metrics"]
    assert interaction["CLICK_COUNT"]["totalMetricValue"] == 30.0
    assert interaction["LOSE_COUNT"]["totalMetricValue"] == 15.0
    assert interaction["LIKE_COUNT"]["totalMetricValue"] == 9.0
    assert interaction["COMMENT_COUNT"]["totalMetricValue"] == 6.0
    assert interaction["SHARE_COUNT"]["totalMetricValue"] == 3.0
    assert interaction["NEW_FOLLOWER_COUNT"]["totalMetricValue"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_diagnostic_analysis.py::test_build_diagnostic_analysis_exposes_interaction_totals_for_all_metrics -q`

Expected: FAIL because `totalMetricValue` is absent.

- [ ] **Step 3: Write minimal implementation**

```python
metric_total = sum(total for _, total in material_values)
result[metric] = {
    "available": True,
    "sampleMaterialCount": len(material_values),
    "totalMetricValue": round(metric_total, 6),
    "buckets": buckets,
}
```

For unavailable metrics, retain the existing fields and add `"totalMetricValue": None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_diagnostic_analysis.py::test_build_diagnostic_analysis_exposes_interaction_totals_for_all_metrics -q`

Expected: PASS.

### Task 2: Enforce Chapter 01 language and AI coverage

**Files:**
- Modify: `tests/test_qianchuan_presentation.py`
- Modify: `tests/test_diagnostic_analysis.py`
- Modify: `scripts/qianchuan_presentation.py`
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`

- [ ] **Step 1: Write the failing tests**

```python
def test_chapter01_uses_material_list_lead():
    chapter = qianchuan_presentation._chapter01(facts, analysis)
    assert chapter["points"][0]["lead"] == "从素材列表数据来看："

def test_prompt_requires_full_interaction_audience_and_script_coverage():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "整体流失数、整体点赞次数、整体评论次数、整体转发次数、整体新增粉丝数" in prompt
    assert "八大人群、年龄、性别、地域" in prompt
    assert "一二级内容公式" in prompt
    assert "脚本文案" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_qianchuan_presentation.py::test_chapter01_uses_material_list_lead tests/test_diagnostic_analysis.py::test_prompt_requires_full_interaction_audience_and_script_coverage -q`

Expected: FAIL because the current lead and prompt requirements are incomplete.

- [ ] **Step 3: Write minimal implementation**

```python
("commercial", "从素材列表数据来看："),
```

Add prompt rules requiring all available interaction totals and distributions, the 3-by-4 audience matrix, and both script analysis lines with evidence and next-action requirements.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_qianchuan_presentation.py::test_chapter01_uses_material_list_lead tests/test_diagnostic_analysis.py::test_prompt_requires_full_interaction_audience_and_script_coverage -q`

Expected: PASS.

### Task 3: Update traceability and validate the real batch

**Files:**
- Modify: `docs/qianchuan-fact-pack-report-chapters-analysis-20260817.md`
- Regenerate: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/diagnostic-analysis/diagnostic-analysis-data.json`
- Regenerate: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/diagnostic-analysis/diagnostic-ai-analysis.json`
- Regenerate: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report/final-report.html`
- Regenerate: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report.zip`

- [ ] **Step 1: Document the fact-to-AI-to-report chain**

Update Chapter 01's mapping to document `interaction.metrics[*].totalMetricValue`, the three-metric/four-dimension audience matrix, the two script analysis lines, and the material-list presentation lead.

- [ ] **Step 2: Rebuild the retained fact pack**

Run: `python scripts/run_standard_analysis.py prepare --help`

Use the existing standard CLI's analysis-only regeneration path identified from its help output. Do not rerun external RPA, overwrite retained CSV, records, or peak frames.

- [ ] **Step 3: Generate and gate an updated AI JSON**

Use the regenerated fact pack and prompt. Reuse only unchanged AI fields; update the Chapter 01 AI conclusions to reference the new interaction total evidence. Validate with the standard `finalize` command.

- [ ] **Step 4: Run focused and full verification**

Run: `pytest tests/test_diagnostic_analysis.py tests/test_qianchuan_presentation.py tests/test_final_report.py -q`

Expected: PASS.

Run: `pytest -q`

Expected: PASS.
