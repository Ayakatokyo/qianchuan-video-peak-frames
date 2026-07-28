import html
import json
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import uuid

from diagnostic_analysis_lib import build_diagnostic_analysis


class FinalReportError(RuntimeError):
    """Raised when final report inputs cannot produce a valid report."""


ALLOWED_DECISIONS = {"scale", "remix", "hold", "stop", "unknown"}
ALLOWED_PRIORITIES = {"high", "medium", "low"}
ALLOWED_INSIGHT_TOPICS = {"audience", "script", "highlight", "integrated"}


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
        "materialIds": _string_list(
            raw.get("materialIds", []), f"keyInsights[{index}].materialIds"
        ),
    }


def _validate_recommendation(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"materialRecommendations[{index}] must be an object")
    material_id = _text(raw.get("materialId"))
    decision = _text(raw.get("decision")) or "unknown"
    reason = _text(raw.get("reason"))
    next_action = _text(raw.get("nextAction"))
    if not material_id:
        raise FinalReportError(
            f"materialRecommendations[{index}] requires materialId"
        )
    if decision not in ALLOWED_DECISIONS:
        raise FinalReportError(
            f"materialRecommendations[{index}].decision is unsupported"
        )
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


def _normalize_v1_conclusion(payload):
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
        "keyInsights": [
            _validate_insight(item, index) for index, item in enumerate(insights)
        ],
        "materialRecommendations": [
            _validate_recommendation(item, index)
            for index, item in enumerate(recommendations)
        ],
        "nextActions": [
            _validate_next_action(item, index)
            for index, item in enumerate(next_actions)
        ],
        "warnings": _string_list(payload.get("warnings", []), "warnings"),
    }


def _validate_v2_insight(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"batchInsights[{index}] must be an object")
    topic = _text(raw.get("topic"))
    title = _text(raw.get("title"))
    conclusion = _text(raw.get("conclusion"))
    if topic not in ALLOWED_INSIGHT_TOPICS:
        raise FinalReportError(f"batchInsights[{index}].topic is unsupported")
    if not title or not conclusion:
        raise FinalReportError(
            f"batchInsights[{index}] requires title and conclusion"
        )
    return {
        "topic": topic,
        "title": title,
        "conclusion": conclusion,
        "evidenceRefs": _string_list(
            raw.get("evidenceRefs", []), f"batchInsights[{index}].evidenceRefs"
        ),
    }


def _validate_v2_highlight_analysis(raw, material_index, index):
    if not isinstance(raw, dict):
        raise FinalReportError(
            f"materialAnalyses[{material_index}].highlightAnalyses[{index}] "
            "must be an object"
        )
    frame_ref = _text(raw.get("frameRef"))
    visual_observation = _text(raw.get("visualObservation"))
    commercial_interpretation = _text(raw.get("commercialInterpretation"))
    if not frame_ref or not visual_observation or not commercial_interpretation:
        raise FinalReportError(
            f"materialAnalyses[{material_index}].highlightAnalyses[{index}] "
            "requires frameRef, visualObservation, and commercialInterpretation"
        )
    return {
        "frameRef": frame_ref,
        "visualObservation": visual_observation,
        "commercialInterpretation": commercial_interpretation,
    }


def _validate_v2_material_analysis(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"materialAnalyses[{index}] must be an object")
    material_id = _text(raw.get("materialId"))
    audience_conclusion = _text(raw.get("audienceConclusion"))
    script_conclusion = _text(raw.get("scriptConclusion"))
    integrated_conclusion = _text(raw.get("integratedConclusion"))
    decision = _text(raw.get("decision")) or "unknown"
    next_action = _text(raw.get("nextAction"))
    highlights = raw.get("highlightAnalyses", [])
    if not material_id:
        raise FinalReportError(f"materialAnalyses[{index}] requires materialId")
    if not audience_conclusion or not script_conclusion or not integrated_conclusion:
        raise FinalReportError(
            f"materialAnalyses[{index}] requires audienceConclusion, "
            "scriptConclusion, and integratedConclusion"
        )
    if decision not in ALLOWED_DECISIONS:
        raise FinalReportError(f"materialAnalyses[{index}].decision is unsupported")
    if not next_action:
        raise FinalReportError(f"materialAnalyses[{index}] requires nextAction")
    if not isinstance(highlights, list):
        raise FinalReportError(f"materialAnalyses[{index}].highlightAnalyses must be a list")
    return {
        "materialId": material_id,
        "audienceConclusion": audience_conclusion,
        "scriptConclusion": script_conclusion,
        "highlightAnalyses": [
            _validate_v2_highlight_analysis(item, index, item_index)
            for item_index, item in enumerate(highlights)
        ],
        "integratedConclusion": integrated_conclusion,
        "decision": decision,
        "nextAction": next_action,
        "evidenceRefs": _string_list(
            raw.get("evidenceRefs", []), f"materialAnalyses[{index}].evidenceRefs"
        ),
    }


