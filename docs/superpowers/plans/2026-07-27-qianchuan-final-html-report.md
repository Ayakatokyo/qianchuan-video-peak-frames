# Qianchuan Final HTML Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a final no-external-API HTML report loop that merges TOP-material selection, peak frames, diagnostic facts, and a structured local conclusion JSON.

**Architecture:** Add a new final report layer instead of expanding the legacy commercial report generator. `final_report_lib.py` owns schema validation, artifact loading, report data assembly, and HTML rendering; `render_final_report.py` is a thin CLI. Existing RPA, peak extraction, and diagnostic fact preparation stay unchanged.

**Tech Stack:** Python 3.9+, standard library (`argparse`, `json`, `html`, `pathlib`, `os`, `uuid`, `unittest`), existing project libraries.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `scripts/final_report_lib.py` | Validate conclusion JSON, load local run artifacts, merge final report data, render standalone HTML, write outputs atomically |
| `scripts/render_final_report.py` | CLI wrapper around `generate_final_report()` |
| `tests/test_final_report.py` | Unit and integration-style tests for schema validation, draft/partial statuses, failed material preservation, and HTML sections |
| `tests/test_skill_contract.py` | Static checks that `SKILL.md` documents the final report loop and no-external-API boundary |
| `SKILL.md` | User-facing workflow for preparing facts, adding conclusion JSON, and rendering the final report |

## Task 1: Add Final Report Tests

**Files:**
- Create: `tests/test_final_report.py`
- Test: `tests/test_final_report.py`

- [ ] **Step 1: Write test helpers and conclusion schema tests**

Create `tests/test_final_report.py` with this initial content:

```python
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from final_report_lib import FinalReportError, load_conclusion_analysis  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class FinalReportConclusionTests(unittest.TestCase):
    def test_accepts_valid_conclusion_schema(self):
        payload = {
            "schemaVersion": 1,
            "overallConclusion": "礼赠和滋补卖点都值得保留。",
            "keyInsights": [
                {"title": "礼盒开箱强", "body": "高光帧集中在包装动作。", "materialIds": ["m-1"]}
            ],
            "materialRecommendations": [
                {
                    "materialId": "m-1",
                    "decision": "scale",
                    "reason": "消耗和点击峰值都集中。",
                    "nextAction": "保留前 5 秒开箱动作。",
                }
            ],
            "nextActions": [
                {"title": "新拍礼赠版", "body": "强化送礼对象。", "priority": "high"}
            ],
            "warnings": ["结论来自外部分析。"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostic-ai-analysis.json"
            write_json(path, payload)

            result = load_conclusion_analysis(path)

        self.assertEqual(result["overallConclusion"], "礼赠和滋补卖点都值得保留。")
        self.assertEqual(result["materialRecommendations"][0]["decision"], "scale")

    def test_rejects_invalid_conclusion_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostic-ai-analysis.json"
            write_json(path, {"schemaVersion": 2, "overallConclusion": "bad"})

            with self.assertRaisesRegex(FinalReportError, "unsupported conclusion schemaVersion"):
                load_conclusion_analysis(path)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportConclusionTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'final_report_lib'`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_final_report.py
git commit -m "test: define final report conclusion contract"
```

## Task 2: Implement Conclusion Schema Validation

**Files:**
- Create: `scripts/final_report_lib.py`
- Test: `tests/test_final_report.py`

- [ ] **Step 1: Add minimal schema implementation**

Create `scripts/final_report_lib.py` with:

```python
import json
from pathlib import Path


class FinalReportError(RuntimeError):
    """Raised when final report inputs cannot produce a valid report."""


ALLOWED_DECISIONS = {"scale", "remix", "hold", "stop", "unknown"}
ALLOWED_PRIORITIES = {"high", "medium", "low"}


def _text(value):
    return str(value).strip() if value is not None else ""


def _read_json(path, purpose):
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FinalReportError(f"unable to read {purpose}: {source}") from exc


def _string_list(value, field_name):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise FinalReportError(f"{field_name} must be a list of strings")
    return [_text(item) for item in value if _text(item)]


def _validate_insight(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"keyInsights[{index}] must be an object")
    title = _text(raw.get("title"))
    body = _text(raw.get("body"))
    if not title or not body:
        raise FinalReportError(f"keyInsights[{index}] requires title and body")
    return {
        "title": title,
        "body": body,
        "materialIds": _string_list(raw.get("materialIds", []), f"keyInsights[{index}].materialIds"),
    }


