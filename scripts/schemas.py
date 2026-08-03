"""Small, dependency-free validators for persisted batch/report contracts."""

from copy import deepcopy
import json
from pathlib import Path


class SchemaError(ValueError):
    """Raised when a persisted artifact cannot be normalized safely."""


def _text(value):
    return str(value).strip() if value is not None else ""


def _validate_optional_wave_fields(payload, *, scope):
    if "waveBarrier" in payload and type(payload["waveBarrier"]) is not bool:
        raise SchemaError(f"{scope} waveBarrier must be a boolean")
    if "waveIndex" in payload and (
        type(payload["waveIndex"]) is not int or payload["waveIndex"] < 0
    ):
        raise SchemaError(f"{scope} waveIndex must be a non-negative integer")
    if "waveSize" in payload and (
        type(payload["waveSize"]) is not int or payload["waveSize"] <= 0
    ):
        raise SchemaError(f"{scope} waveSize must be a positive integer")
    for field in ("waveStartedAtSec", "waveFinishedAtSec"):
        if field in payload and (
            isinstance(payload[field], bool)
            or not isinstance(payload[field], (int, float))
            or payload[field] < 0
        ):
            raise SchemaError(f"{scope} {field} must be a non-negative number")


def _validate_optional_lifecycle_fields(payload, *, scope):
    for field in ("rpaSoftTimeoutExceeded", "rpaHardTimeoutExceeded"):
        if field in payload and type(payload[field]) is not bool:
            raise SchemaError(f"{scope} {field} must be a boolean")
    for field in (
        "rpaStartedAtSec",
        "rpaSoftTimeoutAtSec",
        "rpaFinishedAtSec",
        "postprocessStartedAtSec",
        "postprocessFinishedAtSec",
        "lastStateChangedAtSec",
        "lastProgressAtSec",
    ):
        if field in payload and (
            isinstance(payload[field], bool)
            or not isinstance(payload[field], (int, float))
            or payload[field] < 0
        ):
            raise SchemaError(f"{scope} {field} must be a non-negative number")
    if "postprocessAttemptCount" in payload and (
        type(payload["postprocessAttemptCount"]) is not int
        or payload["postprocessAttemptCount"] < 0
    ):
        raise SchemaError(f"{scope} postprocessAttemptCount must be a non-negative integer")


def normalize_manifest(payload, *, material_ids=None):
    """Normalize legacy v1 and streaming v2 manifests to one list-based shape.

    ``sourceSchemaVersion`` preserves provenance; consumers should use the
    normalized v2-shaped fields and never branch on the source version.
    """
    if not isinstance(payload, dict):
        raise SchemaError("batch manifest must be an object")
    source_version = payload.get("schemaVersion")
    if source_version not in (1, 2):
        raise SchemaError("unsupported batch manifest schemaVersion")
    normalized = deepcopy(payload)
    normalized["sourceSchemaVersion"] = source_version
    normalized["schemaVersion"] = 2
    _validate_optional_wave_fields(normalized, scope="batch manifest")
    _validate_optional_lifecycle_fields(normalized, scope="batch manifest")

    materials = normalized.get("materials")
    if materials is None:
        materials = [
            item
            for field in ("selectedMaterials", "rejectedMaterials", "cancelledMaterials")
            for item in (normalized.get(field) or [])
            if isinstance(item, dict)
        ]
        normalized["materials"] = materials
    if isinstance(materials, dict):
        ordered_ids = material_ids or list(materials)
        normalized["materials"] = [
            dict(
                materials[material_id],
                materialId=material_id,
                rpaStatus=(materials[material_id].get("rpaStatus") or "selected")
                if materials[material_id].get("status") == "succeeded"
                else (materials[material_id].get("rpaStatus") or "rejected"),
                postprocessStatus=(materials[material_id].get("postprocessStatus") or "succeeded")
                if materials[material_id].get("status") == "succeeded"
                else (materials[material_id].get("postprocessStatus") or "failed"),
            )
            for material_id in ordered_ids
            if isinstance(materials.get(material_id), dict)
        ]
    elif isinstance(materials, list):
        normalized["materials"] = [dict(item) for item in materials if isinstance(item, dict)]
    else:
        raise SchemaError("batch manifest materials must be a list or object")
    for item in normalized["materials"]:
        _validate_optional_wave_fields(item, scope="batch manifest material")
        _validate_optional_lifecycle_fields(item, scope="batch manifest material")

    normalized.setdefault(
        "materialIds",
        [_text(item.get("materialId")) for item in normalized["materials"]],
    )
    normalized["materialIds"] = [
        _text(material_id) for material_id in normalized["materialIds"] if _text(material_id)
    ]
    if material_ids is not None and set(normalized["materialIds"]) != set(material_ids):
        raise SchemaError("batch manifest materialIds do not match the requested batch")

    if not isinstance(normalized.get("selectedMaterials"), list):
        normalized["selectedMaterials"] = [
            item for item in normalized["materials"]
            if item.get("rpaStatus") == "selected" or item.get("status") == "succeeded"
        ]
    if not isinstance(normalized.get("rejectedMaterials"), list):
        normalized["rejectedMaterials"] = [
            item for item in normalized["materials"] if item.get("rpaStatus") == "rejected"
        ]
    if not isinstance(normalized.get("cancelledMaterials"), list):
        normalized["cancelledMaterials"] = [
            item for item in normalized["materials"] if item.get("rpaStatus") == "cancelled"
        ]
    if not isinstance(normalized.get("submittedMaterials"), list):
        normalized["submittedMaterials"] = [
            item for item in normalized["materials"]
            if item.get("rpaStatus") in {"selected", "rejected"}
            or item.get("status") in {"running", "succeeded", "failed"}
        ]
    return normalized


