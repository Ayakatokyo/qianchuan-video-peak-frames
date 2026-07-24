import ast
import json
import math
import os
from pathlib import Path
import uuid


SCHEMA_VERSION = 1
AUDIENCE_METRICS = ("SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT")
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "prompts"
    / "diagnostic-analysis-prompt-v1.md"
)


class DiagnosticAnalysisError(RuntimeError):
    """Raised when retained diagnostic inputs cannot be processed."""


def _text(value):
    return str(value).strip() if value is not None else ""


def _finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path, purpose):
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticAnalysisError(f"unable to read {purpose}: {source}") from exc


def _read_text(path, purpose):
    source = Path(path)
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DiagnosticAnalysisError(f"unable to read {purpose}: {source}") from exc


def _period(top_payload):
    return {
        "actualStartDate": _text(top_payload.get("actualStartDate")),
        "actualEndDate": _text(top_payload.get("actualEndDate")),
    }


def _selected_materials(top_payload):
    if not isinstance(top_payload, dict):
        raise DiagnosticAnalysisError("top materials JSON must be an object")
    raw_materials = top_payload.get("materials")
    if not isinstance(raw_materials, list) or not raw_materials:
        raise DiagnosticAnalysisError(
            "top materials JSON must contain a nonempty materials list"
        )

    materials = []
    warnings = []
    seen_ids = set()
    for index, raw_material in enumerate(raw_materials, start=1):
        if not isinstance(raw_material, dict):
            warnings.append(f"top materials entry {index} is not an object")
            continue
        material_id = _text(raw_material.get("materialId"))
        if not material_id:
            warnings.append(f"top materials entry {index} is missing materialId")
            continue
        if material_id in seen_ids:
            warnings.append(f"top materials repeats material {material_id}")
            continue
        seen_ids.add(material_id)
        material = {
            "rank": raw_material.get("rank"),
            "materialId": material_id,
            "materialName": _text(raw_material.get("materialName")),
            "materialType": _text(raw_material.get("materialType")),
            "videoType": _text(raw_material.get("videoType")),
        }
        total_cost = _finite_number(raw_material.get("totalCost"))
        if total_cost is not None:
            material["totalCost"] = total_cost
        materials.append(material)
    if not materials:
        raise DiagnosticAnalysisError("top materials JSON contains no usable materials")
    return materials, warnings


def _nested_dimension_value(record, dimension_key):
    dimensions = record.get("Dimensions")
    if not isinstance(dimensions, dict):
        return ""
    candidates = []
    if dimension_key in dimensions:
        candidates.append(dimensions[dimension_key])
    if len(dimensions) == 1:
        candidates.extend(dimensions.values())
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in ("ValueStr", "valueStr", "Value", "value", "Name", "name"):
                value = _text(candidate.get(key))
                if value:
                    return value
        else:
            value = _text(candidate)
            if value:
                return value
    return ""


def _bucket_label(record, dimension_key):
    for key in ("dimensionValue", "dimensionValueName", "bucket", "bucketName"):
        value = _text(record.get(key))
        if value:
            return value
    return _nested_dimension_value(record, dimension_key)


def _crowd_metric_value(record):
    direct_value = _finite_number(record.get("metricValue"))
    if direct_value is not None:
        return direct_value
    metrics = record.get("Metrics")
    if not isinstance(metrics, dict):
        return None
    values = []
    for metric in metrics.values():
        if not isinstance(metric, dict):
            continue
        value = _finite_number(metric.get("Value"))
        if value is not None:
            values.append(value)
    return values[0] if len(values) == 1 else None


