"""Small, dependency-free validators for persisted batch/report contracts."""

from copy import deepcopy
import json
from pathlib import Path


class SchemaError(ValueError):
    """Raised when a persisted artifact cannot be normalized safely."""


_OPTIONAL_WAVE_COMPLETION_FIELDS = (
    "waveFinishedAtSec",
    "waveCsvArtifactFinishedAtSec",
)


def _text(value):
    return str(value).strip() if value is not None else ""


def _normalize_inflight_wave_completion(payload):
    """Treat legacy null wave-completion fields as absent during resume."""
    for field in _OPTIONAL_WAVE_COMPLETION_FIELDS:
        if payload.get(field) is None:
            payload.pop(field, None)


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
    for field in (
        "waveStartedAtSec",
        "waveFinishedAtSec",
        "waveCsvArtifactFinishedAtSec",
    ):
        if field in payload and (
            isinstance(payload[field], bool)
            or not isinstance(payload[field], (int, float))
            or payload[field] < 0
        ):
            raise SchemaError(f"{scope} {field} must be a non-negative number")
    if (
        "waveStartedAtSec" in payload
        and "waveCsvArtifactFinishedAtSec" in payload
        and payload["waveCsvArtifactFinishedAtSec"] < payload["waveStartedAtSec"]
    ):
        raise SchemaError(
            f"{scope} waveCsvArtifactFinishedAtSec cannot precede waveStartedAtSec"
        )


def _validate_optional_lifecycle_fields(payload, *, scope):
    for field in (
        "rpaSoftTimeoutExceeded",
        "rpaHardTimeoutExceeded",
        "rpaTimeoutExceeded",
        "rpaCsvVerified",
    ):
        if field in payload and type(payload[field]) is not bool:
            raise SchemaError(f"{scope} {field} must be a boolean")
    if "rpaTimeoutSec" in payload and (
        isinstance(payload["rpaTimeoutSec"], bool)
        or not isinstance(payload["rpaTimeoutSec"], (int, float))
        or payload["rpaTimeoutSec"] <= 0
    ):
        raise SchemaError(f"{scope} rpaTimeoutSec must be a positive number")
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
    if "backfillReason" in payload and payload["backfillReason"] not in (
        None,
        "rpa_collection_failure",
        "postprocess_failed_after_retries",
    ):
        raise SchemaError(f"{scope} backfillReason is invalid")


def _validate_rpa_collection(payload):
    collection = payload.get("rpaCollection")
    if collection is None:
        return
    if not isinstance(collection, dict):
        raise SchemaError("batch manifest rpaCollection must be an object")
    if collection.get("status") not in ("collecting", "complete", "backfilling"):
        raise SchemaError("batch manifest rpaCollection status is invalid")
    for field in ("targetCount", "acceptedCount"):
        if type(collection.get(field)) is not int or collection[field] < 0:
            raise SchemaError(f"batch manifest rpaCollection {field} must be a non-negative integer")


def _normalize_postprocess_telemetry(payload, *, scope):
    payload.setdefault("postprocessPhase", None)
    payload.setdefault("postprocessAttempts", [])
    payload.setdefault("compensationStatus", "not_needed")

    if payload["postprocessPhase"] not in (
        None,
        "parallel",
        "serial_compensation",
    ):
        raise SchemaError(f"{scope} postprocessPhase is invalid")
    if payload["compensationStatus"] not in (
        "not_needed",
        "queued",
        "succeeded",
        "failed",
    ):
        raise SchemaError(f"{scope} compensationStatus is invalid")
    attempts = payload["postprocessAttempts"]
    if not isinstance(attempts, list):
        raise SchemaError(f"{scope} postprocessAttempts must be a list")
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise SchemaError(f"{scope} postprocess attempt must be an object")
        if attempt.get("phase") not in ("parallel", "serial_compensation"):
            raise SchemaError(f"{scope} postprocess attempt phase is invalid")
        for field in ("startedAtSec", "finishedAtSec", "durationSec"):
            if field in attempt and (
                isinstance(attempt[field], bool)
                or not isinstance(attempt[field], (int, float))
                or attempt[field] < 0
            ):
                raise SchemaError(f"{scope} postprocess attempt {field} is invalid")


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
    _normalize_inflight_wave_completion(normalized)
    _validate_optional_wave_fields(normalized, scope="batch manifest")
    _validate_optional_lifecycle_fields(normalized, scope="batch manifest")
    _validate_rpa_collection(normalized)

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
        _normalize_inflight_wave_completion(item)
        _validate_optional_wave_fields(item, scope="batch manifest material")
        _validate_optional_lifecycle_fields(item, scope="batch manifest material")
        _normalize_postprocess_telemetry(item, scope="batch manifest material")

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


def wave_manifest(
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
    counts = stage_counts(
        ordered(submitted), base_dir=Path(args.output_dir).parent
    )
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
        "stageCounts": counts,
        "rpaCollection": {
            "status": "complete" if counts["rpaAcceptedCount"] >= args.target_top_n else "collecting",
            "targetCount": args.target_top_n,
            "acceptedCount": counts["rpaAcceptedCount"],
        },
        "timingsSec": {"totalSec": 0.0},
    }


def load_wave_resume(path, args, candidates, error_type):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(
            f"unable to read wave batch manifest for resume: {path}"
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
        raise error_type("wave batch manifest does not match the requested RPA batch")
    return normalized