def _validate_recommendation(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"materialRecommendations[{index}] must be an object")
    material_id = _text(raw.get("materialId"))
    decision = _text(raw.get("decision")) or "unknown"
    reason = _text(raw.get("reason"))
    next_action = _text(raw.get("nextAction"))
    if not material_id:
        raise FinalReportError(f"materialRecommendations[{index}] requires materialId")
    if decision not in ALLOWED_DECISIONS:
        raise FinalReportError(f"materialRecommendations[{index}].decision is unsupported")
    return {
        "materialId": material_id,
        "decision": decision,
        "reason": reason,
        "nextAction": next_action,
    }


def _validate_next_action(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"nextActions[{index}] must be an object")
    title = _text(raw.get("title"))
    body = _text(raw.get("body"))
    priority = _text(raw.get("priority")) or "medium"
    if not title or not body:
        raise FinalReportError(f"nextActions[{index}] requires title and body")
    if priority not in ALLOWED_PRIORITIES:
        raise FinalReportError(f"nextActions[{index}].priority is unsupported")
    return {"title": title, "body": body, "priority": priority}


def normalize_conclusion_analysis(payload):
    if not isinstance(payload, dict):
        raise FinalReportError("conclusion analysis must be an object")
    if payload.get("schemaVersion") != 1:
        raise FinalReportError("unsupported conclusion schemaVersion")
    insights = payload.get("keyInsights", [])
    recommendations = payload.get("materialRecommendations", [])
    next_actions = payload.get("nextActions", [])
    if not isinstance(insights, list):
        raise FinalReportError("keyInsights must be a list")
    if not isinstance(recommendations, list):
        raise FinalReportError("materialRecommendations must be a list")
    if not isinstance(next_actions, list):
        raise FinalReportError("nextActions must be a list")
    return {
        "schemaVersion": 1,
        "overallConclusion": _text(payload.get("overallConclusion")),
        "keyInsights": [_validate_insight(item, index) for index, item in enumerate(insights)],
        "materialRecommendations": [
            _validate_recommendation(item, index)
            for index, item in enumerate(recommendations)
        ],
        "nextActions": [_validate_next_action(item, index) for index, item in enumerate(next_actions)],
        "warnings": _string_list(payload.get("warnings", []), "warnings"),
    }


def load_conclusion_analysis(path):
    return normalize_conclusion_analysis(_read_json(path, "conclusion analysis JSON"))
```

- [ ] **Step 2: Run focused tests to verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportConclusionTests -v
```

Expected: PASS.

- [ ] **Step 3: Commit schema implementation**

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: validate final report conclusion json"
```

## Task 3: Build Final Report Data

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: Add tests for draft, partial, and failed material preservation**

Append to `tests/test_final_report.py`:

```python
from final_report_lib import build_final_report_data  # noqa: E402


def material(material_id, rank=1, cost=100.0):
    return {
        "rank": rank,
        "materialId": material_id,
        "materialName": f"素材 {material_id}",
        "materialType": "短视频",
        "videoType": "素材视频",
        "totalCost": cost,
    }


