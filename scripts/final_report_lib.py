import ast
from copy import deepcopy
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
import math
import re
from pathlib import Path
from batch_runtime import stage_counts
from asset_metrics_lib import (
    COST_METRIC,
    NET_SETTLEMENT_AMOUNT_METRIC,
    OVERALL_PAYMENT_AMOUNT_METRIC,
)

from diagnostic_analysis_lib import (
    build_diagnostic_analysis,
    build_material_audience,
    load_retained_diagnosis_records,
)
from analysis_units_lib import build_display_facts
from report_renderer import (
    ReportRenderingError,
    atomic_write_text,
    render_html_report,
    safe_component,
)
from highlight_export import export_highlight_workbook
from portfolio_projection import project_portfolio_summary

_safe_component = safe_component


FinalReportError = ReportRenderingError


ALLOWED_DECISIONS = {"scale", "remix", "hold", "stop", "unknown"}
ALLOWED_INSIGHT_TOPICS = {"audience", "script"}
PORTFOLIO_DIMENSION_TOPICS = ("commercial", "audience", "script", "highlight")
PORTFOLIO_DIMENSION_TOPICS_WITH_INTERACTION = PORTFOLIO_DIMENSION_TOPICS + ("interaction",)
HIGHLIGHT_DASHBOARD_CATEGORIES = ("portrait", "product", "other")
HIGHLIGHT_DASHBOARD_MINIMUMS = {
    "commonConclusion": 20,
    "derivativeDirectionBody": 20,
}
HIGHLIGHT_DASHBOARD_TITLE_MAX_LENGTH = 32
COMMERCIAL_EVIDENCE_REFS = {
    "commercial:cost",
    "commercial:netSettlementAmount",
    "commercial:overallPaymentAmount",
    "commercial:netSettlementRoi",
    "commercial:overallPaymentRoi",
    "commercial:newMaterialCount7d",
    "commercial:newMaterialStartDate",
    "commercial:newMaterialEndDate",
    "commercial:roi",
    "commercial:ctr",
    "commercial:conversionRate",
    "commercial:threeSecondCompletionRate",
    "commercial:cpm",
}
CHAPTER01_COMMERCIAL_EVIDENCE_KEYS = (
    "cost",
    "newMaterialCount7d",
    "newMaterialStartDate",
    "newMaterialEndDate",
    "roi",
    "ctr",
    "conversionRate",
    "threeSecondCompletionRate",
    "cpm",
)
COMMERCIAL_SOURCE_TERMS = ("资产表", "数据来源")
AUDIENCE_NONCANONICAL_TERMS = ("触达", "响应")
AI_QUALITY_MINIMUMS = {
    "overallConclusion": 100,
    "combinationPoint": 60,
    "batchInsight": 180,
    "materialConclusion": 120,
    "materialNextAction": 100,
    "visualObservation": 40,
    "commercialInterpretation": 80,
}
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
def _text(value):
    return str(value).strip() if value is not None else ""


def _portfolio_summary(value):
    return project_portfolio_summary(value)


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


def _reject_noncanonical_audience_terms(text, field_name):
    for term in AUDIENCE_NONCANONICAL_TERMS:
        if term in text:
            raise FinalReportError(
                f"{field_name}包含非标准人群指标表述：{term}；请使用展现次数、点击次数或成交金额"
            )


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
    if not isinstance(insights, list):
        raise FinalReportError("keyInsights must be a list")
    if not isinstance(recommendations, list):
        raise FinalReportError("materialRecommendations must be a list")
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
    interaction_conclusion = _text(raw.get("interactionConclusion"))
    integrated_conclusion = _text(raw.get("integratedConclusion"))
    decision = _text(raw.get("decision")) or "unknown"
    next_action = _text(raw.get("nextAction"))
    frame_coverage_note = _text(raw.get("frameCoverageNote"))
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
        "interactionConclusion": interaction_conclusion,
        "highlightAnalyses": [
            _validate_v2_highlight_analysis(item, index, item_index)
            for item_index, item in enumerate(highlights)
        ],
        "integratedConclusion": integrated_conclusion,
        "decision": decision,
        "nextAction": next_action,
        "frameCoverageNote": frame_coverage_note,
        "evidenceRefs": _string_list(
            raw.get("evidenceRefs", []), f"materialAnalyses[{index}].evidenceRefs"
        ),
    }


def _normalize_v2_conclusion(payload):
    _reject_execution_process_narrative(payload)
    for field_name in ("batchInsights", "materialAnalyses"):
        if field_name not in payload:
            raise FinalReportError(f"{field_name} is required for schemaVersion 2")
    insights = payload.get("batchInsights", [])
    material_analyses = payload.get("materialAnalyses", [])
    if not isinstance(insights, list):
        raise FinalReportError("batchInsights must be a list")
    if not isinstance(material_analyses, list):
        raise FinalReportError("materialAnalyses must be a list")
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
    }


def _validate_dimension_conclusion(raw, topic):
    if not isinstance(raw, dict):
        raise FinalReportError(
            f"portfolioDimensionConclusions.{topic} must be an object"
        )
    conclusion = _text(raw.get("conclusion"))
    if not conclusion:
        raise FinalReportError(
            f"portfolioDimensionConclusions.{topic}.conclusion is required"
        )
    return {
        "conclusion": conclusion,
        "evidenceRefs": _string_list(
            raw.get("evidenceRefs"),
            f"portfolioDimensionConclusions.{topic}.evidenceRefs",
        ),
    }


