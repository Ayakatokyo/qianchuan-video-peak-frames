# 第 03 至 05 章链路可追溯性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让第 03、04、05 章与第 01、02 章一致，以 Presentation 作为当前 schema v5 的唯一展示入口，并保留事实包与 AI 输出的可追溯来源。

**Architecture:** 不改变事实包或 AI JSON schema。`qianchuan_presentation.py` 将事实字段和 AI 字段投影到各章节，附带来源、证据和可用性；模板在 Presentation 存在时只读取对应章节，旧报告才使用原始合并数据回退。

**Tech Stack:** Python 3、unittest/pytest、静态 HTML/JavaScript 模板。

---

### Task 1: 锁定第 03 至 05 章投影契约

**Files:**
- Modify: `tests/test_qianchuan_presentation.py:87-122`
- Modify: `scripts/qianchuan_presentation.py:156-302`

- [ ] **Step 1: 写失败的 Presentation 回归测试**

```python
self.assertEqual(chapter03["source"], "facts.scripts + ai.batchInsights[script]")
self.assertEqual(chapter03["groups"][0]["source"], "facts.scripts.labelSummary")
self.assertEqual(chapter03["insights"][0]["source"], "ai.batchInsights[script]")
self.assertEqual(chapter04["source"], "facts.materials.highlightFrames + ai.highlightDashboard")
self.assertEqual(chapter04["conclusion"]["source"], "ai.portfolioDimensionConclusions.highlight")
self.assertEqual(chapter05["materials"][0]["analysis"]["source"], "ai.materialAnalyses")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_qianchuan_presentation.py::PresentationTests::test_projects_new_chapter_contracts_with_stable_material_references`

Expected: FAIL，因为当前 03 的章节来源不含 AI，04 没有章节 `source` 与结构化 `conclusion`，05 的 `analysis` 没有 AI 来源。

- [ ] **Step 3: 实现最小投影补充**

```python
# _chapter03
group["source"] = "facts.scripts.labelSummary"
insight["source"] = "ai.batchInsights[script]"
# chapter source
"source": "facts.scripts + ai.batchInsights[script]"

# _chapter04
highlight_text, highlight_refs = _dimension(conclusion, "highlight")
"conclusion": {
    "text": highlight_text,
    "evidenceRefs": highlight_refs,
    "source": "ai.portfolioDimensionConclusions.highlight",
    "availability": _availability(bool(highlight_text), bool(highlight_refs)),
}
"source": "facts.materials.highlightFrames + ai.highlightDashboard"

# _chapter05
analysis["source"] = "ai.materialAnalyses"
```

保留现有事实字段和 AI 文案；不新增 AI 输入/输出字段。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/test_qianchuan_presentation.py`

Expected: PASS。

### Task 2: 使 HTML 的当前报告路径只消费 Presentation

**Files:**
- Modify: `tests/test_final_report.py:2445-2480,2702-2710`
- Modify: `templates/final-report-template.html:111-139,2150-2225,2283-2300`

- [ ] **Step 1: 写失败的模板契约测试**

```python
self.assertIn('function chapter04Conclusion()', html_text)
self.assertIn('const chapter = presentationChapter("chapter04");', html_text)
self.assertIn('const chapter = presentationChapter("chapter05");', html_text)
self.assertIn('const defaultMaterialId = chapter05DefaultMaterialId();', html_text)
```

测试还应断言 `renderHighlightDashboard()` 不再以 `reportData.conclusion` 作为当前 Presentation 章节的输入。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_final_report.py -k 'chapter03 or chapter04 or chapter05'`

Expected: FAIL，因为当前模板没有 `chapter04Conclusion()`/`chapter05DefaultMaterialId()`，且 04 渲染直接读取 `reportData.conclusion`。

- [ ] **Step 3: 实现当前 schema 的单一路径**

```javascript
function chapter04Conclusion() {
  const projected = presentationChapter("chapter04")?.conclusion;
  if (projected && typeof projected === "object") return projected;
  return reportData.conclusion?.portfolioDimensionConclusions?.highlight || {};
}

function chapter05DefaultMaterialId() {
  const chapter = presentationChapter("chapter05");
  return text(chapter?.defaultMaterialId || reportData.defaultMaterialId);
}
```

将 `chapter04Groups()` 和 `chapter05Materials()` 改为从 `presentationChapter()` 获取当前 v1 投影；仅在无 Presentation 时读取旧 `reportData`。`renderHighlightDashboard()` 读取 `chapter04Conclusion()`，并将 schema v3 分支限定为旧报告回退。`renderMaterialTabs()` 使用 `chapter05DefaultMaterialId()`。

- [ ] **Step 4: 运行模板相关测试确认通过**

Run: `pytest -q tests/test_final_report.py -k 'chapter03 or chapter04 or chapter05'`

Expected: PASS。

### Task 3: 全链路验收

**Files:**
- Verify: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report/presentation-report.json`
- Verify: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report/final-report.html`
- Verify: `runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report.zip`

- [ ] **Step 1: 运行全量测试**

Run: `pytest -q`

Expected: 所有测试通过。

- [ ] **Step 2: 从真实事实包和 AI JSON 重建报告**

Run: `python -c 'import sys; from pathlib import Path; sys.path.insert(0, "scripts"); from final_report_lib import generate_final_report; from report_renderer import create_report_archive; root=Path("runs/qianchuan-run-20260729-20260804-top6-live-20260817"); report=root/"final-report"; generate_final_report(root/"peak-frames"/"top_materials_rpa_ready.json", root/"peak-frames", root/"diagnosis-records", report, analysis_json=root/"diagnostic-analysis"/"diagnostic-ai-analysis.json", copy_assets=True, require_ai_quality=True); create_report_archive(report, root/"final-report.zip")'`

- [ ] **Step 3: 验证真实投影与归档**

Run: `rg -n 'facts.scripts \+ ai.batchInsights\[script\]|facts.materials.highlightFrames \+ ai.highlightDashboard|ai.materialAnalyses' runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report/presentation-report.json && python -c 'from zipfile import ZipFile; print(ZipFile("runs/qianchuan-run-20260729-20260804-top6-live-20260817/final-report.zip").testzip())'`

Expected: 三个来源均存在，ZIP 输出 `None`。

- [ ] **Step 4: 提交实现**

Run: `git add scripts/qianchuan_presentation.py templates/final-report-template.html tests/test_qianchuan_presentation.py tests/test_final_report.py && git commit -m "feat: trace chapter03-05 report paths"`
