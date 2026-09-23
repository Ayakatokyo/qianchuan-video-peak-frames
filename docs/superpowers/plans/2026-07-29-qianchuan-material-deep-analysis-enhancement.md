# Qianchuan Material Deep Analysis Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic interaction timeline and script-analysis fact displays to `04｜单素材深度分析` while preserving the current AI second-pass summary blocks and keeping video media out of the HTML payload.

**Architecture:** `scripts/final_report_lib.py` will attach compact per-material facts derived from existing diagnosis records and parsed script facts. `templates/final-report-template.html` will render raw fact cards before the existing AI interpretation cards, with chart drawing scoped to the active material panel. The AI prompt will clarify that AI material summaries should interpret visible facts instead of restating them.

**Tech Stack:** Python standard library, existing `unittest` suite, static HTML/CSS/JavaScript, inline SVG for lightweight charts.

---

### Task 1: Lock Material-Level Raw Fact Contract

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: Write failing tests for interaction timeline and script analysis**

Add these helper functions near the existing `click_record` helper in `tests/test_final_report.py`:

```python
def interaction_record(material_id, metric, second, value):
    return {
        "recordType": "interaction_series",
        "interactionMetric": metric,
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


def script_record(material_id):
    return {
        "recordType": "script_analysis",
        "materialId": material_id,
        "materialUri": f"uri-{material_id}",
        "scriptInfo": json.dumps(
            {
                "text": "早餐可以不碰碳水，但鲜炖燕窝一定要坚持吃。",
                "formula": [
                    {
                        "level_1_label": {"value": 1, "label": "商品卖点"},
                        "level_2_label": {"value": 11, "label": "适用场景"},
                    }
                ],
                "data": [
                    {
                        "level_1_label": {"value": 1, "label": "商品卖点"},
                        "level_2_labels": [
                            {"value": 11, "label": "适用场景"},
                            {"value": 12, "label": "使用感受"},
                        ],
                    }
                ],
                "detail": {
                    "11": {
                        "label": "适用场景",
                        "list": ["早餐可以不碰碳水，但鲜炖燕窝一定要坚持吃。"],
                    },
                    "12": {
                        "label": "使用感受",
                        "list": ["浓稠捞丝，吃起来口感 q 弹。"],
                    },
                },
            },
            ensure_ascii=False,
        ),
    }
```

Add this test method to `FinalReportDataTests`:

```python
def test_material_cards_include_interaction_timeline_and_script_analysis(self):
    top_payload = {
        "actualStartDate": "2026-06-01",
        "actualEndDate": "2026-06-30",
        "materials": [material("m-1")],
    }
    records_by_material = {
        "m-1": [
            interaction_record("m-1", "CLICK_COUNT", 2, 5),
            interaction_record("m-1", "CLICK_COUNT", 1, 9),
            interaction_record("m-1", "CLICK_COUNT", 3, 9),
            interaction_record("m-1", "LIKE_COUNT", 1, 2),
            interaction_record("m-1", "LOSE_COUNT", 1, 7),
            interaction_record("m-1", "COMMENT_COUNT", 1, 1),
            interaction_record("m-1", "SHARE_COUNT", 1, 3),
            interaction_record("m-1", "NEW_FOLLOWER_COUNT", 1, 4),
            script_record("m-1"),
        ]
    }
    summaries_by_material = {
        "m-1": {
            "summaryPath": "/run/peak-frames/m-1/summary.json",
            "frames": [],
            "warnings": [],
        }
    }

    result = build_final_report_data(
        top_payload,
        records_by_material,
        summaries_by_material,
        conclusion=None,
        batch_verification=None,
    )

    card = result["materials"][0]
    timeline = card["interactionTimeline"]
    self.assertEqual(
        [metric["metric"] for metric in timeline["metrics"]],
        [
            "CLICK_COUNT",
            "LOSE_COUNT",
            "LIKE_COUNT",
            "COMMENT_COUNT",
            "SHARE_COUNT",
            "NEW_FOLLOWER_COUNT",
        ],
    )
    click_metric = timeline["metrics"][0]
    self.assertEqual(click_metric["label"], "整体点击次数")
    self.assertEqual(click_metric["points"], [
        {"second": 1.0, "value": 9.0},
        {"second": 2.0, "value": 5.0},
        {"second": 3.0, "value": 9.0},
    ])
    self.assertEqual(click_metric["peak"], {"second": 1.0, "value": 9.0})
    script = card["scriptAnalysis"]
    self.assertEqual(script["materialUri"], "uri-m-1")
    self.assertEqual(script["text"], "早餐可以不碰碳水，但鲜炖燕窝一定要坚持吃。")
    self.assertIn({"level1": "商品卖点", "level2": "适用场景"}, script["labels"])
    self.assertIn({"level1": "商品卖点", "level2": "适用场景"}, script["formula"])
    self.assertEqual(script["evidence"][0]["level2"], "适用场景")
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportDataTests.test_material_cards_include_interaction_timeline_and_script_analysis -v
```