def _validate_hot_material_insight(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FinalReportError("hotMaterialInsight must be an object")
    if set(raw) != {"conclusion", "evidenceRefs"}:
        raise FinalReportError(
            "hotMaterialInsight may contain only conclusion and evidenceRefs"
        )
    conclusion = _text(raw.get("conclusion"))
    evidence_refs = _string_list(raw.get("evidenceRefs"), "hotMaterialInsight.evidenceRefs")
    if not conclusion:
        raise FinalReportError("hotMaterialInsight.conclusion is required")
    if not evidence_refs:
        raise FinalReportError("hotMaterialInsight.evidenceRefs must not be empty")
    return {"conclusion": conclusion, "evidenceRefs": list(dict.fromkeys(evidence_refs))}


def _normalize_v3_conclusion(payload):
    normalized = _normalize_v2_conclusion(payload)
    dimensions = payload.get("portfolioDimensionConclusions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(
        PORTFOLIO_DIMENSION_TOPICS
    ):
        raise FinalReportError(
            "portfolioDimensionConclusions must contain exactly "
            + ", ".join(PORTFOLIO_DIMENSION_TOPICS)
        )
    return {
        **normalized,
        "schemaVersion": 3,
        "overallConclusion": "",
        "portfolioDimensionConclusions": {
            topic: _validate_dimension_conclusion(dimensions[topic], topic)
            for topic in PORTFOLIO_DIMENSION_TOPICS
        },
    }


def _validate_highlight_dashboard_group(raw, index):
    if not isinstance(raw, dict):
        raise FinalReportError(f"highlightDashboard.groups[{index}] must be an object")
    category = _text(raw.get("category"))
    if category not in HIGHLIGHT_DASHBOARD_CATEGORIES:
        raise FinalReportError(
            f"highlightDashboard.groups[{index}].category is unsupported"
        )
    frame_refs = _string_list(
        raw.get("frameRefs"), f"highlightDashboard.groups[{index}].frameRefs"
    )
    if not frame_refs:
        raise FinalReportError(
            f"highlightDashboard.groups[{index}].frameRefs must not be empty"
        )
    raw_common = raw.get("commonConclusion")
    # Repair interim reports that stringified the structured list with
    # Python's repr (single quotes), preserving the intended point structure.
    if isinstance(raw_common, str) and raw_common.lstrip().startswith("["):
        try:
            parsed_common = ast.literal_eval(raw_common)
        except (SyntaxError, ValueError):
            parsed_common = None
        if isinstance(parsed_common, list):
            raw_common = parsed_common
    legacy_common = isinstance(raw_common, str)
    if legacy_common:
        common_text = _text(raw_common)
        if not common_text:
            raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion is required")
        if len(common_text) < HIGHLIGHT_DASHBOARD_MINIMUMS["commonConclusion"]:
            raise FinalReportError(
                f"highlightDashboard.groups[{index}].commonConclusion must contain at least "
                f"{HIGHLIGHT_DASHBOARD_MINIMUMS['commonConclusion']} characters"
            )
        normalized_common = [{"title": "共性结论", "body": common_text, "evidenceRefs": frame_refs}]
    elif isinstance(raw_common, list):
        if not raw_common:
            raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion must not be empty")
        normalized_common = []
        for item_index, item in enumerate(raw_common):
            if not isinstance(item, dict):
                raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion[{item_index}] must be an object")
            title = _text(item.get("title"))
            body = _text(item.get("body"))
            evidence_refs = _string_list(item.get("evidenceRefs"), f"highlightDashboard.groups[{index}].commonConclusion[{item_index}].evidenceRefs")
            if not title or not body or not evidence_refs:
                raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion[{item_index}] requires title, body, and evidenceRefs")
            if len(title) > HIGHLIGHT_DASHBOARD_TITLE_MAX_LENGTH:
                raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion[{item_index}].title must contain at most {HIGHLIGHT_DASHBOARD_TITLE_MAX_LENGTH} characters")
            if len(body) < HIGHLIGHT_DASHBOARD_MINIMUMS["commonConclusion"]:
                raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion[{item_index}].body must contain at least {HIGHLIGHT_DASHBOARD_MINIMUMS['commonConclusion']} characters")
            normalized_common.append({"title": title, "body": body, "evidenceRefs": evidence_refs})
    else:
        raise FinalReportError(f"highlightDashboard.groups[{index}].commonConclusion must be a string or list")
    directions = raw.get("derivativeDirections", [])
    if not isinstance(directions, list):
        raise FinalReportError(
            f"highlightDashboard.groups[{index}].derivativeDirections must be a list"
        )
    if not directions:
        raise FinalReportError(
            f"highlightDashboard.groups[{index}].derivativeDirections must not be empty"
        )
    normalized_directions = []
    for direction_index, direction in enumerate(directions):
        if not isinstance(direction, dict):
            raise FinalReportError(
                "highlightDashboard.groups[{}].derivativeDirections[{}] must be an object".format(
                    index, direction_index
                )
            )
        title = _text(direction.get("title"))
        body = _text(direction.get("body"))
        evidence_refs = _string_list(
            direction.get("evidenceRefs"),
            "highlightDashboard.groups[{}].derivativeDirections[{}].evidenceRefs".format(
                index, direction_index
            ),
        )
        if not title or not body or not evidence_refs:
            raise FinalReportError(
                "highlightDashboard.groups[{}].derivativeDirections[{}] requires title, body, and evidenceRefs".format(
                    index, direction_index
                )
            )
        if len(title) > HIGHLIGHT_DASHBOARD_TITLE_MAX_LENGTH:
            raise FinalReportError(
                "highlightDashboard.groups[{}].derivativeDirections[{}].title must contain at most {} characters".format(
                    index, direction_index, HIGHLIGHT_DASHBOARD_TITLE_MAX_LENGTH
                )
            )
        if len(body) < HIGHLIGHT_DASHBOARD_MINIMUMS["derivativeDirectionBody"]:
            raise FinalReportError(
                "highlightDashboard.groups[{}].derivativeDirections[{}].body must contain at least {} characters".format(
                    index,
                    direction_index,
                    HIGHLIGHT_DASHBOARD_MINIMUMS["derivativeDirectionBody"],
                )
            )
        normalized_directions.append(
            {"title": title, "body": body, "evidenceRefs": evidence_refs}
        )
    if not legacy_common and len(normalized_common) != len(normalized_directions):
        raise FinalReportError(
            f"highlightDashboard.groups[{index}] commonConclusion and derivativeDirections must have equal length"
        )
    return {
        "category": category,
        "frameRefs": frame_refs,
        "commonConclusion": normalized_common,
        "derivativeDirections": normalized_directions,
    }


def _normalize_v4_conclusion(payload):
    normalized = _normalize_v3_conclusion(payload)
    dashboard = payload.get("highlightDashboard")
    if not isinstance(dashboard, dict):
        raise FinalReportError("highlightDashboard must be an object")
    groups = dashboard.get("groups")
    if not isinstance(groups, list):
        raise FinalReportError("highlightDashboard.groups must be a list")
    normalized_groups = [
        _validate_highlight_dashboard_group(group, index)
        for index, group in enumerate(groups)
    ]
    categories = [group["category"] for group in normalized_groups]
    if len(set(categories)) != len(categories):
        raise FinalReportError("highlightDashboard.groups must not repeat category")
    expected_categories = [
        category for category in HIGHLIGHT_DASHBOARD_CATEGORIES if category in categories
    ]
    if categories != expected_categories:
        raise FinalReportError(
            "highlightDashboard.groups must use the fixed category order: portrait, product, other"
        )
    seen_copy_keys = set()
    seen_titles = set()
    for group in normalized_groups:
        for section in ("commonConclusion", "derivativeDirections"):
            for item in group[section]:
                title_key = (section, item["title"].strip())
                if title_key in seen_titles:
                    raise FinalReportError(
                        "highlightDashboard groups must not reuse identical title across categories; each frame must be classified exactly once"
                    )
                seen_titles.add(title_key)
                copy_key = (section, item["title"].strip(), item["body"].strip())
                if copy_key in seen_copy_keys:
                    raise FinalReportError(
                        "highlightDashboard groups must not reuse identical title/body across categories"
                    )
                seen_copy_keys.add(copy_key)
    return {
        **normalized,
        "schemaVersion": 4,
        "highlightDashboard": {"groups": normalized_groups},
    }


def _normalize_v5_conclusion(payload):
    normalized = _normalize_v2_conclusion(payload)
    dimensions = payload.get("portfolioDimensionConclusions")
    compact = isinstance(dimensions, dict) and set(dimensions) == {"commercial"}
    if not isinstance(dimensions, dict) or (
        not compact and set(dimensions) != set(PORTFOLIO_DIMENSION_TOPICS_WITH_INTERACTION)
    ):
        raise FinalReportError(
            "portfolioDimensionConclusions must contain exactly "
            + ", ".join(PORTFOLIO_DIMENSION_TOPICS_WITH_INTERACTION)
        )
    legacy_payload = dict(payload)
    legacy_payload["portfolioDimensionConclusions"] = {
        topic: dimensions.get(topic, {"conclusion": "兼容字段", "evidenceRefs": []})
        for topic in PORTFOLIO_DIMENSION_TOPICS
    }
    keyword_cloud_insight = payload.get("keywordCloudInsight")
    normalized_keyword_cloud = None
    if isinstance(keyword_cloud_insight, dict):
        normalized_keyword_cloud = {
            "oneSentenceConclusion": _text(keyword_cloud_insight.get("oneSentenceConclusion")),
            "summary": _text(keyword_cloud_insight.get("summary")),
            "sellingPoints": [
                {
                    "title": _text(item.get("title")),
                    "description": _text(item.get("description")),
                    "keywordRefs": _string_list(item.get("keywordRefs", []), "keywordCloudInsight.sellingPoints.keywordRefs"),
                    "evidenceRefs": _string_list(item.get("evidenceRefs", []), "keywordCloudInsight.sellingPoints.evidenceRefs"),
                }
                for item in keyword_cloud_insight.get("sellingPoints", [])[:4]
                if isinstance(item, dict)
            ],
        }
    return {
        **normalized,
        "schemaVersion": 5,
        "overallConclusion": _text(payload.get("overallConclusion")),
        "portfolioDimensionConclusions": {
            topic: _validate_dimension_conclusion(dimensions[topic], topic)
            for topic in (("commercial",) if compact else PORTFOLIO_DIMENSION_TOPICS_WITH_INTERACTION)
        },
        "highlightDashboard": _normalize_v4_conclusion(legacy_payload)["highlightDashboard"],
        **({"hotMaterialInsight": _validate_hot_material_insight(payload.get("hotMaterialInsight"))}
           if "hotMaterialInsight" in payload else {}),
        **({"keywordCloudInsight": normalized_keyword_cloud} if normalized_keyword_cloud is not None else {}),
        **({"keywordCloudReview": [
            {"keyword": _text(item.get("keyword")), "decision": _text(item.get("decision")), "target": _text(item.get("target"))}
            for item in payload.get("keywordCloudReview", [])
            if isinstance(item, dict) and _text(item.get("keyword")) and _text(item.get("decision")) in {"accept", "exclude", "merge"}
        ]} if isinstance(payload.get("keywordCloudReview"), list) else {}),
    }


def normalize_conclusion_analysis(payload):
    if not isinstance(payload, dict):
        raise FinalReportError("conclusion analysis must be an object")
    schema_version = payload.get("schemaVersion")
    if schema_version == 1:
        return _normalize_v1_conclusion(payload)
    if schema_version == 2:
        return _normalize_v2_conclusion(payload)
    if schema_version == 3:
        return _normalize_v3_conclusion(payload)
    if schema_version == 4:
        return _normalize_v4_conclusion(payload)
    if schema_version == 5:
        return _normalize_v5_conclusion(payload)
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
        item = {
            "rank": raw.get("rank", index),
            "materialId": material_id,
            "materialName": _text(raw.get("materialName")),
            "materialType": _text(raw.get("materialType")),
            "videoType": _text(raw.get("videoType")),
            "totalCost": round(_number(raw.get("totalCost")), 6),
        }
        if isinstance(raw.get("materialRanking"), dict):
            item["materialRanking"] = deepcopy(raw["materialRanking"])
        selected.append(item)
    if not selected:
        raise FinalReportError("top materials JSON contains no usable materials")
    return selected


def _empty_material_analysis(material_id):
    return {
        "materialId": material_id,
        "audienceConclusion": "数据不足以判断。",
        "scriptConclusion": "数据不足以判断。",
        "interactionConclusion": "数据不足以判断。",
        "highlightAnalyses": [],
        "integratedConclusion": "暂无综合判断。",
        "decision": "unknown",
        "nextAction": "补充结构化分析后复盘。",
        "frameCoverageNote": "",
        "evidenceRefs": [],
    }


def _analyses_by_material(conclusion):
    if not conclusion:
        return {}
    if conclusion.get("schemaVersion") in {2, 3, 4, 5}:
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
    if conclusion.get("schemaVersion") in {2, 3, 4, 5}:
        for insight in conclusion.get("batchInsights", []):
            result[insight["topic"]].append(insight)
        return result
    return result


TOP_MATERIAL_ROI_METRICS = (
    ("netSettlementRoi", "净成交 ROI"),
    ("overallPaymentRoi", "整体支付 ROI"),
)
TOP_MATERIAL_ROI_METRIC_KEYS = {
    "cost": COST_METRIC,
    "netSettlementAmount": NET_SETTLEMENT_AMOUNT_METRIC,
    "overallPaymentAmount": OVERALL_PAYMENT_AMOUNT_METRIC,
}
TOP_MATERIAL_ROI_TOTAL_KEYS = {
    "netSettlementRoi": "netSettlementAmount",
    "overallPaymentRoi": "overallPaymentAmount",
}


def _top_material_roi_data(top_payload, materials, facts):
    expected_ids = [material["materialId"] for material in materials]
    default = {
        "source": "unavailable",
        "actualStartDate": _text(top_payload.get("actualStartDate")),
        "actualEndDate": _text(top_payload.get("actualEndDate")),
        "materialCount": len(expected_ids),
        "materialIds": expected_ids,
        "metrics": [
            {
                "key": key,
                "label": label,
                "value": None,
                "displayValue": None,
                "available": False,
            }
            for key, label in TOP_MATERIAL_ROI_METRICS
        ],
    }
    if not facts:
        return default
    if not isinstance(facts, dict) or facts.get("schemaVersion") != 1:
        raise FinalReportError("top material ROI facts schemaVersion must be 1")
    if facts.get("actualStartDate") != default["actualStartDate"] or facts.get(
        "actualEndDate"
    ) != default["actualEndDate"]:
        raise FinalReportError("top material ROI facts period does not match report")
    if facts.get("materialIds") != expected_ids:
        raise FinalReportError("top material ROI facts do not match verified report materials")
    if facts.get("metricKeys") != TOP_MATERIAL_ROI_METRIC_KEYS:
        raise FinalReportError("top material ROI facts metric keys are invalid")
    raw_totals = facts.get("totals")
    if not isinstance(raw_totals, dict) or set(raw_totals) != set(
        TOP_MATERIAL_ROI_METRIC_KEYS
    ):
        raise FinalReportError("top material ROI facts totals are invalid")
    totals = {
        key: _roi_decimal(raw_totals[key], "top material ROI facts totals")
        for key in TOP_MATERIAL_ROI_METRIC_KEYS
    }
    raw_metrics = facts.get("metrics")
    if not isinstance(raw_metrics, list) or len(raw_metrics) != len(TOP_MATERIAL_ROI_METRICS):
        raise FinalReportError("top material ROI facts metrics are invalid")
    metrics = []
    for raw, (key, label) in zip(raw_metrics, TOP_MATERIAL_ROI_METRICS):
        if not isinstance(raw, dict) or raw.get("key") != key or raw.get("label") != label:
            raise FinalReportError("top material ROI facts metrics are invalid")
        available = raw.get("available")
        value = raw.get("value")
        expected_formula = "sum(%s) / sum(%s)" % (
            TOP_MATERIAL_ROI_METRIC_KEYS[TOP_MATERIAL_ROI_TOTAL_KEYS[key]],
            COST_METRIC,
        )
        if raw.get("formula") != expected_formula:
            raise FinalReportError("top material ROI facts formula is invalid")
        if type(available) is not bool or (available and not _text(value)) or (
            not available and value is not None
        ):
            raise FinalReportError("top material ROI facts metrics are invalid")
        expected_available = totals["cost"] != 0
        if available != expected_available:
            raise FinalReportError("top material ROI facts availability is invalid")
        if available:
            actual_value = _roi_decimal(value, "top material ROI facts metric")
            expected_value = totals[TOP_MATERIAL_ROI_TOTAL_KEYS[key]] / totals["cost"]
            if _roi_decimal_text(actual_value) != _roi_decimal_text(expected_value):
                raise FinalReportError("top material ROI facts value is invalid")
        metrics.append({
            "key": key,
            "label": label,
            "formula": expected_formula,
            "value": _text(value) if available else None,
            "displayValue": (
                format(
                    actual_value.quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    ),
                    ".2f",
                )
                if available else None
            ),
            "available": available,
        })
    return {
        **default,
        "source": "verifiedAssetFacts",
        "metricKeys": TOP_MATERIAL_ROI_METRIC_KEYS,
        "totals": {
            key: _roi_decimal_text(value) for key, value in totals.items()
        },
        "metrics": metrics,
    }


