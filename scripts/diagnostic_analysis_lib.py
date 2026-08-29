import ast
import json
import math
import os
from copy import deepcopy
from pathlib import Path
import uuid

from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv
from path_safety import artifact_path, validate_material_id
from analysis_units_lib import _keyword_cloud, publish_analysis_units


SCHEMA_VERSION = 1
AUDIENCE_METRICS = ("SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT")
INTERACTION_METRICS = (
    "CLICK_COUNT",
    "LOSE_COUNT",
    "LIKE_COUNT",
    "COMMENT_COUNT",
    "SHARE_COUNT",
    "NEW_FOLLOWER_COUNT",
)
INTERACTION_BUCKETS = ((0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))
PREFERENCE_METRIC = "SHOW_COUNT"
PREFERENCE_DIMENSION = "user_group_label_name"
PREFERENCE_LIMIT = 3
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "prompts"
    / "diagnostic-analysis-prompt-v1.md"
)


class DiagnosticAnalysisError(RuntimeError):
    """Raised when retained diagnostic inputs cannot be processed."""


def _text(value):
    return str(value).strip() if value is not None else ""


def _safe_component(value):
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    )
    return cleaned.strip("_") or "unknown-material"


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


def load_retained_diagnosis_records(records_dir, material_id):
    root = Path(records_dir)
    material_id = _text(material_id)
    try:
        validate_material_id(material_id)
        json_path = artifact_path(root, material_id, ".json")
        csv_path = artifact_path(root, material_id, ".csv")
    except ValueError:
        return [], f"invalid material ID {material_id}"
    if json_path.is_file():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return [], f"unable to read diagnosis records for material {material_id}"
        if not isinstance(payload, list):
            return [], f"diagnosis records for material {material_id} are not a list"
        return payload, ""

    if csv_path.is_file():
        try:
            return parse_diagnosis_csv(csv_path), ""
        except RpaCsvError:
            return [], f"unable to parse diagnosis CSV for material {material_id}"

    return [], f"missing diagnosis records for material {material_id}"


def _period(top_payload):
    return {
        "actualStartDate": _text(top_payload.get("actualStartDate")),
        "actualEndDate": _text(top_payload.get("actualEndDate")),
    }


def _aggregate_interaction(records_by_material, selected_ids):
    """Aggregate per-material interaction curves by relative video position."""
    per_metric = {metric: [] for metric in INTERACTION_METRICS}
    for material_id in selected_ids:
        records = records_by_material.get(material_id, [])
        points = {metric: [] for metric in INTERACTION_METRICS}
        max_second = 0.0
        for record in records:
            if not isinstance(record, dict) or record.get("recordType") != "interaction_series":
                continue
            metric = _text(record.get("interactionMetric"))
            second = _finite_number(record.get("durationSec"))
            value = _finite_number(record.get("metricValue"))
            if metric not in points or second is None or value is None or second < 0 or value < 0:
                continue
            points[metric].append((second, value))
            max_second = max(max_second, second)
        for metric, metric_points in points.items():
            if not metric_points:
                continue
            totals = [0.0] * len(INTERACTION_BUCKETS)
            total = 0.0
            for second, value in metric_points:
                relative = second / max_second if max_second else 0.0
                bucket_index = next(
                    (index for index, (_, upper) in enumerate(INTERACTION_BUCKETS) if relative <= upper),
                    len(INTERACTION_BUCKETS) - 1,
                )
                totals[bucket_index] += value
                total += value
            per_metric[metric].append((totals, total))

    result = {}
    for metric, material_values in per_metric.items():
        if not material_values:
            result[metric] = {
                "available": False,
                "sampleMaterialCount": 0,
                "totalMetricValue": None,
                "buckets": [],
            }
            continue
        buckets = []
        for index, (lower, upper) in enumerate(INTERACTION_BUCKETS):
            shares = [values[index] / total for values, total in material_values if total > 0]
            buckets.append({
                "label": f"{int(lower * 100)}-{int(upper * 100)}%",
                "share": round(sum(shares) / len(shares), 6) if shares else 0.0,
                "materialCount": len(shares),
            })
        result[metric] = {
            "available": True,
            "sampleMaterialCount": len(material_values),
            "totalMetricValue": round(sum(total for _, total in material_values), 6),
            "buckets": buckets,
        }
    return {"available": any(item["available"] for item in result.values()), "metrics": result}


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