Expected: FAIL because `interactionTimeline` and `scriptAnalysis` are not attached to material cards yet.

- [ ] **Step 3: Add extraction helpers in `scripts/final_report_lib.py`**

Add constants near the existing top-level constants:

```python
INTERACTION_METRIC_LABELS = {
    "CLICK_COUNT": "整体点击次数",
    "LOSE_COUNT": "整体流失数",
    "LIKE_COUNT": "整体点赞次数",
    "COMMENT_COUNT": "整体评论次数",
    "SHARE_COUNT": "整体转发次数",
    "NEW_FOLLOWER_COUNT": "整体新增粉丝数",
}

INTERACTION_METRIC_ORDER = tuple(INTERACTION_METRIC_LABELS.keys())
```

Add helpers above `_material_card`:

```python
def _material_interaction_timeline(records):
    grouped = {metric: [] for metric in INTERACTION_METRIC_ORDER}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("recordType") != "interaction_series":
            continue
        metric = _text(record.get("interactionMetric"))
        if metric not in grouped:
            continue
        second = _number(record.get("durationSec"), default=None)
        value = _number(record.get("metricValue"), default=None)
        if second is None or value is None:
            continue
        grouped[metric].append({"second": second, "value": value})

    metrics = []
    for metric in INTERACTION_METRIC_ORDER:
        points = sorted(grouped[metric], key=lambda item: item["second"])
        if not points:
            continue
        peak = max(points, key=lambda item: (item["value"], -item["second"]))
        metrics.append(
            {
                "metric": metric,
                "label": INTERACTION_METRIC_LABELS[metric],
                "points": points,
                "peak": {"second": peak["second"], "value": peak["value"]},
            }
        )
    return {"metrics": metrics}


def _script_formula_from_record(record):
    script_info = record.get("scriptInfo")
    if not script_info:
        return []
    try:
        parsed = ast.literal_eval(script_info) if isinstance(script_info, str) else script_info
    except (ValueError, SyntaxError):
        return []
    if not isinstance(parsed, dict):
        return []
    formula = parsed.get("formula", [])
    if not isinstance(formula, list):
        return []
    result = []
    for item in formula:
        if not isinstance(item, dict):
            continue
        level1 = item.get("level_1_label") or {}
        level2 = item.get("level_2_label") or {}
        label1 = _text(level1.get("label") if isinstance(level1, dict) else "")
        label2 = _text(level2.get("label") if isinstance(level2, dict) else "")
        if label1 and label2:
            result.append({"level1": label1, "level2": label2})
    return result


def _scripts_by_material(diagnostic_scripts):
    result = {}
    for material in diagnostic_scripts.get("materials", []):
        if isinstance(material, dict):
            material_id = _text(material.get("materialId"))
            if material_id:
                result[material_id] = material
    return result


def _script_records_by_material(records_by_material):
    result = {}
    for material_id, records in records_by_material.items():
        for record in records:
            if isinstance(record, dict) and record.get("recordType") == "script_analysis":
                result[material_id] = record
                break
    return result


def _material_script_analysis(material_id, script_facts, script_records):
    facts = script_facts.get(material_id, {})
    record = script_records.get(material_id, {})
    formula = _script_formula_from_record(record) or list(facts.get("labels", []))
    return {
        "materialUri": _text(facts.get("materialUri") or record.get("materialUri")),
        "formula": formula,
        "labels": list(facts.get("labels", [])),
        "evidence": list(facts.get("evidence", [])),
        "text": _text(facts.get("text")),
    }
```