def _roi_decimal(value, label):
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise FinalReportError("%s are invalid" % label)
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise FinalReportError("%s are invalid" % label) from error
    if not result.is_finite() or result < 0:
        raise FinalReportError("%s are invalid" % label)
    return result


def _roi_decimal_text(value):
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


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


def _empty_video_metadata():
    return {"src": "", "poster": "", "durationSec": None}


def _material_video_metadata(records):
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("recordType") != "material_info":
            continue
        video_url = _text(record.get("videoUrl"))
        if not video_url:
            continue
        duration = _number(record.get("videoDuration"), default=None)
        return {
            "src": video_url,
            "poster": _text(record.get("thumbnailUrl")),
            "durationSec": duration,
        }
    return _empty_video_metadata()


def _material_card(material, summary, analysis, records=None, script_analysis=None):
    material_records = records or []
    frames = summary.get("frames", []) if isinstance(summary, dict) else []
    warnings = summary.get("warnings", []) if isinstance(summary, dict) else []
    missing_frames = (
        summary.get("missingFrames", []) if isinstance(summary, dict) else []
    )
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
        "missingFrames": missing_frames if isinstance(missing_frames, list) else [],
        "analysis": analysis or _empty_material_analysis(material["materialId"]),
        "interactionTimeline": _material_interaction_timeline(material_records),
        "materialAudience": build_material_audience(
            material_records, material["materialId"]
        ),
        "scriptAnalysis": script_analysis or _empty_script_analysis(),
        "video": _material_video_metadata(material_records),
    }