def click_record(material_id, second, value):
    return {
        "recordType": "interaction_series",
        "interactionMetric": "CLICK_COUNT",
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


class FinalReportDataTests(unittest.TestCase):
    def test_builds_draft_report_without_conclusion_json(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "rankingMetric": "stat_cost_for_overall_roi2",
            "materials": [material("m-1")],
        }
        records_by_material = {
            "m-1": [
                click_record("m-1", 0, 0),
                click_record("m-1", 1, 3),
                click_record("m-1", 2, 0),
            ]
        }
        summaries_by_material = {
            "m-1": {
                "summaryPath": "/run/peak-frames/m-1/summary.json",
                "frames": [
                    {
                        "rankType": "first_peak",
                        "durationSec": 1,
                        "metricValue": 3,
                        "framePath": "/run/peak-frames/m-1/first_peak_0001s_3.jpg",
                    }
                ],
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

        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["overview"]["materialCount"], 1)
        self.assertEqual(result["overview"]["highlightFrameCount"], 1)
        self.assertEqual(result["materials"][0]["extractionStatus"], "succeeded")

    def test_partial_report_keeps_failed_material(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "materials": [material("m-1", 1, 100), material("m-2", 2, 80)],
        }
        records_by_material = {"m-1": [], "m-2": []}
        summaries_by_material = {
            "m-1": {
                "summaryPath": "/run/peak-frames/m-1/summary.json",
                "frames": [
                    {
                        "rankType": "first_peak",
                        "durationSec": 1,
                        "metricValue": 3,
                        "framePath": "/run/peak-frames/m-1/first_peak_0001s_3.jpg",
                    }
                ],
                "warnings": [],
            }
        }
        batch_verification = {
            "valid": False,
            "errors": ["material m-2 is not succeeded"],
            "warnings": [],
        }

        result = build_final_report_data(
            top_payload,
            records_by_material,
            summaries_by_material,
            conclusion={"overallConclusion": "m-1 可复用", "keyInsights": [], "materialRecommendations": [], "nextActions": [], "warnings": []},
            batch_verification=batch_verification,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual([item["materialId"] for item in result["materials"]], ["m-1", "m-2"])
        self.assertEqual(result["materials"][1]["extractionStatus"], "missing_summary")
        self.assertIn("material m-2 is not succeeded", result["warnings"])
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportDataTests -v
```

Expected: FAIL with `ImportError` or `AttributeError` because `build_final_report_data` is missing.

- [ ] **Step 3: Implement data assembly**

Extend `scripts/final_report_lib.py` with imports and functions:

```python
from datetime import datetime, timezone
import math

from diagnostic_analysis_lib import build_diagnostic_analysis


def _number(value, default=0.0):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _selected_materials(top_payload):
    if not isinstance(top_payload, dict):
        raise FinalReportError("top materials JSON must be an object")
    materials = top_payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise FinalReportError("top materials JSON must contain a nonempty materials list")
    selected = []
    seen = set()
    for index, raw in enumerate(materials, start=1):
        if not isinstance(raw, dict):
            continue
        material_id = _text(raw.get("materialId"))
        if not material_id or material_id in seen:
            continue
        seen.add(material_id)
        selected.append(
            {
                "rank": raw.get("rank", index),
                "materialId": material_id,
                "materialName": _text(raw.get("materialName")),
                "materialType": _text(raw.get("materialType")),
                "videoType": _text(raw.get("videoType")),
                "totalCost": round(_number(raw.get("totalCost")), 6),
            }
        )
    if not selected:
        raise FinalReportError("top materials JSON contains no usable materials")
    return selected


def _recommendations_by_material(conclusion):
    result = {}
    if not conclusion:
        return result
    for item in conclusion.get("materialRecommendations", []):
        result[item["materialId"]] = item
    return result


def _material_card(material, summary, recommendation):
    frames = summary.get("frames", []) if isinstance(summary, dict) else []
    warnings = summary.get("warnings", []) if isinstance(summary, dict) else []
    extraction_status = "succeeded" if frames else "missing_summary"
    return {
        **material,
        "extractionStatus": extraction_status,
        "summaryPath": summary.get("summaryPath", "") if isinstance(summary, dict) else "",
        "highlightFrameCount": len(frames),
        "highlightFrames": frames,
        "warnings": warnings,
        "recommendation": recommendation
        or {
            "materialId": material["materialId"],
            "decision": "unknown",
            "reason": "",
            "nextAction": "",
        },
    }


def _status(conclusion, material_cards, batch_verification):
    if conclusion is None:
        return "draft"
    verification_errors = []
    if isinstance(batch_verification, dict):
        verification_errors = batch_verification.get("errors", [])
        if not isinstance(verification_errors, list):
            verification_errors = []
    if verification_errors:
        return "partial"
    if any(material["extractionStatus"] != "succeeded" for material in material_cards):
        return "partial"
    return "complete"


def _warnings(material_cards, conclusion, batch_verification):
    warnings = []
    for material in material_cards:
        if material["extractionStatus"] != "succeeded":
            warnings.append(f"missing high-light summary for material {material['materialId']}")
        warnings.extend(str(item) for item in material.get("warnings", []))
    if conclusion:
        warnings.extend(conclusion.get("warnings", []))
    if isinstance(batch_verification, dict):
        for item in batch_verification.get("warnings", []):
            warnings.append(json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item))
        warnings.extend(str(item) for item in batch_verification.get("errors", []))
    return warnings


def build_final_report_data(
    top_payload,
    records_by_material,
    summaries_by_material,
    conclusion=None,
    batch_verification=None,
):
    materials = _selected_materials(top_payload)
    selected_ids = [material["materialId"] for material in materials]
    diagnostic = build_diagnostic_analysis(top_payload, records_by_material)
    recommendations = _recommendations_by_material(conclusion)
    material_cards = [
        _material_card(
            material,
            summaries_by_material.get(material["materialId"], {}),
            recommendations.get(material["materialId"]),
        )
        for material in materials
    ]
    highlight_frame_count = sum(item["highlightFrameCount"] for item in material_cards)
    successful_material_count = sum(
        1 for item in material_cards if item["extractionStatus"] == "succeeded"
    )
    report_warnings = _warnings(material_cards, conclusion, batch_verification)
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": _status(conclusion, material_cards, batch_verification),
        "period": {
            "actualStartDate": _text(top_payload.get("actualStartDate")),
            "actualEndDate": _text(top_payload.get("actualEndDate")),
        },
        "overview": {
            "materialCount": len(material_cards),
            "successfulMaterialCount": successful_material_count,
            "highlightFrameCount": highlight_frame_count,
            "totalCost": round(sum(item["totalCost"] for item in material_cards), 6),
            "warningCount": len(report_warnings),
            "rankingMetric": _text(top_payload.get("rankingMetric")) or "totalCost",
        },
        "materials": material_cards,
        "audience": diagnostic["audience"],
        "scripts": diagnostic["scripts"],
        "delivery": diagnostic["delivery"],
        "conclusion": conclusion
        or {
            "overallConclusion": "",
            "keyInsights": [],
            "materialRecommendations": [],
            "nextActions": [],
            "warnings": [],
        },
        "batchVerification": batch_verification or {},
        "warnings": report_warnings + diagnostic.get("warnings", []),
    }
```

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report -v
```

Expected: PASS.

- [ ] **Step 5: Commit data assembly**

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: build final report data"
```

## Task 4: Load Artifacts And Render HTML

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: Add end-to-end local rendering test**

Append to `tests/test_final_report.py`:

```python
from final_report_lib import generate_final_report  # noqa: E402


class FinalReportRenderTests(unittest.TestCase):
    def test_generates_html_with_required_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = root / "diagnostic-analysis" / "diagnostic-ai-analysis.json"
            output_dir = root / "final-report"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "rankingMetric": "stat_cost_for_overall_roi2",
                    "materials": [material("m-1", 1, 123.45)],
                },
            )
            write_json(records_dir / "m-1.json", [click_record("m-1", 0, 0)])
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_3.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "materialId": "m-1",
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 3,
                            "framePath": frame_path.name,
                        }
                    ],
                    "warnings": [],
                },
            )
            write_json(peak_dir / "batch-verification.json", {"valid": True, "errors": [], "warnings": []})
            write_json(
                analysis_path,
                {
                    "schemaVersion": 1,
                    "overallConclusion": "礼赠素材可以继续放量。",
                    "keyInsights": [{"title": "开箱强", "body": "高光帧集中。", "materialIds": ["m-1"]}],
                    "materialRecommendations": [
                        {"materialId": "m-1", "decision": "scale", "reason": "表现稳定", "nextAction": "保留开箱"}
                    ],
                    "nextActions": [{"title": "扩展礼赠脚本", "body": "围绕送礼对象拆分。", "priority": "high"}],
                    "warnings": [],
                },
            )

            data_path, html_path, data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
                analysis_json=analysis_path,
            )

            html_text = html_path.read_text(encoding="utf-8")
        self.assertEqual(data_path.name, "final-report-data.json")
        self.assertEqual(html_path.name, "final-report.html")
        self.assertEqual(data["status"], "complete")
        self.assertIn("千川 TOP 素材池商业诊断报告", html_text)
        self.assertIn("组合结论", html_text)
        self.assertIn("关键洞察", html_text)
        self.assertIn("素材证据", html_text)
        self.assertIn("下一轮策略", html_text)
        self.assertIn("数据质量", html_text)
        self.assertIn("礼赠素材可以继续放量。", html_text)
