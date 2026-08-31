"""Strict projection of full-portfolio facts for Chapter 01 consumers."""

from copy import deepcopy


PORTFOLIO_SUMMARY_KEYS = (
    "schemaVersion",
    "period",
    "newMaterialCount7d",
    "newMaterialStartDate",
    "newMaterialEndDate",
    "materialCount",
)
SUMMARY_METRIC_KEYS = (
    "cost",
    "roi",
    "ctr",
    "conversionRate",
    "threeSecondCompletionRate",
    "cpm",
)
HOT_MATERIAL_RULE_KEYS = ("thresholdCost", "comparison", "qualifyingCount")
HOT_MATERIAL_KEYS = ("materialId", "materialName", "totalCost", "evidenceRef")
HOT_MATERIAL_METRIC_KEYS = (
    "roi",
    "ctr",
    "conversionRate",
    "threeSecondCompletionRate",
    "cpm",
)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return str(value).strip() if value is not None else ""


def project_portfolio_summary(value):
    """Return only the schema fields allowed in the Chapter 01 portfolio view."""

    raw = _mapping(value)
    summary_metrics = _mapping(raw.get("summaryMetrics"))
    if not summary_metrics:
        return {}

    projected = {
        key: deepcopy(raw[key])
        for key in PORTFOLIO_SUMMARY_KEYS
        if key in raw
    }
    projected["summaryMetrics"] = {
        key: deepcopy(summary_metrics[key])
        for key in SUMMARY_METRIC_KEYS
        if key in summary_metrics
    }

    rule = raw.get("hotMaterialRule")
    if isinstance(rule, dict):
        projected_rule = {
            key: deepcopy(rule[key])
            for key in HOT_MATERIAL_RULE_KEYS
            if key in rule
        }
        materials = []
        for item in rule.get("qualifyingMaterials", []):
            if not isinstance(item, dict) or not _text(item.get("materialId")):
                continue
            projected_item = {
                key: deepcopy(item[key])
                for key in HOT_MATERIAL_KEYS
                if key in item
            }
            metrics = item.get("metrics")
            if isinstance(metrics, dict):
                projected_item["metrics"] = {
                    key: deepcopy(metrics[key])
                    for key in HOT_MATERIAL_METRIC_KEYS
                    if key in metrics
                }
            materials.append(projected_item)
        projected_rule["qualifyingMaterials"] = materials
        projected["hotMaterialRule"] = projected_rule

    return projected