def validate_manifest(payload, *, material_ids=None, require_run_id=False):
    normalized = normalize_manifest(payload, material_ids=material_ids)
    if require_run_id and not _text(normalized.get("runId")):
        raise SchemaError("batch manifest runId is required")
    for field in ("selectedMaterials", "rejectedMaterials", "cancelledMaterials"):
        if not isinstance(normalized.get(field), list):
            raise SchemaError(f"batch manifest {field} must be a list")
    return normalized


def validate_preflight_receipt(payload, *, args, candidate_payload, material_ids):
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 2:
        raise SchemaError("RPA preflight receipt must use schemaVersion 2")
    if payload.get("satisfied") is not True:
        raise SchemaError("RPA preflight receipt is not satisfied")
    if payload.get("shopId") != args.shop_id:
        raise SchemaError("RPA preflight receipt shopId does not match")
    _validate_optional_wave_fields(payload, scope="RPA preflight receipt")
    _validate_optional_lifecycle_fields(payload, scope="RPA preflight receipt")
    expected_dates = {
        "type": args.date_range_type,
        "customStartDate": args.custom_start_date,
        "customEndDate": args.custom_end_date,
        "actualStartDate": candidate_payload.get("actualStartDate"),
        "actualEndDate": candidate_payload.get("actualEndDate"),
    }
    date_range = payload.get("dateRange")
    if not isinstance(date_range, dict) or any(
        date_range.get(key) != value for key, value in expected_dates.items()
    ):
        raise SchemaError("RPA preflight receipt date range does not match")
    selected = payload.get("selectedMaterials")
    if not isinstance(selected, list):
        raise SchemaError("RPA preflight receipt selectedMaterials must be a list")
    for field in ("selectedMaterials", "rejectedMaterials", "cancelledMaterials"):
        entries = payload.get(field, [])
        if not isinstance(entries, list):
            raise SchemaError(f"RPA preflight receipt {field} must be a list")
        for entry in entries:
            if isinstance(entry, dict):
                _validate_optional_wave_fields(
                    entry, scope="RPA preflight receipt material"
                )
                _validate_optional_lifecycle_fields(
                    entry, scope="RPA preflight receipt material"
                )
    selected_ids = {
        _text(item.get("materialId")) for item in selected if isinstance(item, dict)
    }
    missing = [material_id for material_id in material_ids if material_id not in selected_ids]
    if missing:
        raise SchemaError(
            "RPA preflight receipt does not select material " + ", ".join(missing)
        )
    return payload