def _normalize_v2_conclusion(payload):
    for field_name in ("batchInsights", "materialAnalyses", "nextActions"):
        if field_name not in payload:
            raise FinalReportError(f"{field_name} is required for schemaVersion 2")
    insights = payload.get("batchInsights", [])
    material_analyses = payload.get("materialAnalyses", [])
    next_actions = payload.get("nextActions", [])
    if not isinstance(insights, list):
        raise FinalReportError("batchInsights must be a list")
    if not isinstance(material_analyses, list):
        raise FinalReportError("materialAnalyses must be a list")
    if not isinstance(next_actions, list):
        raise FinalReportError("nextActions must be a list")
    normalized_materials = [
        _validate_v2_material_analysis(item, index)
        for index, item in enumerate(material_analyses)
    ]
    material_ids = [item["materialId"] for item in normalized_materials]
    if len(set(material_ids)) != len(material_ids):
        raise FinalReportError("materialAnalyses must not repeat materialId")
    return {
        "schemaVersion": 2,
        "overallConclusion": _text(payload.get("overallConclusion")),
        "batchInsights": [
            _validate_v2_insight(item, index) for index, item in enumerate(insights)
        ],
        "materialAnalyses": normalized_materials,
        "nextActions": [
            _validate_next_action(item, index) for index, item in enumerate(next_actions)
        ],
        "warnings": _string_list(payload.get("warnings", []), "warnings"),
    }


def normalize_conclusion_analysis(payload):
    if not isinstance(payload, dict):
        raise FinalReportError("conclusion analysis must be an object")
    schema_version = payload.get("schemaVersion")
    if schema_version == 1:
        return _normalize_v1_conclusion(payload)
    if schema_version == 2:
        return _normalize_v2_conclusion(payload)
    raise FinalReportError("unsupported conclusion schemaVersion")


def load_conclusion_analysis(path):
    return normalize_conclusion_analysis(
        _read_json(path, "conclusion analysis JSON")
    )


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
        raise FinalReportError(
            "top materials JSON must contain a nonempty materials list"
        )
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


def _empty_material_analysis(material_id):
    return {
        "materialId": material_id,
        "audienceConclusion": "数据不足以判断。",
        "scriptConclusion": "数据不足以判断。",
        "highlightAnalyses": [],
        "integratedConclusion": "暂无综合判断。",
        "decision": "unknown",
        "nextAction": "补充结构化分析后复盘。",
        "evidenceRefs": [],
    }


def _analyses_by_material(conclusion):
    if not conclusion:
        return {}
    if conclusion.get("schemaVersion") == 2:
        return {
            item["materialId"]: item
            for item in conclusion.get("materialAnalyses", [])
        }
    return {
        item["materialId"]: {
            **_empty_material_analysis(item["materialId"]),
            "integratedConclusion": item.get("reason", "") or "暂无综合判断。",
            "decision": item.get("decision", "unknown"),
            "nextAction": item.get("nextAction", "") or "暂无下一步。",
        }
        for item in conclusion.get("materialRecommendations", [])
    }


def _batch_insights(conclusion):
    result = {topic: [] for topic in ALLOWED_INSIGHT_TOPICS}
    if not conclusion:
        return result
    if conclusion.get("schemaVersion") == 2:
        for insight in conclusion.get("batchInsights", []):
            result[insight["topic"]].append(insight)
        return result
    for insight in conclusion.get("keyInsights", []):
        result["integrated"].append(
            {
                "topic": "integrated",
                "title": insight["title"],
                "conclusion": insight["body"],
                "evidenceRefs": insight.get("materialIds", []),
            }
        )
    return result


