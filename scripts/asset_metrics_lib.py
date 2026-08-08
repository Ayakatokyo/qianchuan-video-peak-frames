"""Deterministic asset-table metrics used by the Top-material ROI facts."""

import json
import re
from decimal import Decimal, InvalidOperation


COST_METRIC = "stat_cost_for_overall_roi2"
NET_SETTLEMENT_AMOUNT_METRIC = "total_order_settle_amount_for_roi2_1h"
OVERALL_PAYMENT_AMOUNT_METRIC = "total_pay_order_gmv_include_coupon_for_roi2"
REQUIRED_METRICS = (
    COST_METRIC,
    NET_SETTLEMENT_AMOUNT_METRIC,
    OVERALL_PAYMENT_AMOUNT_METRIC,
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