def _aggregate_audience_metric(records_by_material, selected_ids, metric):
    dimensions = {}
    missing_material_ids = []
    for material_id in selected_ids:
        usable_record_found = False
        material_records = records_by_material.get(material_id, [])
        if not isinstance(material_records, list):
            material_records = []
        for record in material_records:
            if not isinstance(record, dict):
                continue
            if record.get("recordType") != "crowd_analysis":
                continue
            if record.get("displayMetric") != metric:
                continue
            record_material_id = _text(record.get("materialId"))
            if record_material_id and record_material_id != material_id:
                continue
            dimension_key = _text(record.get("dimensionKey"))
            bucket = _bucket_label(record, dimension_key)
            value = _crowd_metric_value(record)
            if not dimension_key or not bucket or value is None or value < 0:
                continue
            usable_record_found = True
            dimension = dimensions.setdefault(
                dimension_key,
                {
                    "dimensionKey": dimension_key,
                    "dimensionName": _text(record.get("dimensionName")) or dimension_key,
                    "sourceMaterialIds": set(),
                    "buckets": {},
                },
            )
            dimension["sourceMaterialIds"].add(material_id)
            bucket_data = dimension["buckets"].setdefault(
                bucket, {"metricValue": 0.0, "sourceMaterialIds": set()}
            )
            bucket_data["metricValue"] += value
            bucket_data["sourceMaterialIds"].add(material_id)
        if not usable_record_found:
            missing_material_ids.append(material_id)

    normalized_dimensions = {}
    for dimension_key, dimension in dimensions.items():
        total = sum(item["metricValue"] for item in dimension["buckets"].values())
        buckets = []
        for bucket, bucket_data in sorted(
            dimension["buckets"].items(),
            key=lambda item: (-item[1]["metricValue"], item[0]),
        ):
            value = round(bucket_data["metricValue"], 6)
            percentage = round((value / total * 100.0) if total else 0.0, 6)
            buckets.append(
                {
                    "bucket": bucket,
                    "metricValue": value,
                    "metricPercentage": percentage,
                    "sourceMaterialIds": sorted(bucket_data["sourceMaterialIds"]),
                }
            )
        normalized_dimensions[dimension_key] = {
            "dimensionKey": dimension_key,
            "dimensionName": dimension["dimensionName"],
            "totalMetricValue": round(total, 6),
            "sourceMaterialIds": sorted(dimension["sourceMaterialIds"]),
            "buckets": buckets,
        }
    return {"dimensions": normalized_dimensions, "missingMaterialIds": missing_material_ids}


