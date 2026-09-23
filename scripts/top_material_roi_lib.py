"""Build deterministic ROI facts for RPA-verified Top materials only."""

import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tempfile

from asset_metrics_lib import (
    COST_METRIC,
    NET_SETTLEMENT_AMOUNT_METRIC,
    OVERALL_PAYMENT_AMOUNT_METRIC,
)


class TopMaterialRoiError(ValueError):
    """Raised when immutable asset facts and ready Top materials do not agree."""


METRIC_DEFINITIONS = (
    (
        "netSettlementRoi",
        "净成交 ROI",
        "netSettlementAmount",
        NET_SETTLEMENT_AMOUNT_METRIC,
    ),
    (
        "overallPaymentRoi",
        "整体支付 ROI",
        "overallPaymentAmount",
        OVERALL_PAYMENT_AMOUNT_METRIC,
    ),
)


def build_top_material_roi_payload(asset_facts, ready_materials):
    """Aggregate atomic asset facts for the exact final RPA-ready material IDs."""
    asset = _validate_asset_facts(asset_facts)
    material_ids = _ready_material_ids(ready_materials)
    materials = asset["materials"]
    totals = {
        "cost": Decimal("0"),
        "netSettlementAmount": Decimal("0"),
        "overallPaymentAmount": Decimal("0"),
    }
    for material_id in material_ids:
        raw = materials.get(material_id)
        if not isinstance(raw, dict):
            raise TopMaterialRoiError("missing asset facts for ready material: %s" % material_id)
        totals["cost"] += _decimal_metric(raw, COST_METRIC, material_id)
        totals["netSettlementAmount"] += _decimal_metric(
            raw, NET_SETTLEMENT_AMOUNT_METRIC, material_id
        )
        totals["overallPaymentAmount"] += _decimal_metric(
            raw, OVERALL_PAYMENT_AMOUNT_METRIC, material_id
        )
    return {
        "schemaVersion": 1,
        "actualStartDate": asset["actualStartDate"],
        "actualEndDate": asset["actualEndDate"],
        "filters": asset.get("filters", {}),
        "materialIds": material_ids,
        "metricKeys": asset["metricKeys"],
        "totals": {key: _decimal_text(value) for key, value in totals.items()},
        "metrics": [_roi_metric(*definition, totals) for definition in METRIC_DEFINITIONS],
    }


def write_top_material_roi_facts(asset_facts_path, ready_materials_path, output_path):
    """Materialize final Top-material ROI facts after RPA verification completes."""
    asset_facts = _load_json(asset_facts_path, "asset facts")
    ready_materials = _load_json(ready_materials_path, "ready materials")
    payload = build_top_material_roi_payload(asset_facts, ready_materials)
    _write_atomic_json(output_path, payload)
    return payload


def _roi_metric(key, label, total_key, numerator_metric, totals):
    cost = totals["cost"]
    return {
        "key": key,
        "label": label,
        "formula": "sum(%s) / sum(%s)" % (numerator_metric, COST_METRIC),
        "value": None if cost == 0 else _decimal_text(totals[total_key] / cost),
        "available": cost != 0,
    }


def _validate_asset_facts(value):
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise TopMaterialRoiError("asset facts schemaVersion must be 1")
    if not isinstance(value.get("materials"), dict):
        raise TopMaterialRoiError("asset facts materials must be an object")
    metric_keys = value.get("metricKeys")
    expected = {
        "cost": COST_METRIC,
        "netSettlementAmount": NET_SETTLEMENT_AMOUNT_METRIC,
        "overallPaymentAmount": OVERALL_PAYMENT_AMOUNT_METRIC,
    }
    if metric_keys != expected:
        raise TopMaterialRoiError("asset facts metric keys do not match the confirmed mapping")
    for field in ("actualStartDate", "actualEndDate"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise TopMaterialRoiError("asset facts %s is required" % field)
    return value


def _ready_material_ids(value):
    materials = value.get("materials") if isinstance(value, dict) else None
    if not isinstance(materials, list) or not materials:
        raise TopMaterialRoiError("ready materials must contain a nonempty materials list")
    material_ids = [
        str(material.get("materialId") or "").strip()
        for material in materials if isinstance(material, dict)
    ]
    if len(material_ids) != len(materials) or any(not item for item in material_ids):
        raise TopMaterialRoiError("ready materials must provide material IDs")
    if len(material_ids) != len(set(material_ids)):
        raise TopMaterialRoiError("ready materials must not repeat material IDs")
    return material_ids


def _decimal_metric(raw, metric, material_id):
    value = raw.get(metric)
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise TopMaterialRoiError("invalid %s facts for material %s" % (metric, material_id))
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise TopMaterialRoiError(
            "invalid %s facts for material %s" % (metric, material_id)
        ) from error
    if not result.is_finite() or result < 0:
        raise TopMaterialRoiError("invalid %s facts for material %s" % (metric, material_id))
    return result


def _decimal_text(value):
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _load_json(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TopMaterialRoiError("unable to read %s" % label) from error
    return value


def _write_atomic_json(output_path, payload):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".%s." % output.name, suffix=".tmp", dir=str(output.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary_path, output)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
