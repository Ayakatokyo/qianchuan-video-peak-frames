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