def _status(conclusion, material_cards, batch_verification, input_warnings=None):
    if conclusion is None:
        return "draft"
    if input_warnings:
        return "partial"
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

    manifest_stage_counts = batch_manifest.get("stageCounts", {})
    report_ready = (
        isinstance(manifest_stage_counts, dict)
        and manifest_stage_counts.get("reportReadyCount") == 1
    ) or (batch_manifest.get("reportReady") or {}).get("status") == "ready"
    return {
        "runId": batch_manifest.get("runId"),
        "targetTopN": batch_manifest.get("targetTopN"),
        "candidateCount": batch_manifest.get("candidateCount"),
        "submittedCount": submitted_count,
        "selectedCount": selected_count,
        "rejectedCount": rejected_count,
        "cancelledCount": cancelled_count,
        "postprocessSucceededCount": postprocess_succeeded_count,
        "postprocessFailedCount": postprocess_failed_count,
        **stage_counts(submitted_materials, report_ready=report_ready),
        "verificationValid": verification_valid,
        "verificationErrors": verification_errors,
        "verificationWarnings": verification_warnings,
    }


def _known_evidence_refs(diagnostic, summaries_by_material, top_material_roi):
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
    if (
        top_material_roi.get("source") == "verifiedAssetFacts"
        or diagnostic.get("portfolio")
    ):
        refs.update(COMMERCIAL_EVIDENCE_REFS)
    hot_rule = diagnostic.get("portfolio", {}).get("hotMaterialRule", {})
    if isinstance(hot_rule, dict):
        if "qualifyingCount" in hot_rule:
            refs.add("commercial:hotMaterialCount")
        for material in hot_rule.get("qualifyingMaterials", []):
            if isinstance(material, dict) and _text(material.get("materialId")):
                refs.add(f"commercial:hotMaterial:{_text(material['materialId'])}")
    for metric, metric_data in diagnostic.get("interaction", {}).get("metrics", {}).items():
        if metric_data.get("available") and metric_data.get("totalMetricValue") is not None:
            refs.add(f"interaction:{metric}:total")
        for bucket in metric_data.get("buckets", []):
            refs.add(f"interaction:{metric}:{bucket['label']}")
    return refs