Add `ast` to the import block at the top of `scripts/final_report_lib.py`:

```python
import ast
```

- [ ] **Step 4: Pass records and script facts into material card construction**

Change `_material_card` signature:

```python
def _material_card(material, summary, analysis, records=None, script_analysis=None):
    frames = summary.get("frames", []) if isinstance(summary, dict) else []
    warnings = summary.get("warnings", []) if isinstance(summary, dict) else []
    extraction_status = "succeeded" if frames else "missing_summary"
    return {
        **material,
        "extractionStatus": extraction_status,
        "summaryPath": summary.get("summaryPath", "")
        if isinstance(summary, dict)
        else "",
        "highlightFrameCount": len(frames),
        "highlightFrames": frames,
        "warnings": warnings,
        "analysis": analysis or _empty_material_analysis(material["materialId"]),
        "interactionTimeline": _material_interaction_timeline(records or []),
        "scriptAnalysis": script_analysis
        or {
            "materialUri": "",
            "formula": [],
            "labels": [],
            "evidence": [],
            "text": "",
        },
        "video": {"src": "", "poster": "", "durationSec": None},
    }
```

Update `build_final_report_data` before `material_cards`:

```python
    script_facts = _scripts_by_material(diagnostic["scripts"])
    script_records = _script_records_by_material(records_by_material)
```

Update the `_material_card` call:

```python
        _material_card(
            material,
            summaries_by_material.get(material["materialId"], {}),
            analyses.get(material["materialId"]),
            records_by_material.get(material["materialId"], []),
            _material_script_analysis(
                material["materialId"], script_facts, script_records
            ),
        )
```