```

- [ ] **Step 2: Run rendering test to verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportRenderTests -v
```

Expected: FAIL because `generate_final_report` is missing.

- [ ] **Step 3: Implement artifact loading and HTML rendering**

Extend `scripts/final_report_lib.py` with:

```python
import html
import os
import uuid


def _safe_component(value):
    text = str(value)
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in text
    )
    return cleaned.strip("_") or "unknown-material"


def _format_number(value):
    number = _number(value, default=None)
    if number is None:
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    if number == int(number):
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _load_records_for_materials(top_payload, records_dir):
    result = {}
    for material in _selected_materials(top_payload):
        material_id = material["materialId"]
        path = Path(records_dir) / f"{material_id}.json"
        if not path.is_file():
            result[material_id] = []
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            result[material_id] = []
            continue
        result[material_id] = payload if isinstance(payload, list) else []
    return result


def _load_summaries_for_materials(top_payload, peak_output_dir):
    result = {}
    root = Path(peak_output_dir)
    for material in _selected_materials(top_payload):
        material_id = material["materialId"]
        material_dir = root / _safe_component(material_id)
        summary_path = material_dir / "summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        frames = []
        if isinstance(summary, dict):
            for frame in summary.get("frames", []):
                if not isinstance(frame, dict):
                    continue
                frame_name = _text(frame.get("framePath"))
                frames.append(
                    {
                        "rankType": _text(frame.get("rankType")),
                        "durationSec": frame.get("durationSec"),
                        "metricValue": frame.get("metricValue"),
                        "framePath": str(material_dir / frame_name) if frame_name else "",
                    }
                )
            result[material_id] = {
                "summaryPath": str(summary_path),
                "frames": frames,
                "warnings": summary.get("warnings", []) if isinstance(summary.get("warnings", []), list) else [],
            }
    return result


def _load_batch_verification(peak_output_dir):
    path = Path(peak_output_dir) / "batch-verification.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write_text(path, content):
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FinalReportError(f"unable to create output directory {target.parent}: {exc}") from exc
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        staging.write_text(content, encoding="utf-8")
        os.replace(staging, target)
    except OSError as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise FinalReportError(f"unable to write report output {target}: {exc}") from exc


def _render_frames(material):
    figures = []
    for frame in material.get("highlightFrames", []):
        source = html.escape(frame.get("framePath", ""))
        caption = (
            f"{html.escape(frame.get('rankType', ''))} · "
            f"{_format_number(frame.get('durationSec'))}s · "
            f"点击 {_format_number(frame.get('metricValue'))}"
        )
        figures.append(f"<figure><img src=\"{source}\" alt=\"高光帧\"/><figcaption>{caption}</figcaption></figure>")
    return "".join(figures) or "<p class=\"muted\">暂无高光帧</p>"


def _render_html_report(data):
    conclusion = data["conclusion"]
    insights = "".join(
        f"<li><b>{html.escape(item['title'])}</b><p>{html.escape(item['body'])}</p><small>{html.escape(', '.join(item.get('materialIds', [])))}</small></li>"
        for item in conclusion.get("keyInsights", [])
    ) or "<li><b>暂无关键洞察</b><p>请补充 diagnostic-ai-analysis.json 后重新渲染。</p></li>"
    priority_order = {"high": 0, "medium": 1, "low": 2}
    actions = sorted(conclusion.get("nextActions", []), key=lambda item: priority_order.get(item.get("priority"), 1))
    actions_html = "".join(
        f"<li><b>{html.escape(item['title'])}</b><p>{html.escape(item['body'])}</p><small>{html.escape(item.get('priority', 'medium'))}</small></li>"
        for item in actions
    ) or "<li><b>暂无下一轮策略</b><p>请补充结构化结论后重新渲染。</p></li>"
    material_html = []
    for material in data["materials"]:
        recommendation = material.get("recommendation", {})
        material_html.append(
            "<article class=\"material-card\">"
            "<div class=\"material-head\">"
            f"<div><p class=\"eyebrow\">#{html.escape(str(material.get('rank', '')))} · {html.escape(material['materialId'])}</p>"
            f"<h3>{html.escape(material.get('materialName', ''))}</h3>"
            f"<p class=\"muted\">消耗 ¥{_format_number(material.get('totalCost'))} · 状态 {html.escape(material.get('extractionStatus', ''))}</p></div>"
            f"<div class=\"score\"><span>建议</span><b>{html.escape(recommendation.get('decision', 'unknown'))}</b></div>"
            "</div>"
            f"<div class=\"frame-grid\">{_render_frames(material)}</div>"
            "<div class=\"material-body\">"
            f"<div class=\"formula\"><b>判断理由</b><p>{html.escape(recommendation.get('reason', '') or '暂无')}</p></div>"
            f"<div class=\"formula\"><b>下一步</b><p>{html.escape(recommendation.get('nextAction', '') or '暂无')}</p></div>"
            "</div>"
            "</article>"
        )
    warnings_html = "".join(f"<li>{html.escape(str(warning))}</li>" for warning in data.get("warnings", [])) or "<li>无阻断告警</li>"
    cover = ""
    for material in data["materials"]:
        frames = material.get("highlightFrames", [])
        if frames and frames[0].get("framePath"):
            cover = frames[0]["framePath"]
            break
    cover_html = (
        f"<img src=\"{html.escape(cover)}\" alt=\"代表高光帧\"/>"
        if cover
        else "<div class=\"cover-empty\">暂无高光帧封面</div>"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>千川 TOP 素材池商业诊断报告</title>
<style>
:root{{--bg:#120b08;--paper:#fff8ec;--panel:rgba(255,248,236,.94);--ink:#2a170f;--muted:#806758;--gold:#d79a4b;--red:#963827;--line:rgba(82,45,24,.15);--shadow:0 28px 80px rgba(43,23,12,.18)}}*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;background:linear-gradient(135deg,#160d09,#3b1f15 44%,#f5dfbd 44%,#fff8ec);color:var(--ink)}}main{{width:min(1260px,calc(100% - 42px));margin:0 auto;padding:42px 0 72px}}.hero{{display:grid;grid-template-columns:1fr .86fr;gap:28px;color:#fff;margin-bottom:26px}}.hero-card,.section{{border:1px solid var(--line);box-shadow:var(--shadow)}}.hero-card{{padding:36px;border-radius:28px;background:linear-gradient(145deg,rgba(255,255,255,.12),rgba(255,255,255,.04))}}h1{{font-size:clamp(38px,5vw,72px);line-height:.95;margin:14px 0 18px}}.subtitle{{font-size:18px;line-height:1.75;color:rgba(255,255,255,.8)}}.eyebrow{{letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--gold);font-weight:800}}.cover{{border-radius:28px;overflow:hidden;min-height:360px;background:#24120d;position:relative;box-shadow:var(--shadow)}}.cover img{{width:100%;height:100%;object-fit:cover;display:block}}.cover-empty{{display:grid;place-items:center;height:360px;color:#f7dec1}}.pill{{display:inline-flex;padding:9px 13px;border-radius:999px;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.16);margin:5px 7px 0 0;font-weight:700}}.section{{background:var(--panel);border-radius:28px;padding:28px;margin:22px 0}}h2{{font-size:31px;margin:0 0 18px}}h3{{margin:0 0 10px;font-size:24px}}p{{line-height:1.72}}.muted,small{{color:var(--muted)}}.metrics{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px}}.metric{{padding:20px;border:1px solid var(--line);border-radius:20px;background:#fffdf8}}.metric span{{display:block;color:var(--muted);font-size:13px}}.metric strong{{display:block;font-size:28px;margin:8px 0}}.material-card{{background:#fffdf8;border:1px solid var(--line);border-radius:24px;padding:22px;margin:18px 0;box-shadow:0 16px 40px rgba(65,34,16,.1)}}.material-head{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}}.score{{min-width:88px;text-align:center;border-radius:20px;background:#2c1810;color:#fff;padding:14px}}.score span{{display:block;color:#d9b98d;font-size:12px}}.score b{{font-size:20px}}.frame-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}figure{{margin:0;border-radius:18px;overflow:hidden;background:#f4e3cd;border:1px solid var(--line)}}figure img{{width:100%;height:190px;object-fit:cover;display:block}}figcaption{{padding:10px 12px;color:var(--muted);font-size:13px}}.material-body{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.formula{{padding:17px;border-radius:18px;background:#f7ead8;border:1px solid var(--line)}}.rec-list{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;list-style:none;padding:0}}.rec-list li{{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px}}@media(max-width:980px){{.hero,.metrics,.frame-grid,.material-body,.rec-list{{grid-template-columns:1fr}}.cover{{min-height:300px}}}}
</style></head><body><main>
<section class="hero"><div class="hero-card"><p class="eyebrow">QIANCHUAN MATERIAL PORTFOLIO REPORT</p><h1>千川 TOP 素材池商业诊断报告</h1><p class="subtitle">统计周期 {html.escape(data['period'].get('actualStartDate',''))} - {html.escape(data['period'].get('actualEndDate',''))}。报告状态：{html.escape(data['status'])}。</p><div><span class="pill">{data['overview']['materialCount']} 条素材</span><span class="pill">{data['overview']['highlightFrameCount']} 张高光帧</span><span class="pill">¥{_format_number(data['overview']['totalCost'])} 消耗</span></div></div><div class="cover">{cover_html}</div></section>
<section class="section"><h2>01｜组合总览</h2><div class="metrics"><div class="metric"><span>素材数</span><strong>{data['overview']['materialCount']}</strong></div><div class="metric"><span>成功抽帧素材</span><strong>{data['overview']['successfulMaterialCount']}</strong></div><div class="metric"><span>高光帧</span><strong>{data['overview']['highlightFrameCount']}</strong></div><div class="metric"><span>总消耗</span><strong>¥{_format_number(data['overview']['totalCost'])}</strong></div><div class="metric"><span>告警</span><strong>{data['overview']['warningCount']}</strong></div></div></section>
<section class="section"><h2>02｜组合结论</h2><p>{html.escape(conclusion.get('overallConclusion') or '结论待补充：请提供 diagnostic-ai-analysis.json 后重新渲染。')}</p></section>
<section class="section"><h2>03｜关键洞察</h2><ul class="rec-list">{insights}</ul></section>
<section class="section"><h2>04｜素材证据</h2>{''.join(material_html)}</section>
<section class="section"><h2>05｜下一轮策略</h2><ul class="rec-list">{actions_html}</ul></section>
<section class="section"><h2>06｜数据质量与方法说明</h2><p>人群与脚本数据是素材级相关性，不是单帧因果归因。高光帧用于辅助画面复盘，业务结论仅来自本地 diagnostic-ai-analysis.json。</p><ul>{warnings_html}</ul></section>
</main></body></html>"""


def generate_final_report(
    top_materials_json,
    peak_output_dir,
    diagnosis_records_dir,
    output_dir,
    analysis_json=None,
):
    top_payload = _read_json(top_materials_json, "top materials JSON")
    records_by_material = _load_records_for_materials(top_payload, diagnosis_records_dir)
    summaries_by_material = _load_summaries_for_materials(top_payload, peak_output_dir)
    batch_verification = _load_batch_verification(peak_output_dir)
    conclusion = load_conclusion_analysis(analysis_json) if analysis_json else None
    data = build_final_report_data(
        top_payload,
        records_by_material,
        summaries_by_material,
        conclusion=conclusion,
        batch_verification=batch_verification,
    )
    output_root = Path(output_dir)
    data_path = output_root / "final-report-data.json"
    html_path = output_root / "final-report.html"
    _atomic_write_text(data_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(html_path, _render_html_report(data))
    return data_path, html_path, data
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report -v
```