def _chapter01_evidence_refs(diagnostic):
    """Return only evidence refs backed by the full Chapter 01 portfolio."""
    portfolio = diagnostic.get("portfolio") if isinstance(diagnostic, dict) else None
    if not isinstance(portfolio, dict):
        return set()
    refs = {
        f"commercial:{key}"
        for key in CHAPTER01_COMMERCIAL_EVIDENCE_KEYS
        if key in portfolio.get("summaryMetrics", {})
        or key in portfolio
    }
    hot_rule = portfolio.get("hotMaterialRule")
    if isinstance(hot_rule, dict):
        if "qualifyingCount" in hot_rule:
            refs.add("commercial:hotMaterialCount")
        for material in hot_rule.get("qualifyingMaterials", []):
            if isinstance(material, dict) and _text(material.get("materialId")):
                refs.add(f"commercial:hotMaterial:{_text(material['materialId'])}")
    return refs


def _validate_v2_evidence(
    conclusion, diagnostic, summaries_by_material, material_cards, top_material_roi
):
    if not conclusion or conclusion.get("schemaVersion") not in {2, 3, 4, 5}:
        return
    known_refs = _known_evidence_refs(
        diagnostic, summaries_by_material, top_material_roi
    )
    chapter01_refs = _chapter01_evidence_refs(diagnostic)
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
        if insight.get("topic") == "audience":
            _reject_noncanonical_audience_terms(
                insight["conclusion"], "batchInsights audience conclusion"
            )
        for reference in insight["evidenceRefs"]:
            require_known(reference)
    hot_insight = conclusion.get("hotMaterialInsight")
    hot_rule = diagnostic.get("portfolio", {}).get("hotMaterialRule")
    if hot_rule is not None:
        if not isinstance(hot_rule, dict):
            raise FinalReportError("portfolio.hotMaterialRule must be an object")
    if hot_insight is not None:
        if not isinstance(hot_rule, dict):
            raise FinalReportError("hotMaterialInsight requires portfolio.hotMaterialRule")
        hot_refs = {
            "commercial:hotMaterialCount",
            *{
                f"commercial:hotMaterial:{_text(item.get('materialId'))}"
                for item in hot_rule.get("qualifyingMaterials", [])
                if isinstance(item, dict) and _text(item.get("materialId"))
            },
        }
        if not any(reference in hot_refs for reference in hot_insight["evidenceRefs"]):
            raise FinalReportError("hotMaterialInsight must reference hot material evidence")
        for reference in hot_insight["evidenceRefs"]:
            if reference not in hot_refs:
                raise FinalReportError(f"hotMaterialInsight has invalid evidence reference: {reference}")
    for material_analysis in conclusion["materialAnalyses"]:
        _reject_noncanonical_audience_terms(
            material_analysis["audienceConclusion"],
            f"materialAnalyses[{material_analysis['materialId']}].audienceConclusion",
        )
        material_id = material_analysis["materialId"]
        if material_id not in selected_ids:
            raise FinalReportError(f"unknown material analysis: {material_id}")
        analysis_ids.add(material_id)
        for reference in material_analysis["evidenceRefs"]:
            require_known(reference)
        for highlight in material_analysis["highlightAnalyses"]:
            require_known(highlight["frameRef"])
        material_card = next(
            item for item in material_cards if item["materialId"] == material_id
        )
        if material_card["missingFrames"] and not material_analysis["frameCoverageNote"]:
            raise FinalReportError(
                f"material analysis for {material_id} requires frameCoverageNote "
                "because expected frames are missing"
            )
    missing_ids = sorted(successful_ids - analysis_ids)
    if missing_ids:
        raise FinalReportError(
            "missing material analysis for succeeded material: "
            + ", ".join(missing_ids)
        )
    if conclusion.get("schemaVersion") not in {3, 4, 5}:
        return

    dimensions = conclusion["portfolioDimensionConclusions"]
    batch_text_by_topic = {}
    for insight in conclusion["batchInsights"]:
        batch_text_by_topic.setdefault(insight["topic"], set()).add(
            " ".join(insight["conclusion"].split())
        )
    seen_dimension_texts = set()
    dimensions = conclusion["portfolioDimensionConclusions"]
    if conclusion.get("schemaVersion") == 5 and set(dimensions) == {"commercial"}:
        dimension_topics = ("commercial",)
    else:
        dimension_topics = (
            PORTFOLIO_DIMENSION_TOPICS_WITH_INTERACTION
            if conclusion.get("schemaVersion") == 5
            else PORTFOLIO_DIMENSION_TOPICS
        )
    for topic in dimension_topics:
        dimension = dimensions[topic]
        dimension_text = " ".join(dimension["conclusion"].split())
        if dimension_text in seen_dimension_texts:
            raise FinalReportError(
                "portfolioDimensionConclusions duplicates another dimension conclusion"
            )
        seen_dimension_texts.add(dimension_text)
        if topic == "audience":
            _reject_noncanonical_audience_terms(
                dimension_text, "portfolioDimensionConclusions.audience.conclusion"
            )
        if not dimension["evidenceRefs"]:
            raise FinalReportError(
                f"portfolioDimensionConclusions.{topic}.evidenceRefs must not be empty"
            )
        for reference in dimension["evidenceRefs"]:
            if (
                conclusion.get("schemaVersion") == 5
                and topic == "commercial"
                and reference not in chapter01_refs
            ):
                raise FinalReportError(
                    "portfolioDimensionConclusions.commercial has invalid evidence reference"
                )
            if reference not in known_refs:
                raise FinalReportError(f"unknown evidence reference: {reference}")
            if not reference.startswith(topic + ":"):
                raise FinalReportError(
                    f"portfolioDimensionConclusions.{topic} has invalid evidence reference"
                )
        if topic in batch_text_by_topic and dimension_text in batch_text_by_topic[topic]:
            raise FinalReportError(
                f"portfolioDimensionConclusions.{topic} duplicates detailed insight"
            )
    commercial_text = dimensions["commercial"]["conclusion"]
    for term in COMMERCIAL_SOURCE_TERMS:
        if term in commercial_text:
            raise FinalReportError(
                "portfolioDimensionConclusions.commercial must not reference " + term
            )

    if conclusion.get("schemaVersion") not in {4, 5}:
        return

    expected_frame_refs = {
        f"highlight:{card['materialId']}:{frame['rankType']}"
        for card in material_cards
        if card["extractionStatus"] == "succeeded"
        for frame in card["highlightFrames"]
    }
    actual_frame_refs = []
    for group in conclusion["highlightDashboard"]["groups"]:
        group_refs = set(group["frameRefs"])
        if len(group_refs) != len(group["frameRefs"]):
            raise FinalReportError(
                "highlightDashboard.groups frameRefs must contain each frame exactly once"
            )
        for reference in group_refs:
            require_known(reference)
            if not reference.startswith("highlight:"):
                raise FinalReportError(
                    "highlightDashboard.groups frameRefs must be highlight evidence"
                )
        actual_frame_refs.extend(group["frameRefs"])
        common_items = group.get("commonConclusion", [])
        if isinstance(common_items, list):
            for item in common_items:
                for reference in item.get("evidenceRefs", []):
                    require_known(reference)
                    if reference not in group_refs:
                        raise FinalReportError(
                            "highlightDashboard commonConclusion must reference its group"
                        )
        for direction in group["derivativeDirections"]:
            for reference in direction["evidenceRefs"]:
                require_known(reference)
                if reference not in group_refs:
                    raise FinalReportError(
                        "highlightDashboard derivativeDirections must reference its group"
                    )
    if set(actual_frame_refs) != expected_frame_refs or len(actual_frame_refs) != len(
        expected_frame_refs
    ):
        raise FinalReportError(
            "highlightDashboard.groups must cover each successful highlight frame exactly once"
        )