def validate_report_data(payload, *, run_id=None):
    if not isinstance(payload, dict):
        raise SchemaError("report data must be an object")
    if payload.get("schemaVersion") != 1 or payload.get("status") != "complete":
        raise SchemaError("report data must be a complete schemaVersion 1 report")
    report_run_id = _text(payload.get("runId"))
    pipeline = payload.get("pipelineSummary")
    if not report_run_id or not isinstance(pipeline, dict) or _text(pipeline.get("runId")) != report_run_id:
        raise SchemaError("report data runId and pipelineSummary.runId must match")
    if run_id is not None and report_run_id != _text(run_id):
        raise SchemaError("report data runId does not match the batch manifest")
    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise SchemaError("report data materials must be a non-empty list")
    material_ids = []
    for item in materials:
        if not isinstance(item, dict) or not _text(item.get("materialId")):
            raise SchemaError("report data contains a material without materialId")
        material_ids.append(_text(item["materialId"]))
    if len(material_ids) != len(set(material_ids)):
        raise SchemaError("report data contains duplicate materialId values")
    return payload


def streaming_manifest(
    args,
    candidates,
    entries,
    submitted,
    selected,
    rejected,
    cancelled,
    *,
    public_entry,
    ordered,
    stage_counts,
):
    ordered_materials = [public_entry(entries[index]) for index in sorted(entries)]
    return {
        "schemaVersion": 2,
        "source": "rpa",
        "runId": getattr(args, "run_id", None),
        "shopId": args.shop_id,
        "targetTopN": args.target_top_n,
        "candidateCount": len(candidates),
        "candidateLimit": len(candidates),
        "rpaConcurrency": args.rpa_concurrency,
        "postprocessConcurrency": args.postprocess_concurrency,
        "waveBarrier": getattr(args, "rpa_wave_barrier", True),
        "dateRange": {
            "type": args.date_range_type,
            "customStartDate": args.custom_start_date,
            "customEndDate": args.custom_end_date,
        },
        "materialIds": [entry["materialId"] for entry in ordered_materials],
        "materials": ordered_materials,
        "submittedMaterials": ordered(submitted),
        "selectedMaterials": ordered(selected),
        "rejectedMaterials": ordered(rejected),
        "cancelledMaterials": ordered(cancelled),
        "stageCounts": stage_counts(
            ordered(submitted), base_dir=Path(args.output_dir).parent
        ),
        "timingsSec": {"totalSec": 0.0},
    }


def load_streaming_resume(path, args, candidates, error_type):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(
            f"unable to read streaming batch manifest for resume: {path}"
        ) from exc
    expected = {
        "schemaVersion": 2,
        "source": "rpa",
        "shopId": args.shop_id,
        "targetTopN": args.target_top_n,
        "dateRange": {
            "type": args.date_range_type,
            "customStartDate": args.custom_start_date,
            "customEndDate": args.custom_end_date,
        },
        "materialIds": [
            str(item.get("materialId") or "").strip() for item in candidates
        ],
    }
    try:
        normalized = validate_manifest(manifest, material_ids=expected["materialIds"])
    except SchemaError as exc:
        raise error_type(str(exc)) from exc
    if any(
        normalized.get(field) != expected[field]
        for field in (
            "schemaVersion",
            "source",
            "shopId",
            "targetTopN",
            "dateRange",
            "materialIds",
        )
    ):
        raise error_type("streaming batch manifest does not match the requested RPA batch")
    return normalized


def build_batch_manifest(args, material_ids, empty_timings):
    return {
        "schemaVersion": 2,
        "source": "rpa",
        "shopId": args.shop_id,
        "concurrency": args.concurrency,
        "dateRange": {
            "type": args.date_range_type,
            "customStartDate": args.custom_start_date,
            "customEndDate": args.custom_end_date,
        },
        "materialIds": material_ids,
        "timingsSec": {"totalSec": 0.0},
        "materials": {
            material_id: {
                "status": "pending",
                "attempts": 0,
                "summaryJson": None,
                "summaryMd": None,
                "errorType": None,
                "errorMessage": None,
                "timingsSec": empty_timings(),
            }
            for material_id in material_ids
        },
    }


def load_batch_resume(path, expected_manifest, error_type):
    source = Path(path)
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(f"unable to read batch manifest for resume: {source}") from exc
    try:
        normalized = validate_manifest(
            manifest, material_ids=expected_manifest["materialIds"]
        )
    except SchemaError as exc:
        raise error_type(str(exc)) from exc
    expected_normalized = validate_manifest(expected_manifest)
    if any(
        normalized.get(field) != expected_normalized.get(field)
        for field in ("source", "shopId", "dateRange", "materialIds")
    ):
        raise error_type("batch manifest does not match the requested RPA batch")
    return manifest
