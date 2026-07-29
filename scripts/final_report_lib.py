import ast
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
ALLOWED_INSIGHT_TOPICS = {"audience", "script", "integrated"}
INTERACTION_METRIC_LABELS = {
    "CLICK_COUNT": "整体点击次数",
    "LOSE_COUNT": "整体流失数",
    "LIKE_COUNT": "整体点赞次数",
    "COMMENT_COUNT": "整体评论次数",
    "SHARE_COUNT": "整体转发次数",
    "NEW_FOLLOWER_COUNT": "整体新增粉丝数",
}
INTERACTION_METRIC_ORDER = tuple(INTERACTION_METRIC_LABELS.keys())
EXECUTION_PROCESS_TERMS = (
    "--verify",
    "verify",
    "fresh rerun",
    "rerun",
    "rpa_request_failed",
    "batch-manifest",
    "batch-verification",
    "batchVerification",
    "top_materials_rpa_ready",
    "selectedMaterials",
    "rejectedMaterials",
    "cancelledMaterials",
    "补位",
    "剔除",
    "RPA 失败",
    "执行过程",
    "execution process",
    "流程修复",
    "修复后的",
)
REPORT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "final-report-template.html"
REPORT_DATA_PLACEHOLDER = "__REPORT_DATA_JSON__"


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


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)


def _execution_process_term(payload):
    for text in _iter_strings(payload):
        lowered = text.lower()
        for term in EXECUTION_PROCESS_TERMS:
            if term.lower() in lowered:
                return term
    return ""


def _contains_execution_process_term(payload):
    return bool(_execution_process_term(payload))


def _reject_execution_process_narrative(payload):
    term = _execution_process_term(payload)
    if term:
        raise FinalReportError(
            "conclusion references execution process: " + term
        )


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
    _reject_execution_process_narrative(payload)
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
            _validate_next_action(item, index)
            for index, item in enumerate(next_actions)
        ],
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


def _parse_script_info_for_formula(value):
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _script_formula_from_record(record):
    if not isinstance(record, dict):
        return []
    script_info = _parse_script_info_for_formula(record.get("scriptInfo"))
    formula = script_info.get("formula", [])
    if not isinstance(formula, list):
        return []
    result = []
    seen = set()
    for item in formula:
        if not isinstance(item, dict):
            continue
        level1 = item.get("level_1_label") or {}
        level2 = item.get("level_2_label") or {}
        label1 = _text(level1.get("label") if isinstance(level1, dict) else "")
        label2 = _text(level2.get("label") if isinstance(level2, dict) else "")
        key = (label1, label2)
        if label1 and label2 and key not in seen:
            seen.add(key)
            result.append({"level1": label1, "level2": label2})
    return result


def _scripts_by_material(diagnostic_scripts):
    result = {}
    if not isinstance(diagnostic_scripts, dict):
        return result
    for material in diagnostic_scripts.get("materials", []):
        if not isinstance(material, dict):
            continue
        material_id = _text(material.get("materialId"))
        if material_id:
            result[material_id] = material
    return result


def _script_records_by_material(records_by_material):
    result = {}
    for material_id, records in records_by_material.items():
        if not isinstance(records, list):
            continue
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


def _empty_script_analysis():
    return {
        "materialUri": "",
        "formula": [],
        "labels": [],
        "evidence": [],
        "text": "",
    }


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
        "scriptAnalysis": script_analysis or _empty_script_analysis(),
        "video": {"src": "", "poster": "", "durationSec": None},
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