def _material_video_metadata(records, material_id):
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("recordType") != "material_info":
            continue
        record_material_id = _text(record.get("materialId"))
        if record_material_id and record_material_id != material_id:
            continue
        video_url = _text(record.get("videoUrl"))
        if not video_url:
            continue
        duration = _finite_number(record.get("videoDuration"))
        return {
            "src": video_url,
            "poster": _text(record.get("thumbnailUrl")),
            "durationSec": duration,
            "materialUri": _text(record.get("materialUri")),
        }
    return {}


def _materials_with_record_metadata(materials, records_by_material):
    enriched = []
    for material in materials:
        material_id = material["materialId"]
        enriched_material = dict(material)
        video = _material_video_metadata(
            records_by_material.get(material_id, []), material_id
        )
        if video:
            enriched_material["video"] = video
        enriched.append(enriched_material)
    return enriched


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


def build_material_audience(records, material_id):
    """Build the same audience fact shape used by the report, scoped to one material."""
    material_id = _text(material_id)
    records_by_material = {material_id: records if isinstance(records, list) else []}
    return {
        "metrics": {
            metric: _aggregate_audience_metric(
                records_by_material, [material_id], metric
            )
            for metric in AUDIENCE_METRICS
        }
    }


def _material_interaction_timeline(records):
    """Build deterministic interaction facts scoped to one material."""
    grouped = {metric: [] for metric in INTERACTION_METRICS}
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict) or record.get("recordType") != "interaction_series":
            continue
        metric = _text(record.get("interactionMetric"))
        second = _finite_number(record.get("durationSec"))
        value = _finite_number(record.get("metricValue"))
        if metric not in grouped or second is None or value is None or second < 0 or value < 0:
            continue
        grouped[metric].append({"second": second, "value": value})
    metrics = []
    labels = {
        "CLICK_COUNT": "整体点击次数",
        "LOSE_COUNT": "整体流失数",
        "LIKE_COUNT": "整体点赞次数",
        "COMMENT_COUNT": "整体评论次数",
        "SHARE_COUNT": "整体转发次数",
        "NEW_FOLLOWER_COUNT": "整体新增粉丝数",
    }
    for metric in INTERACTION_METRICS:
        points = sorted(grouped[metric], key=lambda item: item["second"])
        if not points:
            continue
        peak = max(points, key=lambda item: (item["value"], -item["second"]))
        metrics.append({
            "metric": metric,
            "label": labels.get(metric, metric),
            "points": points,
            "peak": {"second": peak["second"], "value": peak["value"]},
        })
    return {"metrics": metrics}