def _material_card(material, summary, analysis):
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
            warnings.append(
                f"missing high-light summary for material {material['materialId']}"
            )
        warnings.extend(str(item) for item in material.get("warnings", []))
    if conclusion:
        warnings.extend(conclusion.get("warnings", []))
    if isinstance(batch_verification, dict):
        for item in batch_verification.get("warnings", []):
            warnings.append(
                json.dumps(item, ensure_ascii=False)
                if isinstance(item, dict)
                else str(item)
            )
        warnings.extend(str(item) for item in batch_verification.get("errors", []))
    return warnings


def _known_evidence_refs(diagnostic, summaries_by_material):
    refs = set()
    for metric, metric_data in diagnostic["audience"]["metrics"].items():
        for dimension_key, dimension in metric_data["dimensions"].items():
            for bucket in dimension["buckets"]:
                refs.add(
                    f"audience:{metric}:{dimension_key}:{bucket['bucket']}"
                )
    for material in diagnostic["scripts"]["materials"]:
        for label in material["labels"]:
            refs.add(
                f"script:{material['materialId']}:{label['level1']}:"
                f"{label['level2']}"
            )
    for material_id, summary in summaries_by_material.items():
        if not isinstance(summary, dict):
            continue
        for frame in summary.get("frames", []):
            if isinstance(frame, dict):
                rank_type = _text(frame.get("rankType"))
                if rank_type:
                    refs.add(f"highlight:{material_id}:{rank_type}")
    return refs


def _validate_v2_evidence(conclusion, diagnostic, summaries_by_material, material_cards):
    if not conclusion or conclusion.get("schemaVersion") != 2:
        return
    known_refs = _known_evidence_refs(diagnostic, summaries_by_material)
    selected_ids = {material["materialId"] for material in material_cards}
    successful_ids = {
        material["materialId"]
        for material in material_cards
        if material["extractionStatus"] == "succeeded"
    }
    analysis_ids = set()

    def require_known(reference):
        if reference not in known_refs:
            raise FinalReportError(f"unknown evidence reference: {reference}")

    for insight in conclusion["batchInsights"]:
        for reference in insight["evidenceRefs"]:
            require_known(reference)
    for material_analysis in conclusion["materialAnalyses"]:
        material_id = material_analysis["materialId"]
        if material_id not in selected_ids:
            raise FinalReportError(f"unknown material analysis: {material_id}")
        analysis_ids.add(material_id)
        for reference in material_analysis["evidenceRefs"]:
            require_known(reference)
        for highlight in material_analysis["highlightAnalyses"]:
            require_known(highlight["frameRef"])
    missing_ids = sorted(successful_ids - analysis_ids)
    if missing_ids:
        raise FinalReportError(
            "missing material analysis for succeeded material: "
            + ", ".join(missing_ids)
        )