def _repeated_values(items, field_name):
    values = {}
    for item in items:
        value = _text(item.get(field_name))
        if value:
            values.setdefault(value, []).append(_text(item.get("materialId")))
    return [material_ids for material_ids in values.values() if len(material_ids) > 1]


def _duplicate_highlight_observations(material_id, highlights):
    """Return frame-reference pairs whose observations only differ by frame labels."""
    by_observation = {}
    for highlight in highlights:
        observation = " ".join(_text(highlight.get("visualObservation")).split())
        observation = re.sub(
            r"(?:当前帧(?:为|是)?\s*)?(?:zero_second|peak_top_[1-5])",
            "<frame>",
            observation,
            flags=re.IGNORECASE,
        )
        frame_ref = _text(highlight.get("frameRef"))
        if observation and frame_ref:
            by_observation.setdefault(observation, []).append(frame_ref)
    duplicates = []
    for frame_refs in by_observation.values():
        if len(frame_refs) > 1:
            first = frame_refs[0]
            duplicates.extend((first, other) for other in frame_refs[1:])
    return duplicates


def _duplicate_highlight_interpretations(material_id, highlights):
    """Return frame-reference pairs with repeated commercial interpretations."""
    by_interpretation = {}
    for highlight in highlights:
        interpretation = " ".join(
            _text(highlight.get("commercialInterpretation")).split()
        )
        interpretation = re.sub(
            r"(?:当前帧(?:为|是)?\s*)?(?:zero_second|peak_top_[1-5])",
            "<frame>",
            interpretation,
            flags=re.IGNORECASE,
        )
        frame_ref = _text(highlight.get("frameRef"))
        if interpretation and frame_ref:
            by_interpretation.setdefault(interpretation, []).append(frame_ref)
    duplicates = []
    for frame_refs in by_interpretation.values():
        if len(frame_refs) > 1:
            first = frame_refs[0]
            duplicates.extend((first, other) for other in frame_refs[1:])
    return duplicates


def _audience_insight_metrics(insight):
    metrics = set()
    for reference in insight.get("evidenceRefs", []):
        parts = _text(reference).split(":", 2)
        if len(parts) == 3 and parts[0] == "audience":
            metrics.add(parts[1])
    return metrics