def _aggregate_audience_preferences(records_by_material, materials, metric=PREFERENCE_METRIC):
    material_buckets = {}
    baseline_buckets = {}
    for material in materials:
        material_id = material["materialId"]
        material_records = records_by_material.get(material_id, [])
        if not isinstance(material_records, list):
            material_records = []
        buckets = {}
        for record in material_records:
            if not isinstance(record, dict):
                continue
            if record.get("recordType") != "crowd_analysis":
                continue
            if record.get("displayMetric") != metric:
                continue
            if _text(record.get("dimensionKey")) != PREFERENCE_DIMENSION:
                continue
            record_material_id = _text(record.get("materialId"))
            if record_material_id and record_material_id != material_id:
                continue
            bucket = _bucket_label(record, PREFERENCE_DIMENSION)
            value = _crowd_metric_value(record)
            if not bucket or value is None or value <= 0:
                continue
            buckets[bucket] = buckets.get(bucket, 0.0) + value
            baseline_buckets[bucket] = baseline_buckets.get(bucket, 0.0) + value
        if buckets:
            material_buckets[material_id] = buckets

    baseline_total = sum(baseline_buckets.values())
    if baseline_total <= 0:
        return []
    baseline_percentages = {
        group: value / baseline_total * 100.0
        for group, value in baseline_buckets.items()
        if value > 0
    }

    groups = {}
    materials_by_id = {material["materialId"]: material for material in materials}
    for material_id, buckets in material_buckets.items():
        material = materials_by_id[material_id]
        total = sum(buckets.values())
        if total <= 0:
            continue
        for group, value in buckets.items():
            baseline_percentage = baseline_percentages.get(group)
            if not baseline_percentage:
                continue
            audience_percentage = value / total * 100.0
            preference_delta = audience_percentage - baseline_percentage
            if preference_delta <= 0:
                continue
            preference_lift = audience_percentage / baseline_percentage
            item = {
                    "materialId": material_id,
                    "materialName": material["materialName"],
                    "preferenceMetric": metric,
                    "audiencePercentage": round(audience_percentage, 6),
                    "audienceMetricValue": round(value, 6),
                    "audienceBaselinePercentage": round(baseline_percentage, 6),
                    "preferenceDeltaPercentage": round(preference_delta, 6),
                    "preferenceLift": round(preference_lift, 6),
                    "totalCost": round(
                        _finite_number(material.get("totalCost")) or 0.0, 6
                    ),
            }
            if metric == PREFERENCE_METRIC:
                item["audienceShowCount"] = round(value, 6)
            groups.setdefault(group, []).append(item)

    preferences = []
    for group, items in sorted(groups.items()):
        ranked_items = sorted(
            items,
            key=lambda item: (
                -item["totalCost"],
                -item["audiencePercentage"],
                -item["preferenceDeltaPercentage"],
                item["materialId"],
            ),
        )[:PREFERENCE_LIMIT]
        preferences.append(
            {
                "audienceGroup": group,
                "topMaterials": [
                    {"rank": index, **item}
                    for index, item in enumerate(ranked_items, start=1)
                ],
            }
        )
    return preferences


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
    return {
        "materials": materials,
        "labelSummary": label_summary,
        "keywordCloud": _keyword_cloud(materials),
    }


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
    records_by_material = {}
    warnings = []
    for material in materials:
        material_id = material["materialId"]
        records, warning = load_retained_diagnosis_records(records_dir, material_id)
        if warning:
            warnings.append(warning)
        records_by_material[material_id] = records
    return records_by_material, warnings


def _highlight_evidence_ref(material_id, rank_type):
    return f"highlight:{material_id}:{rank_type}"


def _load_highlights(top_payload, peak_output_dir):
    materials, _warnings = _selected_materials(top_payload)
    root = Path(peak_output_dir)
    result = {"materials": []}
    warnings = []

    for material in materials:
        material_id = material["materialId"]
        material_dir = root / _safe_component(material_id)
        summary_path = material_dir / "summary.json"
        frames = []
        summary = {}
        if not summary_path.is_file():
            warnings.append(f"missing high-light summary for material {material_id}")
        else:
            try:
                summary = _read_json(
                    summary_path, f"high-light summary for material {material_id}"
                )
            except DiagnosticAnalysisError:
                warnings.append(
                    f"unable to read high-light summary for material {material_id}"
                )
                summary = {}
            raw_frames = summary.get("frames", []) if isinstance(summary, dict) else []
            if not isinstance(raw_frames, list):
                warnings.append(
                    f"high-light summary frames for material {material_id} are not a list"
                )
                raw_frames = []
            for raw_frame in raw_frames:
                if not isinstance(raw_frame, dict):
                    continue
                rank_type = _text(raw_frame.get("rankType"))
                frame_name = _text(raw_frame.get("framePath"))
                frame_path = material_dir / frame_name
                if not rank_type or not frame_name or not frame_path.is_file():
                    warnings.append(
                        f"missing high-light JPEG for material {material_id}: "
                        f"{rank_type or 'unknown'}"
                    )
                    continue
                frames.append(
                    {
                        "materialId": material_id,
                        "rankType": rank_type,
                        "durationSec": raw_frame.get("durationSec"),
                        "metricValue": raw_frame.get("metricValue"),
                        "framePath": str(frame_path),
                        "evidenceRef": _highlight_evidence_ref(material_id, rank_type),
                    }
                )
        raw_missing_frames = (
            summary.get("missingFrames", []) if isinstance(summary, dict) else []
        )
        missing_frames = []
        if isinstance(raw_missing_frames, list):
            for raw_frame in raw_missing_frames:
                if not isinstance(raw_frame, dict):
                    continue
                rank_type = _text(raw_frame.get("rankType"))
                if not rank_type:
                    continue
                missing_frames.append(
                    {
                        "rankType": rank_type,
                        "durationSec": raw_frame.get("durationSec"),
                        "metricValue": raw_frame.get("metricValue"),
                        "requestedTimestampSec": raw_frame.get(
                            "requestedTimestampSec"
                        ),
                        "errorType": _text(raw_frame.get("errorType")),
                        "errorMessage": _text(raw_frame.get("errorMessage")),
                    }
                )
        frame_coverage = (
            summary.get("frameCoverage", {}) if isinstance(summary, dict) else {}
        )
        result["materials"].append(
            {
                "materialId": material_id,
                "frames": frames,
                "missingFrames": missing_frames,
                "frameCoverage": frame_coverage
                if isinstance(frame_coverage, dict)
                else {},
            }
        )

    return result, warnings