- [ ] **Step 5: Run focused data tests**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportDataTests.test_material_cards_include_interaction_timeline_and_script_analysis tests.test_final_report.FinalReportDataTests.test_builds_draft_report_without_conclusion_json -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: attach material timeline and script facts"
```

---

### Task 2: Preserve Raw Facts In Template Data Without Media Inlining

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: Write failing render-data test**

Add this method to `FinalReportRenderTests`:

```python
def test_embedded_template_data_includes_material_raw_facts_without_video_payload(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        top_path = root / "top_materials.json"
        records_dir = root / "diagnosis-records"
        peak_dir = root / "peak-frames"
        output_dir = root / "final-report"
        write_json(
            top_path,
            {
                "actualStartDate": "2026-06-01",
                "actualEndDate": "2026-06-30",
                "materials": [material("m-1", 1, 100)],
            },
        )
        write_json(
            records_dir / "m-1.json",
            [
                interaction_record("m-1", "CLICK_COUNT", 1, 9),
                interaction_record("m-1", "LIKE_COUNT", 1, 2),
                script_record("m-1"),
            ],
        )
        material_dir = peak_dir / "m-1"
        material_dir.mkdir(parents=True)
        frame_path = material_dir / "first_peak_0001s_9.jpg"
        frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
        write_json(
            material_dir / "summary.json",
            {
                "frames": [
                    {
                        "rankType": "first_peak",
                        "durationSec": 1,
                        "metricValue": 9,
                        "framePath": frame_path.name,
                    }
                ]
            },
        )

        _data_path, html_path, _data = generate_final_report(
            top_path,
            peak_dir,
            records_dir,
            output_dir,
        )

        html_text = html_path.read_text(encoding="utf-8")

    self.assertIn('"interactionTimeline"', html_text)
    self.assertIn('"label":"整体点击次数"', html_text)
    self.assertIn('"peak":{"second":1.0,"value":9.0}', html_text)
    self.assertIn('"scriptAnalysis"', html_text)
    self.assertIn('"formula"', html_text)
    self.assertIn('"早餐可以不碰碳水', html_text)
    self.assertIn('"video":{"src":"","poster":"","durationSec":null}', html_text)
    self.assertNotIn("data:video/", html_text)
    self.assertNotIn(";base64", html_text)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_embedded_template_data_includes_material_raw_facts_without_video_payload -v
```

Expected: FAIL because `_report_data_for_template` currently drops `interactionTimeline`, `scriptAnalysis`, and `video`.

- [ ] **Step 3: Pass compact raw fact fields into `_report_data_for_template`**

Inside `_report_data_for_template`, extend the object appended to `materials`:

```python
        materials.append(
            {
                "rank": material.get("rank"),
                "materialId": material.get("materialId"),
                "materialName": material.get("materialName"),
                "extractionStatus": material.get("extractionStatus"),
                "highlightFrameCount": material.get("highlightFrameCount"),
                "highlightFrames": highlight_frames,
                "interactionTimeline": material.get("interactionTimeline", {"metrics": []}),
                "scriptAnalysis": material.get(
                    "scriptAnalysis",
                    {
                        "materialUri": "",
                        "formula": [],
                        "labels": [],
                        "evidence": [],
                        "text": "",
                    },
                ),
                "video": material.get(
                    "video",
                    {"src": "", "poster": "", "durationSec": None},
                ),
                "analysis": material.get("analysis") or {},
            }
        )
```

Do not transform any video bytes. The only supported video shape is a string URL or an empty string.

- [ ] **Step 4: Run focused render-data tests**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_embedded_template_data_includes_material_raw_facts_without_video_payload tests.test_final_report.FinalReportRenderTests.test_embedded_template_data_excludes_execution_process_metadata -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: expose material raw facts to final template"
```

---

### Task 3: Render Interaction Timeline And Script Fact Cards

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `templates/final-report-template.html`

- [ ] **Step 1: Write failing template structure assertions**

Extend `test_template_keeps_current_structure_with_livecheck_visual_style` with:

```python
self.assertIn("function renderInteractionTimeline", html_text)
self.assertIn("function renderTimelineChart", html_text)
self.assertIn("function hydrateMaterialPanel", html_text)
self.assertIn("function renderScriptAnalysis", html_text)
self.assertIn("互动时序分析", html_text)
self.assertIn("脚本分析数据", html_text)
self.assertIn("暂无互动时序数据", html_text)
self.assertIn("暂无脚本分析数据", html_text)
self.assertIn("preload = \"metadata\"", html_text)
self.assertNotIn("data:video/", html_text)
```

Extend `test_renders_v2_analysis_with_accessible_material_tabs` with:

```python
self.assertIn("互动时序分析", html_text)
self.assertIn("脚本分析数据", html_text)
self.assertIn('"interactionTimeline"', html_text)
self.assertIn('"scriptAnalysis"', html_text)
self.assertIn("人群与脚本证据", html_text)
self.assertIn("高光帧观察", html_text)
self.assertIn("综合判断与下一步", html_text)
self.assertNotIn("data:video/", html_text)
```

- [ ] **Step 2: Run the focused render tests and confirm they fail**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_template_keeps_current_structure_with_livecheck_visual_style tests.test_final_report.FinalReportRenderTests.test_renders_v2_analysis_with_accessible_material_tabs -v
```

Expected: FAIL because the new functions and visible headings do not exist yet.

- [ ] **Step 3: Add CSS for raw fact cards**

In `templates/final-report-template.html`, near the existing `.formula` and `.material-body` rules, add:

```css
    .raw-fact-card {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #fff;
      padding: 17px;
    }

    .raw-fact-card > b {
      display: block;
      margin-bottom: 10px;
      font-size: 16px;
    }

    .timeline-chart {
      width: 100%;
      height: 180px;
      display: block;
      border-radius: 14px;
      background: #fff8ec;
      border: 1px solid rgba(82, 45, 24, .1);
    }

    .timeline-peak {
      margin-top: 10px;
      color: var(--ink);
      font-weight: 800;
    }

    .chip-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 8px 0 12px;
    }

    .chip {
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(82, 45, 24, .14);
      border-radius: 999px;
      padding: 5px 10px;
      background: #f7ead8;
      color: var(--ink);
      font-size: 12px;
      font-weight: 800;
    }

    .evidence-list {
      display: grid;
      gap: 8px;
      margin: 8px 0 12px;
    }

    .evidence-row {
      padding: 10px 12px;
      border-radius: 12px;
      background: #fff8ec;
      color: var(--ink);
      line-height: 1.65;
    }

    .transcript {
      max-height: 120px;
      overflow: auto;
      padding: 12px;
      border-radius: 12px;
      background: #f7ead8;
      color: var(--ink);
      line-height: 1.7;
    }
