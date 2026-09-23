"""Deterministic asset-table metrics used by the Top-material ROI facts."""

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


COST_METRIC = "stat_cost_for_overall_roi2"
NET_SETTLEMENT_AMOUNT_METRIC = "total_order_settle_amount_for_roi2_1h"
OVERALL_PAYMENT_AMOUNT_METRIC = "total_pay_order_gmv_include_coupon_for_roi2"
REQUIRED_METRICS = (
    COST_METRIC,
    NET_SETTLEMENT_AMOUNT_METRIC,
    OVERALL_PAYMENT_AMOUNT_METRIC,
)

PORTFOLIO_METRIC_KEYS = {
    "roi": "total_prepay_and_pay_order_roi2",
    "ctr": "additional_delivery_ctr_for_roi2_assist",
    "conversionRate": "additional_delivery_convert_rate_for_roi2_assist",
    "threeSecondCompletionRate": "video_play_duration_3s_rate_for_roi2",
    "cpm": "total_ecpm_for_roi2",
}
DEFAULT_HOT_MATERIAL_RULES_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "hot-material-rules.json"
)


class AssetMetricsError(ValueError):
    """Raised when asset-table source data cannot support deterministic facts."""


class AssetMetricsAggregator:
    """Aggregate required atomic metrics by material across asset-table rows."""

    def __init__(self, name_pattern=None, material_types=()):
        self._name_pattern = _normalise_name_pattern(name_pattern)
        self._material_types = _normalise_material_types(material_types)
        self._materials = {}

    def add_raw_origin_data(self, raw_row):
        row = _load_origin_data(raw_row)
        material_id, name, material_type = _parse_material_scope(row)
        if self._name_pattern is not None and self._name_pattern.search(name) is None:
            return
        if self._material_types and material_type not in self._material_types:
            return
        metrics = _parse_metrics(row)
        aggregate = self._materials.setdefault(
            material_id, {metric: Decimal("0") for metric in REQUIRED_METRICS}
        )
        for metric in REQUIRED_METRICS:
            aggregate[metric] += metrics[metric]

    def facts(self):
        return {
            material_id: dict(metrics)
            for material_id, metrics in sorted(self._materials.items())
        }


def aggregate_asset_metrics(raw_rows, name_pattern=None, material_types=()):
    """Return per-material atomic metric totals from source ``origin_data`` rows."""
    aggregator = AssetMetricsAggregator(name_pattern, material_types)
    for raw_row in raw_rows:
        aggregator.add_raw_origin_data(raw_row)
    return aggregator.facts()


def build_asset_facts_payload(
    actual_start_date,
    actual_end_date,
    name_pattern,
    material_types,
    advertiser_id,
    materials,
):
    """Create the versioned, JSON-serializable asset metric facts artifact."""
    pattern = _normalise_name_pattern(name_pattern)
    types = _normalise_material_types(material_types)
    return {
        "schemaVersion": 1,
        "actualStartDate": actual_start_date,
        "actualEndDate": actual_end_date,
        "filters": {
            "materialNameRegex": None if pattern is None else pattern.pattern,
            "materialTypes": list(types),
            "advertiserId": None if advertiser_id is None else str(advertiser_id),
        },
        "metricKeys": {
            "cost": COST_METRIC,
            "netSettlementAmount": NET_SETTLEMENT_AMOUNT_METRIC,
            "overallPaymentAmount": OVERALL_PAYMENT_AMOUNT_METRIC,
        },
        "materials": {
            material_id: {
                metric: _decimal_text(metrics[metric]) for metric in REQUIRED_METRICS
            }
            for material_id, metrics in sorted(materials.items())
        },
    }