def _parse_script_info(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("scriptInfo must be a nonempty structured string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError) as exc:
            raise ValueError("scriptInfo must be structured data") from exc
    if not isinstance(parsed, dict):
        raise ValueError("scriptInfo must decode to an object")
    return parsed


def _script_labels_and_evidence(script_info):
    labels = []
    evidence = []
    details = script_info.get("detail")
    if not isinstance(details, dict):
        details = {}
    data = script_info.get("data")
    if not isinstance(data, list):
        data = []
    seen_labels = set()
    seen_evidence = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        level1 = entry.get("level_1_label")
        level1_label = _text(level1.get("label")) if isinstance(level1, dict) else ""
        level2_labels = entry.get("level_2_labels")
        if not isinstance(level2_labels, list):
            continue
        for level2 in level2_labels:
            if not isinstance(level2, dict):
                continue
            level2_value = level2.get("value")
            detail = details.get(str(level2_value))
            if detail is None and level2_value in details:
                detail = details[level2_value]
            detail_label = _text(detail.get("label")) if isinstance(detail, dict) else ""
            level2_label = detail_label or _text(level2.get("label"))
            if not level1_label or not level2_label:
                continue
            label_key = (level1_label, level2_label)
            if label_key not in seen_labels:
                seen_labels.add(label_key)
                labels.append({"level1": level1_label, "level2": level2_label})
            detail_sentences = detail.get("list") if isinstance(detail, dict) else []
            if not isinstance(detail_sentences, list):
                continue
            for raw_sentence in detail_sentences:
                sentence = _text(raw_sentence)
                evidence_key = (level2_label, sentence)
                if sentence and evidence_key not in seen_evidence:
                    seen_evidence.add(evidence_key)
                    evidence.append({"level2": level2_label, "sentence": sentence})
    return labels, evidence


def _build_scripts(records_by_material, selected_ids, warnings):
    materials = []
    summaries = {}
    for material_id in selected_ids:
        material_records = records_by_material.get(material_id, [])
        if not isinstance(material_records, list):
            continue
        for record in material_records:
            if not isinstance(record, dict) or record.get("recordType") != "script_analysis":
                continue
            record_material_id = _text(record.get("materialId"))
            if record_material_id and record_material_id != material_id:
                continue
            try:
                script_info = _parse_script_info(record.get("scriptInfo"))
            except ValueError:
                warnings.append(f"invalid scriptInfo for material {material_id}")
                continue
            labels, evidence = _script_labels_and_evidence(script_info)
            fact = {
                "materialId": material_id,
                "materialUri": _text(record.get("materialUri")),
                "text": _text(script_info.get("text")),
                "labels": labels,
                "evidence": evidence,
            }
            materials.append(fact)
            evidence_by_label = {}
            for evidence_item in evidence:
                evidence_by_label.setdefault(evidence_item["level2"], set()).add(
                    evidence_item["sentence"]
                )
            for label in labels:
                key = (label["level1"], label["level2"])
                summary = summaries.setdefault(
                    key,
                    {"materialIds": set(), "evidence": set(), "occurrenceCount": 0},
                )
                summary["occurrenceCount"] += 1
                summary["materialIds"].add(material_id)
                summary["evidence"].update(evidence_by_label.get(label["level2"], set()))
            break
    label_summary = []
    for (level1, level2), summary in sorted(summaries.items()):
        label_summary.append(
            {
                "level1": level1,
                "level2": level2,
                "occurrenceCount": summary["occurrenceCount"],
                "materialIds": sorted(summary["materialIds"]),
                "evidence": sorted(summary["evidence"]),
            }
        )
    return {"materials": materials, "labelSummary": label_summary}


DELIVERY_EXCLUDED_FIELDS = {
    "recordType",
    "materialId",
    "materialUri",
    "materialName",
    "statDate",
    "accountId",
    "actualStartDate",
    "actualEndDate",
    "customStartDate",
    "customEndDate",
    "dateRangeType",
    "bizDate",
    "Dimensions",
    "Metrics",
    "Fields",
    "scriptInfo",
}


def _delivery_value(value):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            return text
        number = _finite_number(text.replace(",", ""))
        return number if number is not None else text
    number = _finite_number(value)
    return number if number is not None else value


def _build_delivery(records_by_material, selected_ids):
    materials = []
    for material_id in selected_ids:
        facts = []
        material_records = records_by_material.get(material_id, [])
        if not isinstance(material_records, list):
            continue
        for record in material_records:
            if not isinstance(record, dict):
                continue
            if record.get("recordType") != "delivery_material_detail":
                continue
            record_material_id = _text(record.get("materialId"))
            if record_material_id and record_material_id != material_id:
                continue
            values = {}
            for key, value in record.items():
                if key in DELIVERY_EXCLUDED_FIELDS or key.lower().endswith("url"):
                    continue
                normalized = _delivery_value(value)
                if normalized is not None:
                    values[key] = normalized
            facts.append({"statDate": _text(record.get("statDate")), "values": values})
        if facts:
            materials.append({"materialId": material_id, "facts": facts})
    return materials


def _load_selected_records(top_payload, records_dir):
    materials, _warnings = _selected_materials(top_payload)
    root = Path(records_dir)
    records_by_material = {}
    warnings = []
    for material in materials:
        material_id = material["materialId"]
        path = root / f"{material_id}.json"
        if not path.is_file():
            warnings.append(f"missing diagnosis records for material {material_id}")
            records_by_material[material_id] = []
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            warnings.append(f"unable to read diagnosis records for material {material_id}")
            records_by_material[material_id] = []
            continue
        if not isinstance(records, list):
            warnings.append(f"diagnosis records for material {material_id} are not a list")
            records_by_material[material_id] = []
            continue
        records_by_material[material_id] = records
    return records_by_material, warnings


def _publish_output_pair(output_dir, analysis, prompt):
    root = Path(output_dir)
    data_path = root / "diagnostic-analysis-data.json"
    prompt_path = root / "diagnostic-analysis-prompt.md"
    data_staging = root / f".diagnostic-analysis-data.{uuid.uuid4().hex}.json.tmp"
    prompt_staging = root / f".diagnostic-analysis-prompt.{uuid.uuid4().hex}.md.tmp"
    try:
        root.mkdir(parents=True, exist_ok=True)
        data_staging.write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        prompt_staging.write_text(prompt, encoding="utf-8")
        os.replace(data_staging, data_path)
        os.replace(prompt_staging, prompt_path)
    except OSError as exc:
        raise DiagnosticAnalysisError(
            f"unable to publish diagnostic analysis output in {root}: {exc}"
        ) from exc
    finally:
        for staging in (data_staging, prompt_staging):
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    return data_path, prompt_path


def build_diagnostic_analysis(top_payload, records_by_material):
    materials, warnings = _selected_materials(top_payload)
    selected_ids = [material["materialId"] for material in materials]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "period": _period(top_payload),
        "materials": materials,
        "audience": {
            "metrics": {
                metric: _aggregate_audience_metric(records_by_material, selected_ids, metric)
                for metric in AUDIENCE_METRICS
            }
        },
        "scripts": _build_scripts(records_by_material, selected_ids, warnings),
        "delivery": {"materials": _build_delivery(records_by_material, selected_ids)},
        "warnings": warnings,
    }


def prepare_diagnostic_analysis(
    top_materials_json,
    diagnosis_records_dir,
    output_dir,
    prompt_template=None,
):
    top_payload = _read_json(top_materials_json, "top materials JSON")
    records_by_material, read_warnings = _load_selected_records(
        top_payload, diagnosis_records_dir
    )
    prompt = _read_text(
        prompt_template if prompt_template else DEFAULT_PROMPT_PATH,
        "diagnostic prompt template",
    )
    analysis = build_diagnostic_analysis(top_payload, records_by_material)
    analysis["warnings"] = read_warnings + analysis["warnings"]
    data_path, prompt_path = _publish_output_pair(output_dir, analysis, prompt)
    return data_path, prompt_path, analysis
