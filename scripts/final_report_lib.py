import json
from datetime import datetime, timezone
import math
from pathlib import Path

from diagnostic_analysis_lib import build_diagnostic_analysis


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
        "summaryPath": summary.get("summaryPath", "")
        if isinstance(summary, dict)
        else "",
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


def build_final_report_data(
    top_payload,
    records_by_material,
    summaries_by_material,
    conclusion=None,
    batch_verification=None,
):
    materials = _selected_materials(top_payload)
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
    highlight_frame_count = sum(
        item["highlightFrameCount"] for item in material_cards
    )
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