def _validate_v2_analysis_quality(conclusion, material_cards):
    if not conclusion or conclusion.get("schemaVersion") not in {2, 3, 4, 5}:
        return

    failures = []
    minimums = AI_QUALITY_MINIMUMS
    if (
        conclusion.get("schemaVersion") in {2, 5}
        and len(conclusion["overallConclusion"]) < minimums["overallConclusion"]
    ):
        failures.append(
            "overallConclusion must contain at least "
            f"{minimums['overallConclusion']} characters"
        )

    if conclusion.get("schemaVersion") in {3, 4, 5}:
        for topic, dimension in conclusion["portfolioDimensionConclusions"].items():
            if len(dimension["conclusion"]) < minimums["combinationPoint"]:
                failures.append(
                    f"portfolioDimensionConclusions.{topic}.conclusion must contain at least "
                    f"{minimums['combinationPoint']} characters"
                )

    insights_by_topic = {topic: [] for topic in ALLOWED_INSIGHT_TOPICS}
    for index, insight in enumerate(conclusion["batchInsights"]):
        insights_by_topic[insight["topic"]].append(insight)
        if len(insight["conclusion"]) < minimums["batchInsight"]:
            failures.append(
                f"batchInsights[{index}].conclusion must contain at least "
                f"{minimums['batchInsight']} characters"
            )
        if not insight["evidenceRefs"]:
            failures.append(f"batchInsights[{index}].evidenceRefs must not be empty")
    for topic, required_count in (("audience", 3), ("script", 2)):
        if len(insights_by_topic[topic]) < required_count:
            failures.append(
                f"batchInsights requires at least {required_count} {topic} insights"
            )
    audience_metrics = set()
    expected_audience_metrics = {
        "SHOW_COUNT",
        "CLICK_COUNT",
        "TRANSACTION_AMOUNT",
    }
    for index, insight in enumerate(insights_by_topic["audience"]):
        metrics = _audience_insight_metrics(insight)
        if not metrics:
            continue
        if len(metrics) != 1:
            failures.append(
                f"batchInsights audience insight {index} must use a single metric"
            )
            continue
        metric = next(iter(metrics))
        if metric not in expected_audience_metrics:
            failures.append(
                f"batchInsights audience insight {index} uses unsupported metric {metric}"
            )
            continue
        audience_metrics.add(metric)
    if audience_metrics and audience_metrics != expected_audience_metrics:
        failures.append(
            "batchInsights audience insights must cover SHOW_COUNT, CLICK_COUNT, and TRANSACTION_AMOUNT"
        )
    if audience_metrics and len(insights_by_topic["audience"]) != 3:
        failures.append(
            "batchInsights audience insights must contain exactly 3 metric insights"
        )

    analyses = {
        analysis["materialId"]: analysis
        for analysis in conclusion["materialAnalyses"]
    }
    successful_cards = [
        card for card in material_cards if card["extractionStatus"] == "succeeded"
    ]
    successful_analyses = []
    for card in successful_cards:
        material_id = card["materialId"]
        analysis = analyses.get(material_id)
        if analysis is None:
            continue
        successful_analyses.append(analysis)
        for field_name in (
            "audienceConclusion",
            "scriptConclusion",
            "integratedConclusion",
        ):
            if len(analysis[field_name]) < minimums["materialConclusion"]:
                failures.append(
                    f"materialAnalyses[{material_id}].{field_name} must contain at least "
                    f"{minimums['materialConclusion']} characters"
                )
        if conclusion.get("schemaVersion") == 5 and not analysis["interactionConclusion"]:
            failures.append(
                f"materialAnalyses[{material_id}].interactionConclusion is required"
            )
        if analysis["interactionConclusion"] and len(analysis["interactionConclusion"]) < minimums["materialConclusion"]:
            failures.append(
                f"materialAnalyses[{material_id}].interactionConclusion must contain at least "
                f"{minimums['materialConclusion']} characters"
            )
        if len(analysis["nextAction"]) < minimums["materialNextAction"]:
            failures.append(
                f"materialAnalyses[{material_id}].nextAction must contain at least "
                f"{minimums['materialNextAction']} characters"
            )
        if not analysis["evidenceRefs"]:
            failures.append(
                f"materialAnalyses[{material_id}].evidenceRefs must not be empty"
            )

        expected_frame_refs = {
            f"highlight:{material_id}:{frame['rankType']}"
            for frame in card["highlightFrames"]
        }
        actual_frame_refs = {
            highlight["frameRef"] for highlight in analysis["highlightAnalyses"]
        }
        if actual_frame_refs != expected_frame_refs:
            failures.append(
                f"materialAnalyses[{material_id}].highlightAnalyses must cover every "
                "available highlight frame"
            )
        for index, highlight in enumerate(analysis["highlightAnalyses"]):
            if len(highlight["visualObservation"]) < minimums["visualObservation"]:
                failures.append(
                    f"materialAnalyses[{material_id}].highlightAnalyses[{index}]."
                    "visualObservation must contain concrete visual detail"
                )
            if len(highlight["commercialInterpretation"]) < minimums[
                "commercialInterpretation"
            ]:
                failures.append(
                    f"materialAnalyses[{material_id}].highlightAnalyses[{index}]."
                    "commercialInterpretation must contain at least "
                    f"{minimums['commercialInterpretation']} characters"
                )
        duplicate_frames = _duplicate_highlight_observations(
            material_id, analysis["highlightAnalyses"]
        )
        if duplicate_frames:
            failures.append(
                f"materialAnalyses[{material_id}].highlightAnalyses has repeated "
                "visualObservation for frames "
                + ", ".join(f"{first}/{second}" for first, second in duplicate_frames)
            )
        duplicate_interpretations = _duplicate_highlight_interpretations(
            material_id, analysis["highlightAnalyses"]
        )
        if duplicate_interpretations:
            failures.append(
                f"materialAnalyses[{material_id}].highlightAnalyses has repeated "
                "commercialInterpretation for frames "
                + ", ".join(
                    f"{first}/{second}"
                    for first, second in duplicate_interpretations
                )
            )

    for field_name in (
        "audienceConclusion",
        "scriptConclusion",
        "integratedConclusion",
        "nextAction",
    ):
        repeated = _repeated_values(successful_analyses, field_name)
        if repeated:
            failures.append(
                f"materialAnalyses.{field_name} has repeated values for "
                + ", ".join("/".join(ids) for ids in repeated)
            )

    if failures:
        raise FinalReportError("AI quality gate: " + "; ".join(failures))


def _validate_standard_ai_quality(conclusion, material_cards):
    if not conclusion or conclusion.get("schemaVersion") not in {4, 5}:
        raise FinalReportError(
            "AI quality gate requires schemaVersion 4 or 5 with highlightDashboard"
        )
    _validate_v2_analysis_quality(conclusion, material_cards)


def _prepare_report_data(
    top_materials_json,
    peak_output_dir,
    diagnosis_records_dir,
    analysis_json,
):
    top_payload = _read_json(top_materials_json, "top materials JSON")
    # Chapter 01 facts are the authoritative full material-list source.  The
    # RPA-ready payload intentionally contains only the selected assets, so
    # when an analysis-unit directory is available, inject its portfolio facts
    # before building the report projection.
    if analysis_json:
        chapter01_facts_path = Path(analysis_json).parent / "analysis-units" / "chapter01-portfolio-input.json"
        if chapter01_facts_path.is_file():
            chapter01_payload = _read_json(chapter01_facts_path, "Chapter 01 facts")
            portfolio = chapter01_payload.get("portfolio") if isinstance(chapter01_payload, dict) else None
            if isinstance(portfolio, dict):
                top_payload["portfolioFacts"] = portfolio
    materials = _selected_materials(top_payload)
    records_by_material, record_warnings = load_records_for_materials(
        materials, diagnosis_records_dir
    )
    summaries_by_material = load_summaries_for_materials(materials, peak_output_dir)
    batch_verification = load_batch_verification(peak_output_dir)
    batch_manifest = load_batch_manifest(peak_output_dir)
    top_material_roi = load_top_material_roi_facts(peak_output_dir)
    conclusion = load_conclusion_analysis(analysis_json) if analysis_json else None
    return (
        build_final_report_data(
            top_payload,
            records_by_material,
            summaries_by_material,
            conclusion=conclusion,
            batch_verification=batch_verification,
            batch_manifest=batch_manifest,
            input_warnings=record_warnings,
            top_material_roi=top_material_roi,
        ),
        conclusion,
    )


def validate_standard_ai_analysis(
    top_materials_json,
    peak_output_dir,
    diagnosis_records_dir,
    analysis_json,
):
    """Validate the AI analysis against the same inputs used for rendering."""
    data, conclusion = _prepare_report_data(
        top_materials_json,
        peak_output_dir,
        diagnosis_records_dir,
        analysis_json,
    )
    _validate_standard_ai_quality(conclusion, data["materials"])
    overview = data["overview"]
    return {
        "analysisSchemaVersion": conclusion["schemaVersion"],
        "materialCount": overview["materialCount"],
        "highlightFrameCount": overview["highlightFrameCount"],
    }