def build_portfolio_facts(
    raw_rows,
    *,
    execution_date,
    start_date,
    end_date,
    name_pattern=None,
    material_types=(),
    advertiser_id=None,
    hot_material_rules_path=None,
):
    """Build deterministic material-list facts from API origin rows.

    The new-material window follows the T+1 convention: the seven calendar
    dates from execution_date - 7 days through execution_date - 1 day.
    """
    execution = _parse_iso_date(execution_date, "execution_date")
    start = _parse_iso_date(start_date, "start_date")
    end = _parse_iso_date(end_date, "end_date")
    if start > end:
        raise AssetMetricsError("start_date must not be after end_date")
    window_start = execution - timedelta(days=7)
    window_end = execution - timedelta(days=1)
    pattern = _normalise_name_pattern(name_pattern)
    types = _normalise_material_types(material_types)
    materials = {}
    new_material_ids = set()
    for raw_row in raw_rows:
        row = _load_origin_data(raw_row) if isinstance(raw_row, str) else raw_row
        if not isinstance(row, dict):
            continue
        try:
            material_id, name, material_type = _parse_material_scope(row)
            cost = _normalise_metric(
                _nested_value(row, "metrics", COST_METRIC, "Value"), COST_METRIC
            )
        except AssetMetricsError:
            # API pages can contain incomplete placeholder rows; they cannot
            # contribute deterministic portfolio facts and are ignored.
            continue
        if pattern is not None and pattern.search(name) is None:
            continue
        if types and material_type not in types:
            continue
        if material_id == "-2":
            continue
        created_at = _creation_time(row)
        aggregate = materials.setdefault(
            material_id,
            {
                "materialId": material_id,
                "materialName": name,
                "materialType": material_type,
                "totalCost": Decimal("0"),
                "createdAt": created_at,
                "metricValues": {key: [] for key in PORTFOLIO_METRIC_KEYS},
            },
        )
        aggregate["totalCost"] += cost
        if aggregate.get("createdAt") is None and created_at is not None:
            aggregate["createdAt"] = created_at
        for key, source_metric in PORTFOLIO_METRIC_KEYS.items():
            value = _optional_metric(row, source_metric)
            if value is not None:
                aggregate["metricValues"][key].append((cost, value))
        if created_at is not None and window_start <= created_at.date() <= window_end:
            new_material_ids.add(material_id)

    material_payload = {}
    for material_id, aggregate in sorted(materials.items()):
        metrics = {
            key: _decimal_text(_weighted_average(values)) if values else None
            for key, values in aggregate["metricValues"].items()
        }
        material_payload[material_id] = {
            "materialId": material_id,
            "materialName": aggregate["materialName"],
            "materialType": aggregate["materialType"],
            "createdAt": aggregate["createdAt"].isoformat(sep=" ")
            if aggregate["createdAt"] is not None else None,
            "totalCost": _decimal_text(aggregate["totalCost"]),
            "metrics": metrics,
        }

    summary = {
        "cost": _decimal_text(
            sum((Decimal(item["totalCost"]) for item in material_payload.values()), Decimal("0"))
        ),
    }
    total_cost = Decimal(summary["cost"])
    for key in PORTFOLIO_METRIC_KEYS:
        weighted_values = [
            (Decimal(item["totalCost"]), Decimal(item["metrics"][key]))
            for item in material_payload.values()
            if item["metrics"][key] is not None
        ]
        summary[key] = _decimal_text(_weighted_average(weighted_values)) if weighted_values else None

    hot_material_rule = _build_hot_material_rule(
        material_payload,
        advertiser_id=advertiser_id,
        rules_path=hot_material_rules_path,
    )

    result = {
        "schemaVersion": 1,
        "period": {"actualStartDate": start.isoformat(), "actualEndDate": end.isoformat()},
        "newMaterialCount7d": len(new_material_ids),
        "newMaterialStartDate": window_start.isoformat(),
        "newMaterialEndDate": window_end.isoformat(),
        "materialCount": len(material_payload),
        "summaryMetrics": summary,
        "materials": material_payload,
    }
    if hot_material_rule is not None:
        result["hotMaterialRule"] = hot_material_rule
    return result


def load_hot_material_rule(advertiser_id, rules_path=None):
    """Load and validate the account-scoped deterministic hot-material rule."""
    account_id = _normalise_advertiser_id(advertiser_id)
    if account_id is None:
        return None
    source = Path(rules_path) if rules_path is not None else DEFAULT_HOT_MATERIAL_RULES_PATH
    try:
        raw_rules = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssetMetricsError(f"unable to load hot material rule: {source}") from exc
    if not isinstance(raw_rules, dict) or account_id not in raw_rules:
        raise AssetMetricsError(f"missing hot material rule for advertiser {account_id}")
    raw_rule = raw_rules[account_id]
    if not isinstance(raw_rule, dict) or raw_rule.get("comparison") != "gte":
        raise AssetMetricsError(f"invalid hot material rule for advertiser {account_id}")
    threshold = _normalise_metric(raw_rule.get("thresholdCost"), "hot material thresholdCost")
    return {"thresholdCost": _decimal_text(threshold), "comparison": "gte"}