def _commercial_facts(top_payload, top_material_roi_facts_path):
    unavailable = {
        "available": False,
        "totals": {},
        "metrics": [],
    }
    if top_material_roi_facts_path is None:
        return unavailable
    facts = _read_json(top_material_roi_facts_path, "top material ROI facts")
    materials, _warnings = _selected_materials(top_payload)
    expected_ids = [material["materialId"] for material in materials]
    if (
        facts.get("schemaVersion") != 1
        or facts.get("actualStartDate") != _text(top_payload.get("actualStartDate"))
        or facts.get("actualEndDate") != _text(top_payload.get("actualEndDate"))
        or facts.get("materialIds") != expected_ids
    ):
        raise DiagnosticAnalysisError("top material ROI facts do not match selected materials")
    totals = facts.get("totals")
    if not isinstance(totals, dict) or set(totals) != {
        "cost", "netSettlementAmount", "overallPaymentAmount"
    }:
        raise DiagnosticAnalysisError("top material ROI facts totals are invalid")
    metrics = facts.get("metrics")
    if not isinstance(metrics, list) or len(metrics) != 2:
        raise DiagnosticAnalysisError("top material ROI facts metrics are invalid")
    expected_metrics = (
        ("netSettlementRoi", "净成交 ROI"),
        ("overallPaymentRoi", "整体支付 ROI"),
    )
    normalized_metrics = []
    for raw, (key, label) in zip(metrics, expected_metrics):
        if (
            not isinstance(raw, dict)
            or raw.get("key") != key
            or raw.get("label") != label
            or type(raw.get("available")) is not bool
        ):
            raise DiagnosticAnalysisError("top material ROI facts metrics are invalid")
        value = raw.get("value")
        if raw["available"] and not _text(value):
            raise DiagnosticAnalysisError("top material ROI facts metrics are invalid")
        if not raw["available"] and value is not None:
            raise DiagnosticAnalysisError("top material ROI facts metrics are invalid")
        normalized_metrics.append({
            "evidenceRef": "commercial:" + key,
            "key": key,
            "label": label,
            "value": _text(value) if raw["available"] else None,
            "available": raw["available"],
        })
    return {
        "available": True,
        "totals": {key: _text(totals[key]) for key in sorted(totals)},
        "metrics": normalized_metrics,
    }