def build_final_report_data(
    top_payload,
    records_by_material,
    summaries_by_material,
    conclusion=None,
    batch_verification=None,
    batch_manifest=None,
    input_warnings=None,
    top_material_roi=None,
):
    materials = _selected_materials(top_payload)
    diagnostic = build_diagnostic_analysis(top_payload, records_by_material)
    top_material_roi_data = _top_material_roi_data(
        top_payload, materials, top_material_roi
    )
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
    display_facts = build_display_facts(diagnostic)
    display_facts["materials"] = [
        {
            key: value
            for key, value in card.items()
            if key != "analysis"
        }
        for card in material_cards
    ]
    display_facts["topMaterialRoi"] = top_material_roi_data
    _validate_v2_evidence(
        conclusion,
        diagnostic,
        summaries_by_material,
        material_cards,
        top_material_roi_data,
    )
    highlight_frame_count = sum(
        item["highlightFrameCount"] for item in material_cards
    )
    successful_material_count = sum(
        1 for item in material_cards if item["extractionStatus"] == "succeeded"
    )
    diagnostic_warnings = diagnostic.get("warnings", [])
    input_warnings = list(input_warnings or [])
    report_warnings = (
        _warnings(material_cards, conclusion, batch_verification)
        + input_warnings
        + diagnostic_warnings
    )
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
        "runId": batch_manifest.get("runId") if isinstance(batch_manifest, dict) else None,
        "status": _status(
            conclusion,
            material_cards,
            batch_verification,
            input_warnings=input_warnings + diagnostic_warnings,
        ),
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
        "topMaterialRoi": top_material_roi_data,
        "displayFacts": display_facts,
        "portfolio": _portfolio_summary(diagnostic.get("portfolio", {})),
        "materials": material_cards,
        "defaultMaterialId": default_material_id,
        "pipelineSummary": _pipeline_summary(batch_manifest, batch_verification),
        "batchInsights": insights,
        "audience": diagnostic["audience"],
        "scripts": diagnostic["scripts"],
        "interaction": diagnostic["interaction"],
        "delivery": diagnostic["delivery"],
        "conclusion": conclusion
        or {
            "overallConclusion": "",
            "keyInsights": [],
            "materialRecommendations": [],
            "warnings": [],
        },
        "batchVerification": batch_verification or {},
        "warnings": report_warnings,
    }


def _safe_asset_filename(value):
    from report_renderer import safe_asset_filename

    return safe_asset_filename(value)


def load_records_for_materials(materials, records_dir):
    records_by_material = {}
    warnings = []
    for material in materials:
        material_id = material["materialId"]
        records, warning = load_retained_diagnosis_records(records_dir, material_id)
        if warning:
            warnings.append(warning)
        records_by_material[material_id] = records
    return records_by_material, warnings


def load_summaries_for_materials(materials, peak_output_dir):
    result = {}
    root = Path(peak_output_dir)
    for material in materials:
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
            frame_name = str(frame.get("framePath") or "").strip()
            frames.append(
                {
                    "rankType": str(frame.get("rankType") or "").strip(),
                    "durationSec": frame.get("durationSec"),
                    "metricValue": frame.get("metricValue"),
                    "framePath": str(material_dir / frame_name) if frame_name else "",
                }
            )
        summary_warnings = summary.get("warnings", [])
        missing_frames = summary.get("missingFrames", [])
        result[material_id] = {
            "summaryPath": str(summary_path),
            "frames": frames,
            "warnings": summary_warnings if isinstance(summary_warnings, list) else [],
            "missingFrames": missing_frames if isinstance(missing_frames, list) else [],
        }
    return result


def _load_report_artifact(path):
    source = Path(path)
    if not source.is_file():
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_batch_verification(peak_output_dir):
    return _load_report_artifact(Path(peak_output_dir) / "batch-verification.json")


def load_batch_manifest(peak_output_dir):
    return _load_report_artifact(Path(peak_output_dir) / "batch-manifest.json")


def load_top_material_roi_facts(peak_output_dir):
    return _load_report_artifact(
        Path(peak_output_dir) / "top-material-roi-facts-v1.json"
    )


def _atomic_write_text(path, content):
    return atomic_write_text(path, content)


def _copy_html_asset(source_path, asset_base_dir, material_id):
    from report_renderer import copy_html_asset

    return copy_html_asset(source_path, asset_base_dir, material_id)


def _html_asset_src(
    path,
    asset_base_dir,
    embed_assets=False,
    copy_assets=False,
    material_id="",
):
    from report_renderer import html_asset_src

    return html_asset_src(
        path,
        asset_base_dir,
        embed_assets=embed_assets,
        copy_assets=copy_assets,
        material_id=material_id,
    )


def _load_report_template():
    from report_renderer import _load_report_template

    return _load_report_template()


def _json_for_html_script(data):
    from report_renderer import json_for_html_script

    return json_for_html_script(data)


def _report_data_for_template(
    data,
    asset_base_dir,
    embed_assets=False,
    copy_assets=False,
):
    from report_renderer import report_data_for_template

    return report_data_for_template(
        data,
        asset_base_dir,
        embed_assets=embed_assets,
        copy_assets=copy_assets,
        contains_execution_process_term=_contains_execution_process_term,
    )


def _render_html_report(
    data,
    asset_base_dir=None,
    embed_assets=False,
    copy_assets=False,
):
    return render_html_report(
        data,
        asset_base_dir,
        embed_assets=embed_assets,
        copy_assets=copy_assets,
        contains_execution_process_term=_contains_execution_process_term,
    )


def generate_final_report(
    top_materials_json,
    peak_output_dir,
    diagnosis_records_dir,
    output_dir,
    analysis_json=None,
    embed_assets=False,
    copy_assets=False,
    require_ai_quality=False,
):
    legacy_data, conclusion = _prepare_report_data(
        top_materials_json,
        peak_output_dir,
        diagnosis_records_dir,
        analysis_json,
    )
    if require_ai_quality:
        _validate_standard_ai_quality(conclusion, legacy_data["materials"])
    output_root = Path(output_dir)
    data_path = output_root / "final-report-data.json"
    display_facts_path = output_root / "report-display-facts.json"
    presentation_path = output_root / "presentation-report.json"
    html_path = output_root / "final-report.html"
    from qianchuan_presentation import build_presentation_report

    chapter05_facts = {}
    if analysis_json:
        chapter05_path = Path(analysis_json).parent / "analysis-units" / "chapter05-material-input.json"
        if chapter05_path.is_file():
            chapter05_facts = _read_json(chapter05_path, "Chapter 05 material facts")
    presentation = build_presentation_report(
        {**legacy_data, "chapter05Facts": chapter05_facts}
    )
    data = {"presentation": presentation, **legacy_data}
    export_path = export_highlight_workbook(
        legacy_data, presentation, output_root / "highlight-frames.xlsx"
    )
    if export_path is not None:
        data["highlightExportHref"] = export_path.name
    _atomic_write_text(
        data_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(
        display_facts_path,
        json.dumps(data.get("displayFacts", {}), ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(
        presentation_path, json.dumps(presentation, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(
        html_path,
        _render_html_report(
            data,
            output_root,
            embed_assets=embed_assets,
            copy_assets=copy_assets,
        ),
    )
    return data_path, html_path, data