def _build_hot_material_rule(material_payload, *, advertiser_id, rules_path):
    if advertiser_id is None:
        rule = None
    else:
        try:
            rule = load_hot_material_rule(advertiser_id, rules_path)
        except AssetMetricsError as exc:
            # Accounts without a configured business rule retain the legacy
            # export contract; malformed rule files still fail loudly.
            if not str(exc).startswith("missing hot material rule for advertiser "):
                raise
            rule = None
    if rule is None:
        return None
    threshold = Decimal(rule["thresholdCost"])
    qualifying_materials = []
    for material_id, material in material_payload.items():
        if Decimal(material["totalCost"]) < threshold:
            continue
        qualifying_materials.append({
            "materialId": material_id,
            "materialName": material["materialName"],
            "totalCost": material["totalCost"],
            "metrics": dict(material.get("metrics", {})),
            "evidenceRef": f"commercial:hotMaterial:{material_id}",
        })
    qualifying_materials.sort(
        key=lambda item: (-Decimal(item["totalCost"]), item["materialId"])
    )
    return {
        **rule,
        "qualifyingCount": len(qualifying_materials),
        "qualifyingMaterials": qualifying_materials,
    }


def _normalise_advertiser_id(value):
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise AssetMetricsError("advertiser_id must be a nonempty scalar")
    result = str(value).strip()
    if not result:
        raise AssetMetricsError("advertiser_id must be a nonempty scalar")
    return result


def _parse_iso_date(value, field):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AssetMetricsError("%s must use YYYY-MM-DD" % field) from exc


def _creation_time(row):
    raw = row.get("dimensions", {}).get("material_create_time_v2", {})
    value = raw.get("ValueStr") if isinstance(raw, dict) else raw
    try:
        return datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _optional_metric(row, metric):
    try:
        value = _nested_value(row, "metrics", metric, "Value")
    except AssetMetricsError:
        return None
    try:
        return _normalise_metric(value, metric)
    except AssetMetricsError:
        return None


def _weighted_average(values):
    if not values:
        return Decimal("0")
    total_weight = sum((weight for weight, _value in values), Decimal("0"))
    if total_weight:
        return sum((weight * value for weight, value in values), Decimal("0")) / total_weight
    return sum((value for _weight, value in values), Decimal("0")) / len(values)


def _parse_origin_data(raw_row):
    row = _load_origin_data(raw_row)
    material_id, name, material_type = _parse_material_scope(row)
    return material_id, name, material_type, _parse_metrics(row)


def _load_origin_data(raw_row):
    if not isinstance(raw_row, str):
        raise AssetMetricsError("origin_data must be a JSON string")
    try:
        row = json.loads(
            raw_row,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise AssetMetricsError("origin_data is not valid JSON") from error
    if not isinstance(row, dict):
        raise AssetMetricsError("origin_data must be a JSON object")
    return row


def _parse_material_scope(row):
    material_id = _normalise_material_id(
        _nested_value(row, "dimensions", "material_id", "Value")
    )
    name = _normalise_text(
        _nested_value(row, "dimensions", "roi2_material_video_name", "ValueStr"),
        "material name",
    )
    material_type = _normalise_text(
        _nested_value(row, "dimensions", "roi2_material_type_v3", "ValueStr"),
        "material type",
    )
    return material_id, name, material_type


def _parse_metrics(row):
    return {
        metric: _normalise_metric(
            _nested_value(row, "metrics", metric, "Value"), metric
        )
        for metric in REQUIRED_METRICS
    }


def _nested_value(row, *path):
    current = row
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise AssetMetricsError("missing origin_data path: %s" % ".".join(path))
        current = current[key]
    return current


def _normalise_material_id(value):
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise AssetMetricsError("material ID must be nonempty")
    material_id = str(value).strip()
    if not material_id:
        raise AssetMetricsError("material ID must be nonempty")
    return material_id


def _normalise_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise AssetMetricsError("%s must be a nonempty string" % label)
    return value.strip()


def _normalise_metric(value, metric):
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise AssetMetricsError("%s must be a finite non-negative decimal" % metric)
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise AssetMetricsError(
            "%s must be a finite non-negative decimal" % metric
        ) from error
    if not result.is_finite() or result < 0:
        raise AssetMetricsError("%s must be a finite non-negative decimal" % metric)
    return result


def _normalise_name_pattern(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return re.compile(value)
        except re.error as error:
            raise AssetMetricsError("material name regex is invalid") from error
    if callable(getattr(value, "search", None)) and isinstance(
        getattr(value, "pattern", None), str
    ):
        return value
    raise AssetMetricsError("material name regex must be compiled or a string")


def _normalise_material_types(value):
    if value is None:
        return ()
    try:
        result = tuple(value)
    except TypeError as error:
        raise AssetMetricsError("material types must be iterable") from error
    if any(not isinstance(item, str) for item in result):
        raise AssetMetricsError("material types must be strings")
    return result


def _decimal_text(value):
    if not isinstance(value, Decimal):
        raise AssetMetricsError("asset facts must contain Decimal totals")
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _reject_json_constant(value):
    raise ValueError("invalid JSON constant: %s" % value)