Expected: PASS.

- [ ] **Step 5: Commit rendering implementation**

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: render final html report"
```

## Task 5: Add CLI Entry Point

**Files:**
- Create: `scripts/render_final_report.py`
- Modify: `tests/test_final_report.py`

- [ ] **Step 1: Add CLI test**

Append to `tests/test_final_report.py`:

```python
import render_final_report  # noqa: E402


class FinalReportCliTests(unittest.TestCase):
    def test_cli_rejects_invalid_analysis_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            output_dir = root / "final-report"
            analysis_path = root / "bad.json"
            write_json(top_path, {"actualStartDate": "2026-06-01", "actualEndDate": "2026-06-30", "materials": [material("m-1")]})
            records_dir.mkdir()
            peak_dir.mkdir()
            write_json(analysis_path, {"schemaVersion": 2})

            result = render_final_report.main(
                [
                    "--top-materials-json",
                    str(top_path),
                    "--peak-output-dir",
                    str(peak_dir),
                    "--diagnosis-records-dir",
                    str(records_dir),
                    "--analysis-json",
                    str(analysis_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )

        self.assertEqual(result, 2)
```

- [ ] **Step 2: Run CLI test to verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportCliTests -v
```

Expected: FAIL because `render_final_report` is missing.

- [ ] **Step 3: Implement CLI**

Create `scripts/render_final_report.py`:

```python
#!/usr/bin/env python3
import argparse
import sys

from final_report_lib import FinalReportError, generate_final_report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Render final Qianchuan material HTML report."
    )
    parser.add_argument("--top-materials-json", required=True)
    parser.add_argument("--peak-output-dir", required=True)
    parser.add_argument("--diagnosis-records-dir", required=True)
    parser.add_argument("--analysis-json")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data_path, html_path, _data = generate_final_report(
            args.top_materials_json,
            args.peak_output_dir,
            args.diagnosis_records_dir,
            args.output_dir,
            analysis_json=args.analysis_json,
        )
    except FinalReportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"final_report_data={data_path}")
    print(f"final_report_html={html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI and final report tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_final_report -v
.venv/bin/python -m py_compile scripts/final_report_lib.py scripts/render_final_report.py
```

Expected: both commands return zero.

- [ ] **Step 5: Commit CLI**

```bash
git add scripts/render_final_report.py scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: add final report cli"
```

## Task 6: Update Skill Documentation Contract

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `SKILL.md`

- [ ] **Step 1: Add failing skill contract test**

Append to `tests/test_skill_contract.py` inside `BatchSkillContractTests`:

```python
    def test_final_html_report_loop_is_documented(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("## Final HTML Report Loop", skill)
        self.assertIn("prepare_diagnostic_analysis.py", skill)
        self.assertIn("diagnostic-ai-analysis.json", skill)
        self.assertIn("render_final_report.py", skill)
        self.assertIn("does not call external APIs", skill)
```

- [ ] **Step 2: Run skill contract test to verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_skill_contract.BatchSkillContractTests.test_final_html_report_loop_is_documented -v
```

Expected: FAIL because the final report loop section is missing from `SKILL.md`.

- [ ] **Step 3: Update `SKILL.md`**

Add this section after batch verification and before single-material debug:

```markdown
## Final HTML Report Loop

After RPA extraction and `--verify`, prepare the local diagnostic fact package:

```bash
python scripts/prepare_diagnostic_analysis.py \
  --top-materials-json "/path/to/run/top_materials.json" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/diagnostic-analysis"
```

Ask the external analysis environment to produce `/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json` using the retained fact package and prompt. The final report renderer does not call external APIs and does not generate business conclusions. It only injects local structured conclusions from `diagnostic-ai-analysis.json`.

Render the final HTML report:

```bash
python scripts/render_final_report.py \
  --top-materials-json "/path/to/run/top_materials.json" \
  --peak-output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --analysis-json "/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json" \
  --output-dir "/path/to/run/final-report"
```

Omit `--analysis-json` only when intentionally producing a draft report. Review `final-report/final-report-data.json`, `final-report/final-report.html`, batch verification errors, and warnings before treating the report as complete.
```

Also add quick-reference rows:

```markdown
| Prepare diagnostic facts | Run `prepare_diagnostic_analysis.py` after RPA verification |
| Final HTML report | Run `render_final_report.py`; omit `--analysis-json` only for a draft |
```

And add common mistakes:

```markdown
- Do not expect `render_final_report.py` to call a model or external API; provide `diagnostic-ai-analysis.json` for final conclusions.
- Do not hide failed materials from the final report; preserve them with warnings and failure reasons.
```

- [ ] **Step 4: Run skill contract test to verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_skill_contract -v
```

Expected: PASS.

- [ ] **Step 5: Commit skill documentation**

```bash
git add SKILL.md tests/test_skill_contract.py
git commit -m "docs: document final html report loop"
```

## Task 7: Full Regression

**Files:**
- Verify all modified files

- [ ] **Step 1: Run project test suite**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run Python compile check**

Run:

```bash
.venv/bin/python -m py_compile \
  scripts/final_report_lib.py \
  scripts/render_final_report.py \
  scripts/diagnostic_analysis_lib.py \
  scripts/extract_peak_frames.py
```

Expected: command returns zero.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only intended files remain changed if not already committed. `.superpowers/` must not be staged or committed.

## Self-Review

Spec coverage:

- Final local report renderer: Tasks 2-5.
- No external API boundary: Task 6.
- Structured conclusion JSON: Tasks 1-2.
- Draft and partial report states: Task 3.
- Failed material preservation: Task 3.
- HTML structure based on simulation report: Task 4.
- Skill workflow update: Task 6.
- Verification: Task 7.

Placeholder scan: no unresolved placeholder markers or undefined later work remains. The only draft behavior is an intentional report status.

Type consistency: the plan consistently uses `FinalReportError`, `load_conclusion_analysis()`, `build_final_report_data()`, and `generate_final_report()` across tests, implementation, and CLI.