def build_final_report_data(
    top_payload,
    records_by_material,
    summaries_by_material,
    conclusion=None,
    batch_verification=None,
):
    materials = _selected_materials(top_payload)
    diagnostic = build_diagnostic_analysis(top_payload, records_by_material)
    analyses = _analyses_by_material(conclusion)
    material_cards = [
        _material_card(
            material,
            summaries_by_material.get(material["materialId"], {}),
            analyses.get(material["materialId"]),
        )
        for material in materials
    ]
    _validate_v2_evidence(
        conclusion, diagnostic, summaries_by_material, material_cards
    )
    highlight_frame_count = sum(
        item["highlightFrameCount"] for item in material_cards
    )
    successful_material_count = sum(
        1 for item in material_cards if item["extractionStatus"] == "succeeded"
    )
    report_warnings = _warnings(material_cards, conclusion, batch_verification)
    insights = _batch_insights(conclusion)
    default_material_id = next(
        (
            item["materialId"]
            for item in material_cards
            if item["extractionStatus"] == "succeeded"
        ),
        material_cards[0]["materialId"],
    )
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
        "defaultMaterialId": default_material_id,
        "batchInsights": insights,
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
        return "-"
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    if number == int(number):
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _load_records_for_materials(top_payload, records_dir):
    result = {}
    root = Path(records_dir)
    for material in _selected_materials(top_payload):
        material_id = material["materialId"]
        path = root / f"{material_id}.json"
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
        if not isinstance(summary, dict):
            continue
        frames = []
        for frame in summary.get("frames", []):
            if not isinstance(frame, dict):
                continue
            frame_name = _text(frame.get("framePath"))
            frames.append(
                {
                    "rankType": _text(frame.get("rankType")),
                    "durationSec": frame.get("durationSec"),
                    "metricValue": frame.get("metricValue"),
                    "framePath": str(material_dir / frame_name)
                    if frame_name
                    else "",
                }
            )
        summary_warnings = summary.get("warnings", [])
        result[material_id] = {
            "summaryPath": str(summary_path),
            "frames": frames,
            "warnings": summary_warnings
            if isinstance(summary_warnings, list)
            else [],
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
        raise FinalReportError(
            f"unable to create output directory {target.parent}: {exc}"
        ) from exc
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


def _html_asset_src(path, asset_base_dir):
    source = _text(path)
    if not source or asset_base_dir is None:
        return source
    try:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        source_path = source_path.resolve()
        relative = os.path.relpath(source_path, Path(asset_base_dir).resolve())
    except (OSError, ValueError):
        return source
    return Path(relative).as_posix()


def _render_frames(material, asset_base_dir=None):
    figures = []
    for frame in material.get("highlightFrames", []):
        source = html.escape(
            _html_asset_src(frame.get("framePath", ""), asset_base_dir)
        )
        caption = (
            f"{html.escape(frame.get('rankType', ''))} · "
            f"{_format_number(frame.get('durationSec'))}s · "
            f"点击 {_format_number(frame.get('metricValue'))}"
        )
        figures.append(
            f"<figure><img src=\"{source}\" alt=\"高光帧\"/>"
            f"<figcaption>{caption}</figcaption></figure>"
        )
    return "".join(figures) or "<p class=\"muted\">暂无高光帧</p>"


def _render_insight_cards(insights):
    return "".join(
        "<li><b>{}</b><p>{}</p><small>{}</small></li>".format(
            html.escape(item["title"]),
            html.escape(item["conclusion"]),
            html.escape(" · ".join(item.get("evidenceRefs", []))),
        )
        for item in insights
    ) or "<li><b>暂无可用洞察</b><p>请补充结构化分析后重新渲染。</p></li>"


def _render_material_panel(material, asset_base_dir):
    analysis = material["analysis"]
    visual_items = "".join(
        "<p><b>{}</b>：{}<br/><small>{}</small></p>".format(
            html.escape(item["frameRef"]),
            html.escape(item["visualObservation"]),
            html.escape(item["commercialInterpretation"]),
        )
        for item in analysis["highlightAnalyses"]
    ) or "<p>暂无高光帧观察。</p>"
    return (
        "<div class=\"material-head\">"
        f"<div><p class=\"eyebrow\">#{html.escape(str(material['rank']))} · "
        f"{html.escape(material['materialId'])}</p>"
        f"<h3>{html.escape(material.get('materialName', ''))}</h3>"
        f"<p class=\"muted\">状态 {html.escape(material['extractionStatus'])}</p></div>"
        f"<div class=\"score\"><span>建议</span><b>{html.escape(analysis['decision'])}</b></div>"
        "</div>"
        f"<div class=\"frame-grid\">{_render_frames(material, asset_base_dir)}</div>"
        "<div class=\"material-body\">"
        "<div class=\"formula\"><b>人群与脚本证据</b>"
        f"<p>{html.escape(analysis['audienceConclusion'])}</p>"
        f"<p>{html.escape(analysis['scriptConclusion'])}</p></div>"
        f"<div class=\"formula\"><b>高光帧观察</b>{visual_items}</div>"
        "<div class=\"formula\"><b>综合判断与下一步</b>"
        f"<p>{html.escape(analysis['integratedConclusion'])}</p>"
        f"<p><b>下一步：</b>{html.escape(analysis['nextAction'])}</p></div>"
        "</div>"
    )


def _render_material_tabs(materials, default_material_id, asset_base_dir):
    buttons = []
    panels = []
    for material in materials:
        material_id = material["materialId"]
        safe_id = _safe_component(material_id)
        selected = material_id == default_material_id
        label = material.get("materialName") or material_id
        buttons.append(
            f'<button type="button" role="tab" id="tab-{safe_id}" '
            f'data-material-id="{html.escape(material_id)}" '
            f'aria-selected="{str(selected).lower()}" '
            f'aria-controls="panel-{safe_id}" tabindex="{0 if selected else -1}">'
            f'#{html.escape(str(material["rank"]))} {html.escape(label)}</button>'
        )
        panels.append(
            f'<article class="material-card" role="tabpanel" id="panel-{safe_id}" '
            f'aria-labelledby="tab-{safe_id}" data-material-id="{html.escape(material_id)}" '
            f'{"" if selected else "hidden"}>{_render_material_panel(material, asset_base_dir)}</article>'
        )
    return (
        '<div class="material-tabs" data-material-tabs>'
        '<div class="tab-list" role="tablist" aria-label="素材选择">'
        + "".join(buttons)
        + "</div><div class=\"material-panels\">"
        + "".join(panels)
        + "</div></div>"
    )


def _render_html_report(data, asset_base_dir=None):
    conclusion = data["conclusion"]
    insights = data["batchInsights"]
    priority_order = {"high": 0, "medium": 1, "low": 2}
    actions = sorted(
        conclusion.get("nextActions", []),
        key=lambda item: priority_order.get(item.get("priority"), 1),
    )
    actions_html = "".join(
        f"<li><b>{html.escape(item['title'])}</b><p>{html.escape(item['body'])}</p>"
        f"<small>{html.escape(item.get('priority', 'medium'))}</small></li>"
        for item in actions
    ) or "<li><b>暂无下一轮策略</b><p>请补充结构化结论后重新渲染。</p></li>"
    warnings_html = "".join(
        f"<li>{html.escape(str(warning))}</li>" for warning in data.get("warnings", [])
    ) or "<li>无阻断告警</li>"
    cover = next(
        (
            frame["framePath"]
            for material in data["materials"]
            for frame in material.get("highlightFrames", [])
            if frame.get("framePath")
        ),
        "",
    )
    cover_html = (
        f'<img src="{html.escape(_html_asset_src(cover, asset_base_dir))}" alt="代表高光帧"/>'
        if cover
        else '<div class="cover-empty">暂无高光帧封面</div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>千川 TOP 素材池商业诊断报告</title>
<style>
:root{{--bg:#120b08;--paper:#fff8ec;--panel:rgba(255,248,236,.94);--ink:#2a170f;--muted:#806758;--gold:#d79a4b;--line:rgba(82,45,24,.15);--shadow:0 28px 80px rgba(43,23,12,.18)}}*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;background:linear-gradient(135deg,#160d09,#3b1f15 44%,#f5dfbd 44%,#fff8ec);color:var(--ink)}}main{{width:min(1260px,calc(100% - 42px));margin:0 auto;padding:42px 0 72px}}.hero{{display:grid;grid-template-columns:1fr .86fr;gap:28px;color:#fff;margin-bottom:26px}}.hero-card,.section{{border:1px solid var(--line);box-shadow:var(--shadow)}}.hero-card{{padding:36px;border-radius:28px;background:linear-gradient(145deg,rgba(255,255,255,.12),rgba(255,255,255,.04))}}h1{{font-size:clamp(38px,5vw,72px);line-height:.95;margin:14px 0 18px}}.subtitle{{font-size:18px;line-height:1.75;color:rgba(255,255,255,.8)}}.eyebrow{{letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--gold);font-weight:800}}.cover{{border-radius:28px;overflow:hidden;min-height:360px;background:#24120d;box-shadow:var(--shadow)}}.cover img{{width:100%;height:100%;object-fit:cover;display:block}}.cover-empty{{display:grid;place-items:center;height:360px;color:#f7dec1}}.section{{background:var(--panel);border-radius:28px;padding:28px;margin:22px 0}}h2{{font-size:31px;margin:0 0 18px}}h3{{margin:0 0 10px;font-size:24px}}p{{line-height:1.72}}.muted,small{{color:var(--muted)}}.rec-list{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;list-style:none;padding:0}}.rec-list li{{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px}}.tab-list{{display:flex;gap:9px;overflow-x:auto;border-bottom:1px solid var(--line);padding-bottom:12px}}.tab-list button{{border:1px solid var(--line);border-radius:8px;background:#fff8ec;color:var(--ink);padding:10px 14px;white-space:nowrap;cursor:pointer;font:inherit}}.tab-list button[aria-selected="true"]{{background:#2c1810;color:#fff;border-color:#2c1810}}.tab-list button:focus-visible{{outline:3px solid var(--gold);outline-offset:2px}}.material-card{{background:#fffdf8;border:1px solid var(--line);border-radius:24px;padding:22px;margin:18px 0;box-shadow:0 16px 40px rgba(65,34,16,.1)}}.material-head{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}}.score{{min-width:88px;text-align:center;border-radius:20px;background:#2c1810;color:#fff;padding:14px}}.score span{{display:block;color:#d9b98d;font-size:12px}}.score b{{font-size:20px}}.frame-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}figure{{margin:0;border-radius:18px;overflow:hidden;background:#f4e3cd;border:1px solid var(--line)}}figure img{{width:100%;height:190px;object-fit:cover;display:block}}figcaption{{padding:10px 12px;color:var(--muted);font-size:13px}}.material-body{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.formula{{padding:17px;border-radius:18px;background:#f7ead8;border:1px solid var(--line)}}@media(max-width:980px){{.hero,.frame-grid,.material-body,.rec-list{{grid-template-columns:1fr}}.cover{{min-height:300px}}}}
</style></head><body><main>
<section class="hero"><div class="hero-card"><p class="eyebrow">QIANCHUAN MATERIAL PORTFOLIO REPORT</p><h1>千川 TOP 素材池商业诊断报告</h1><p class="subtitle">统计周期 {html.escape(data['period'].get('actualStartDate',''))} - {html.escape(data['period'].get('actualEndDate',''))}。报告状态：{html.escape(data['status'])}。</p></div><div class="cover">{cover_html}</div></section>
<section class="section"><h2>01｜TOP 素材组合结论</h2><p>{html.escape(conclusion.get('overallConclusion') or '结论待补充：请提供 diagnostic-ai-analysis.json 后重新渲染。')}</p><ul class="rec-list">{_render_insight_cards(insights['integrated'])}</ul></section>
<section class="section"><h2>02｜人群洞察</h2><ul class="rec-list">{_render_insight_cards(insights['audience'])}</ul></section>
<section class="section"><h2>03｜脚本洞察</h2><ul class="rec-list">{_render_insight_cards(insights['script'])}</ul></section>
<section class="section"><h2>04｜高光帧洞察</h2><ul class="rec-list">{_render_insight_cards(insights['highlight'])}</ul></section>
<section class="section"><h2>05｜单素材深度分析</h2>{_render_material_tabs(data['materials'], data['defaultMaterialId'], asset_base_dir)}</section>
<section class="section"><h2>06｜下一轮策略</h2><ul class="rec-list">{actions_html}</ul></section>
<section class="section"><h2>07｜数据质量与方法说明</h2><p>人群与脚本数据是素材级相关性，不是单帧因果归因。高光帧用于辅助画面复盘，业务结论仅来自本地 diagnostic-ai-analysis.json。</p><ul>{warnings_html}</ul></section>
</main><script>
function activateMaterialTab(tab) {{
  const container = tab.closest('[data-material-tabs]');
  container.querySelectorAll('[role="tab"]').forEach((item) => {{
    const selected = item === tab;
    item.setAttribute('aria-selected', String(selected));
    item.tabIndex = selected ? 0 : -1;
  }});
  container.querySelectorAll('[role="tabpanel"]').forEach((panel) => {{
    panel.hidden = panel.dataset.materialId !== tab.dataset.materialId;
  }});
  tab.focus();
}}
document.querySelectorAll('[data-material-tabs]').forEach((container) => {{
  const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
  tabs.forEach((tab, index) => {{
    tab.addEventListener('click', () => activateMaterialTab(tab));
    tab.addEventListener('keydown', (event) => {{
      let nextIndex = index;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = tabs.length - 1;
      else return;
      event.preventDefault();
      activateMaterialTab(tabs[nextIndex]);
    }});
  }});
}});
</script></body></html>"""


def generate_final_report(
    top_materials_json,
    peak_output_dir,
    diagnosis_records_dir,
    output_dir,
    analysis_json=None,
):
    top_payload = _read_json(top_materials_json, "top materials JSON")
    records_by_material = _load_records_for_materials(
        top_payload, diagnosis_records_dir
    )
    summaries_by_material = _load_summaries_for_materials(
        top_payload, peak_output_dir
    )
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
    _atomic_write_text(
        data_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(html_path, _render_html_report(data, output_root))
    return data_path, html_path, data