```

- [ ] **Step 4: Add JavaScript render helpers**

Add these functions before `renderMaterialPanel`:

```javascript
function renderTimelineChart(metric) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "timeline-chart");
  svg.setAttribute("viewBox", "0 0 640 180");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${text(metric.label)}趋势图`);
  const points = Array.isArray(metric.points) ? metric.points : [];
  if (points.length === 0) {
    return svg;
  }
  const maxSecond = Math.max(...points.map((point) => Number(point.second || 0)), 1);
  const maxValue = Math.max(...points.map((point) => Number(point.value || 0)), 1);
  const pathData = points.map((point, index) => {
    const x = 24 + (Number(point.second || 0) / maxSecond) * 592;
    const y = 146 - (Number(point.value || 0) / maxValue) * 112;
    return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ");
  const gridLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
  gridLine.setAttribute("x1", "24");
  gridLine.setAttribute("x2", "616");
  gridLine.setAttribute("y1", "146");
  gridLine.setAttribute("y2", "146");
  gridLine.setAttribute("stroke", "rgba(82,45,24,.18)");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#d79a4b");
  path.setAttribute("stroke-width", "4");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(gridLine, path);
  const peak = metric.peak || {};
  if (peak.second !== undefined && peak.value !== undefined) {
    const cx = 24 + (Number(peak.second || 0) / maxSecond) * 592;
    const cy = 146 - (Number(peak.value || 0) / maxValue) * 112;
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", cx.toFixed(2));
    dot.setAttribute("cy", cy.toFixed(2));
    dot.setAttribute("r", "6");
    dot.setAttribute("fill", "#2c1810");
    svg.append(dot);
  }
  return svg;
}

function renderInteractionTimeline(material) {
  const card = element("div", "raw-fact-card");
  card.append(element("b", "", "互动时序分析"));
  const metrics = Array.isArray(material.interactionTimeline?.metrics)
    ? material.interactionTimeline.metrics
    : [];
  if (metrics.length === 0) {
    card.append(element("p", "muted", "暂无互动时序数据"));
    return card;
  }
  const switcher = element("div", "metric-switch");
  const chartMount = element("div", "");
  const peakNote = element("p", "timeline-peak");
  card.dataset.timelineReady = "false";
  card._timelineMetrics = metrics;
  card._timelineRenderMetric = renderMetric;
  card.append(switcher, chartMount, peakNote);

  function renderMetric(metric) {
    switcher.querySelectorAll("button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.metric === metric.metric));
    });
    chartMount.replaceChildren(renderTimelineChart(metric));
    const peak = metric.peak || {};
    peakNote.textContent = `${text(metric.label)}峰值：${formatNumber(peak.second)}s，${formatNumber(peak.value)} 次`;
  }

  metrics.forEach((metric, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.metric = text(metric.metric);
    button.setAttribute("aria-pressed", String(index === 0));
    button.textContent = text(metric.label || metric.metric);
    button.addEventListener("click", () => {
      renderMetric(metric);
      card.dataset.timelineReady = "true";
    });
    switcher.append(button);
  });
  return card;
}

function renderChipList(items) {
  const list = element("div", "chip-list");
  items.forEach((item) => {
    const label = typeof item === "string" ? item : `${text(item.level1)} / ${text(item.level2)}`;
    if (label.trim()) {
      list.append(element("span", "chip", label));
    }
  });
  return list;
}

function renderScriptAnalysis(material) {
  const card = element("div", "raw-fact-card");
  card.append(element("b", "", "脚本分析数据"));
  const script = material.scriptAnalysis || {};
  const labels = Array.isArray(script.labels) ? script.labels : [];
  const formula = Array.isArray(script.formula) ? script.formula : [];
  const evidence = Array.isArray(script.evidence) ? script.evidence : [];
  const scriptText = text(script.text);
  if (labels.length === 0 && evidence.length === 0 && !scriptText) {
    card.append(element("p", "muted", "暂无脚本分析数据"));
    return card;
  }
  if (formula.length > 0) {
    card.append(element("p", "muted", "本视频内容公式"), renderChipList(formula));
  }
  if (labels.length > 0) {
    card.append(element("p", "muted", "脚本标签"), renderChipList(labels));
  }
  if (evidence.length > 0) {
    const evidenceList = element("div", "evidence-list");
    evidence.slice(0, 6).forEach((item) => {
      evidenceList.append(
        element("div", "evidence-row", `${text(item.level2)}：${displayText(item.sentence)}`)
      );
    });
    card.append(element("p", "muted", "证据句"), evidenceList);
  }
  if (scriptText) {
    card.append(element("p", "muted", "脚本文案"), element("div", "transcript", scriptText));
  }
  return card;
}

function renderVideoPreview(material) {
  const videoData = material.video || {};
  const src = text(videoData.src);
  if (!src) {
    return null;
  }
  const video = document.createElement("video");
  video.controls = true;
  video.preload = "metadata";
  video.src = src;
  if (videoData.poster) {
    video.poster = text(videoData.poster);
  }
  return video;
}

function hydrateMaterialPanel(panel) {
  const timeline = panel.querySelector("[data-timeline-ready='false']");
  if (!timeline || !Array.isArray(timeline._timelineMetrics) || timeline._timelineMetrics.length === 0) {
    return;
  }
  timeline._timelineRenderMetric(timeline._timelineMetrics[0]);
  timeline.dataset.timelineReady = "true";
}
```