def _load_batch_manifest(peak_output_dir):
    path = Path(peak_output_dir) / "batch-manifest.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pipeline_summary(batch_manifest, batch_verification):
    if not isinstance(batch_manifest, dict):
        batch_manifest = {}
    submitted_materials = batch_manifest.get("submittedMaterials", [])
    if not isinstance(submitted_materials, list):
        submitted_materials = []

    selected_count = 0
    rejected_count = 0
    cancelled_count = 0
    postprocess_succeeded_count = 0
    postprocess_failed_count = 0

    for item in submitted_materials:
        if not isinstance(item, dict):
            continue
        rpa_status = _text(item.get("rpaStatus"))
        postprocess_status = _text(item.get("postprocessStatus"))
        if rpa_status == "selected":
            selected_count += 1
        elif rpa_status == "rejected":
            rejected_count += 1
        elif rpa_status in {"cancelled", "not_submitted"}:
            cancelled_count += 1
        if postprocess_status == "succeeded":
            postprocess_succeeded_count += 1
        elif postprocess_status == "failed":
            postprocess_failed_count += 1

    submitted_count = selected_count + rejected_count
    verification_errors = []
    verification_warnings = []
    verification_valid = None
    if isinstance(batch_verification, dict):
        verification_valid = batch_verification.get("valid")
        verification_errors = [
            str(item)
            for item in batch_verification.get("errors", [])
            if _text(item) and not _contains_execution_process_term(item)
        ]
        verification_warnings = [
            str(item)
            for item in batch_verification.get("warnings", [])
            if _text(item) and not _contains_execution_process_term(item)
        ]

    if (
        not batch_manifest
        and verification_valid is None
        and not verification_errors
        and not verification_warnings
    ):
        return {}

    return {
        "targetTopN": batch_manifest.get("targetTopN"),
        "candidateCount": batch_manifest.get("candidateCount"),
        "submittedCount": submitted_count,
        "selectedCount": selected_count,
        "rejectedCount": rejected_count,
        "cancelledCount": cancelled_count,
        "postprocessSucceededCount": postprocess_succeeded_count,
        "postprocessFailedCount": postprocess_failed_count,
        "verificationValid": verification_valid,
        "verificationErrors": verification_errors,
        "verificationWarnings": verification_warnings,
    }


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
    batch_manifest=None,
):
    materials = _selected_materials(top_payload)
    diagnostic = build_diagnostic_analysis(top_payload, records_by_material)
    analyses = _analyses_by_material(conclusion)
    script_facts = _scripts_by_material(diagnostic["scripts"])
    script_records = _script_records_by_material(records_by_material)
    material_cards = [
        _material_card(
            material,
            summaries_by_material.get(material["materialId"], {}),
            analyses.get(material["materialId"]),
            records_by_material.get(material["materialId"], []),
            _material_script_analysis(
                material["materialId"], script_facts, script_records
            ),
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
        "pipelineSummary": _pipeline_summary(batch_manifest, batch_verification),
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


def _load_report_template():
    try:
        template = REPORT_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise FinalReportError(f"unable to read report template: {REPORT_TEMPLATE_PATH}") from exc
    if REPORT_DATA_PLACEHOLDER not in template:
        raise FinalReportError(
            f"report template is missing {REPORT_DATA_PLACEHOLDER}"
        )
    return template


def _json_for_html_script(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _report_data_for_template(data, asset_base_dir):
    materials = []
    for material in data.get("materials", []):
        highlight_frames = []
        for frame in material.get("highlightFrames", []):
            highlight_frames.append(
                {
                    "rankType": frame.get("rankType"),
                    "durationSec": frame.get("durationSec"),
                    "metricValue": frame.get("metricValue"),
                    "framePath": _html_asset_src(
                        frame.get("framePath", ""), asset_base_dir
                    ),
                }
            )
        materials.append(
            {
                "rank": material.get("rank"),
                "materialId": material.get("materialId"),
                "materialName": material.get("materialName"),
                "extractionStatus": material.get("extractionStatus"),
                "highlightFrameCount": material.get("highlightFrameCount"),
                "highlightFrames": highlight_frames,
                "analysis": material.get("analysis") or {},
            }
        )
    conclusion = data.get("conclusion", {})
    warnings = [
        _text(warning)
        for warning in data.get("warnings", [])
        if _text(warning) and not _contains_execution_process_term(warning)
    ]
    return {
        "schemaVersion": data.get("schemaVersion"),
        "generatedAt": data.get("generatedAt"),
        "status": data.get("status"),
        "period": data.get("period", {}),
        "materials": materials,
        "defaultMaterialId": data.get("defaultMaterialId"),
        "batchInsights": data.get("batchInsights", {}),
        "audience": data.get("audience", {}),
        "scripts": data.get("scripts", {}),
        "conclusion": {
            "overallConclusion": conclusion.get("overallConclusion", ""),
            "nextActions": conclusion.get("nextActions", []),
        },
        "warnings": warnings,
    }


def _render_html_report(data, asset_base_dir=None):
    template = _load_report_template()
    template_data = _report_data_for_template(data, asset_base_dir)
    return template.replace(
        REPORT_DATA_PLACEHOLDER, _json_for_html_script(template_data)
    )


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
    batch_manifest = _load_batch_manifest(peak_output_dir)
    conclusion = load_conclusion_analysis(analysis_json) if analysis_json else None
    data = build_final_report_data(
        top_payload,
        records_by_material,
        summaries_by_material,
        conclusion=conclusion,
        batch_verification=batch_verification,
        batch_manifest=batch_manifest,
    )
    output_root = Path(output_dir)
    data_path = output_root / "final-report-data.json"
    html_path = output_root / "final-report.html"
    _atomic_write_text(
        data_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(html_path, _render_html_report(data, output_root))
    return data_path, html_path, data