def _portfolio_facts(top_payload, materials):
    raw = top_payload.get("portfolioFacts") if isinstance(top_payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    result = {
        key: deepcopy(raw[key])
        for key in (
            "schemaVersion", "period", "newMaterialCount7d",
            "newMaterialStartDate", "newMaterialEndDate", "materialCount",
            "summaryMetrics",
        ) if key in raw
    }
    all_materials = raw.get("materials")
    if isinstance(all_materials, dict):
        # Chapter 01 is a portfolio-level view: retain the complete material
        # list rather than narrowing facts to the RPA-selected Top N assets.
        result["materials"] = {
            material_id: deepcopy(value)
            for material_id, value in all_materials.items()
            if isinstance(value, dict)
        }
    return result


def _publish_prompt(output_dir, prompt):
    root = Path(output_dir)
    prompt_path = root / "diagnostic-analysis-prompt.md"
    prompt_staging = root / f".diagnostic-analysis-prompt.{uuid.uuid4().hex}.md.tmp"
    try:
        root.mkdir(parents=True, exist_ok=True)
        prompt_staging.write_text(prompt, encoding="utf-8")
        os.replace(prompt_staging, prompt_path)
    except OSError as exc:
        raise DiagnosticAnalysisError(
            f"unable to publish diagnostic analysis output in {root}: {exc}"
        ) from exc
    finally:
        for staging in (prompt_staging,):
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    return prompt_path


def build_diagnostic_analysis(top_payload, records_by_material, commercial=None):
    materials, warnings = _selected_materials(top_payload)
    selected_ids = [material["materialId"] for material in materials]
    script_facts = _build_scripts(records_by_material, selected_ids, warnings)
    enriched_materials = _materials_with_record_metadata(materials, records_by_material)
    script_by_material = {
        _text(item.get("materialId")): item
        for item in script_facts.get("materials", [])
        if isinstance(item, dict) and _text(item.get("materialId"))
    }
    for material in enriched_materials:
        material_id = material["materialId"]
        material["audience"] = build_material_audience(
            records_by_material.get(material_id, []), material_id
        )
        material["script"] = deepcopy(script_by_material.get(material_id, {}))
        material["interactionTimeline"] = _material_interaction_timeline(
            records_by_material.get(material_id, [])
        )
        script_refs = []
        for label in material["script"].get("labels", []):
            if not isinstance(label, dict):
                continue
            level1 = _text(label.get("level1"))
            level2 = _text(label.get("level2"))
            if level1 and level2:
                script_refs.append(f"script:{material_id}:{level1}:{level2}")
        audience_refs = []
        for metric, metric_data in material["audience"].get("metrics", {}).items():
            for dimension_key, dimension in metric_data.get("dimensions", {}).items():
                for bucket in dimension.get("buckets", []):
                    bucket_name = _text(bucket.get("bucket")) if isinstance(bucket, dict) else ""
                    if bucket_name:
                        audience_refs.append(
                            f"audience:{metric}:{dimension_key}:{bucket_name}"
                        )
        interaction_refs = [
            f"interaction:{item['metric']}:total"
            for item in material["interactionTimeline"].get("metrics", [])
            if isinstance(item, dict) and _text(item.get("metric"))
        ]
        material["evidenceIndex"] = {
            "audience": list(dict.fromkeys(audience_refs)),
            "script": list(dict.fromkeys(script_refs)),
            "highlight": [],
            "interaction": list(dict.fromkeys(interaction_refs)),
            "commercial": [],
        }
    preferences_by_metric = {
        metric: _aggregate_audience_preferences(records_by_material, materials, metric)
        for metric in AUDIENCE_METRICS
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "period": _period(top_payload),
        "materials": enriched_materials,
        "portfolio": _portfolio_facts(top_payload, materials),
        "audience": {
            "metrics": {
                metric: _aggregate_audience_metric(records_by_material, selected_ids, metric)
                for metric in AUDIENCE_METRICS
            },
            "preferencesByMetric": preferences_by_metric,
        },
        "scripts": script_facts,
        "interaction": _aggregate_interaction(records_by_material, selected_ids),
        "delivery": {"materials": _build_delivery(records_by_material, selected_ids)},
        "commercial": commercial if commercial is not None else {
            "available": False, "totals": {}, "metrics": []
        },
        "warnings": warnings,
    }


def prepare_diagnostic_analysis(
    top_materials_json,
    diagnosis_records_dir,
    output_dir,
    prompt_template=None,
    peak_output_dir=None,
    top_material_roi_facts_path=None,
):
    top_payload = _read_json(top_materials_json, "top materials JSON")
    records_by_material, read_warnings = _load_selected_records(
        top_payload, diagnosis_records_dir
    )
    prompt = _read_text(
        prompt_template if prompt_template else DEFAULT_PROMPT_PATH,
        "diagnostic prompt template",
    )
    analysis = build_diagnostic_analysis(
        top_payload,
        records_by_material,
        commercial=_commercial_facts(top_payload, top_material_roi_facts_path),
    )
    highlight_warnings = []
    if peak_output_dir:
        analysis["highlights"], highlight_warnings = _load_highlights(
            top_payload, peak_output_dir
        )
    analysis["warnings"] = read_warnings + analysis["warnings"] + highlight_warnings
    publish_analysis_units(output_dir, analysis)
    prompt_path = _publish_prompt(output_dir, prompt)
    return prompt_path, analysis