- [ ] **Step 5: Insert raw fact cards before AI summary cards**

Update `renderMaterialPanel` so the body is appended in this order and the selected panel is hydrated once:

```javascript
  const body = element("div", "material-body");
  body.append(
    renderInteractionTimeline(material),
    renderScriptAnalysis(material),
    renderFormula("人群与脚本证据", [analysis.audienceConclusion, analysis.scriptConclusion]),
    renderHighlightFormula(analysis.highlightAnalyses),
    renderFormula("综合判断与下一步", [analysis.integratedConclusion, `下一步：${text(analysis.nextAction)}`])
  );

  const videoPreview = renderVideoPreview(material);
  const visualNodes = [renderFrames(material)];
  if (videoPreview) {
    visualNodes.push(videoPreview);
  }
  panel.append(head, ...visualNodes, body);
  if (selected) {
    hydrateMaterialPanel(panel);
  }
  return panel;
```

Remove the old line:

```javascript
  panel.append(head, renderFrames(material), body);
```

Update `activateMaterialTab` so charts render when a user switches material:

```javascript
  container.querySelectorAll('[role="tabpanel"]').forEach((panel) => {
    const selected = panel.dataset.materialId === tab.dataset.materialId;
    panel.hidden = !selected;
    if (selected) {
      hydrateMaterialPanel(panel);
    }
  });
```

- [ ] **Step 6: Run focused template tests**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_template_keeps_current_structure_with_livecheck_visual_style tests.test_final_report.FinalReportRenderTests.test_renders_v2_analysis_with_accessible_material_tabs -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add templates/final-report-template.html tests/test_final_report.py
git commit -m "feat: render material timeline and script cards"
```

---

### Task 4: Clarify AI Prompt Boundary For Material Summaries

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`

- [ ] **Step 1: Write failing prompt-contract test**

Add this method to `FinalReportRenderTests` in `tests/test_final_report.py`:

```python
def test_prompt_explains_material_ai_summary_complements_raw_fact_cards(self):
    prompt_text = (ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md").read_text(
        encoding="utf-8"
    )

    self.assertIn("04｜单素材深度分析", prompt_text)
    self.assertIn("互动时序分析", prompt_text)
    self.assertIn("脚本分析数据", prompt_text)
    self.assertIn("AI 二次总结", prompt_text)
    self.assertIn("不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句", prompt_text)
    self.assertIn("解释这些事实为什么影响素材放量、改写或保留判断", prompt_text)
```

- [ ] **Step 2: Run the prompt test and confirm it fails**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_prompt_explains_material_ai_summary_complements_raw_fact_cards -v
```

Expected: FAIL because the prompt does not yet mention the new material raw fact cards.

- [ ] **Step 3: Update the prompt material-section wording**

In `prompts/diagnostic-analysis-prompt-v1.md`, under `## 页面结构映射`, replace the `04｜单素材深度分析` bullet with:

```markdown
- `04｜单素材深度分析`：页面会先展示确定性的 `互动时序分析` 和 `脚本分析数据`，再展示 `materialAnalyses` 中的 AI 二次总结。AI 二次总结不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句，而要解释这些事实为什么影响素材放量、改写或保留判断。
```

- [ ] **Step 4: Run the prompt-contract test**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_prompt_explains_material_ai_summary_complements_raw_fact_cards -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add prompts/diagnostic-analysis-prompt-v1.md tests/test_final_report.py
git commit -m "docs: clarify material summary prompt boundary"
```

---

### Task 5: Full Regression And Real Report Regeneration

**Files:**
- Generated: `runs/2026-07-28-top18-1770096415226951-fullflow/final-report/final-report-data.json`
- Generated: `runs/2026-07-28-top18-1770096415226951-fullflow/final-report/final-report.html`

- [ ] **Step 1: Run the full final report test suite**

Run:

```bash
python -m unittest tests.test_final_report -v
```

Expected: PASS with all final-report tests passing.

- [ ] **Step 2: Run skill contract tests**

Run:

```bash
python -m unittest tests.test_skill_contract -v
```

Expected: PASS.

- [ ] **Step 3: Run whitespace diff check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Regenerate the fullflow final report**

Run:

```bash
python scripts/render_final_report.py \
  --top-materials-json runs/2026-07-28-top18-1770096415226951-fullflow/peak-frames/top_materials_rpa_ready.json \
  --peak-output-dir runs/2026-07-28-top18-1770096415226951-fullflow/peak-frames \
  --diagnosis-records-dir runs/2026-07-28-top18-1770096415226951-fullflow/diagnosis-records \
  --analysis-json runs/2026-07-28-top18-1770096415226951-fullflow/diagnostic-analysis/diagnostic-ai-analysis.json \
  --output-dir runs/2026-07-28-top18-1770096415226951-fullflow/final-report
```

Expected:

- `final-report-data.json` is rewritten successfully.
- `final-report.html` is rewritten successfully.
- Command exits with code 0.

- [ ] **Step 5: Verify generated report contains the new material facts and no inline video**

Run:

```bash
python - <<'PY'
from pathlib import Path
html = Path("runs/2026-07-28-top18-1770096415226951-fullflow/final-report/final-report.html").read_text(encoding="utf-8")
required = [
    "互动时序分析",
    "脚本分析数据",
    '"interactionTimeline"',
    '"scriptAnalysis"',
    "整体点击次数",
    "整体流失数",
    "整体点赞次数",
    "整体评论次数",
    "整体转发次数",
    "整体新增粉丝数",
    "人群与脚本证据",
    "高光帧观察",
    "综合判断与下一步",
]
missing = [item for item in required if item not in html]
forbidden = ["data:video/", ";base64", "06｜下一轮策略", "07｜数据质量与执行口径"]
present_forbidden = [item for item in forbidden if item in html]
if missing or present_forbidden:
    raise SystemExit(f"missing={missing}; forbidden={present_forbidden}")
print("generated report contract ok")
PY
```

Expected:

```text
generated report contract ok
```

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add runs/2026-07-28-top18-1770096415226951-fullflow/final-report/final-report-data.json runs/2026-07-28-top18-1770096415226951-fullflow/final-report/final-report.html
git commit -m "chore: regenerate final report with material raw facts"
```

---

### Task 6: Final Verification

**Files:**
- Inspect: all changed files

- [ ] **Step 1: Run complete verification**

Run:

```bash
python -m unittest tests.test_final_report -v
python -m unittest tests.test_skill_contract -v
python -m py_compile scripts/*.py
git diff --check
git status --short
```

Expected:

- both unittest commands pass;
- `py_compile` exits 0;
- `git diff --check` exits 0;
- `git status --short` has no uncommitted implementation files, unless the worker intentionally leaves generated report artifacts unstaged for review.

- [ ] **Step 2: Summarize implementation outcome**

Report:

```text
Implemented material-level interaction timeline facts, script-analysis fact cards, prompt boundary wording, and regenerated the fullflow report. Verification commands completed with the statuses listed above.
```

If any command fails, report the exact command and failure instead of claiming completion.
