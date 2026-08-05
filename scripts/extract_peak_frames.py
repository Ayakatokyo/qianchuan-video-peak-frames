#!/usr/bin/env python3
import argparse
import json
import math
import os
import signal
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from peak_frame_lib import (
    DEFAULT_FFMPEG_TIMEOUT_SEC,
    FfmpegError,
    InputDataError,
    parse_click_series,
    process_local,
    process_records,
)
from rpa_adapter import (
    FUNCTION_CODE,
    RpaAdapterError,
    download_video,
    fetch_material_diagnosis,
    fetch_material_preflight,
    find_material_video_url,
    list_accounts,
    preflight_material_availability,
)
from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv
from batch_runtime import BatchLockError, acquire_run_lock, read_json, stage_counts
import batch_verification
from batch_verification import material_output_issues
import material_processing
import rpa_batch_runner
import rpa_preflight
from path_safety import artifact_path, validate_material_id
from rpa_runtime import multiprocessing_context_name, run_isolated_preflight
from schemas import SchemaError, validate_manifest, validate_preflight_receipt
import schemas

_material_output_issues = material_output_issues


BATCH_MANIFEST_NAME = "batch-manifest.json"
BATCH_MANIFEST_VERSION = 2
STREAMING_BATCH_MANIFEST_VERSION = 2
BATCH_PARTIAL_FAILURE_EXIT_CODE = 5
BATCH_VERIFICATION_NAME = "batch-verification.json"
BATCH_VERIFICATION_FAILURE_EXIT_CODE = 6
RPA_PREFLIGHT_NAME = "rpa-preflight.json"
RPA_READY_TOP_MATERIALS_NAME = "top_materials_rpa_ready.json"
MATERIAL_TIMING_FIELDS = (
    "rpaFetchSec",
    "diagnosisRecordWriteSec",
    "videoDownloadSec",
    "frameExtractionSec",
    "totalSec",
)
HANDLED_MATERIAL_ERRORS = (InputDataError, FfmpegError, RpaAdapterError, RpaCsvError)
_MATERIAL_TIMING_STATE = threading.local()


DATE_RANGE_TYPES = (
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_15_DAYS",
    "LAST_WEEK",
    "THIS_MONTH",
    "LAST_MONTH",
    "CUSTOM",
)


def _positive_timeout(value):
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError(
            "FFmpeg timeout must be a positive finite number"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError(
            "FFmpeg timeout must be a positive finite number"
        )
    return timeout


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "concurrency must be a positive integer"
        ) from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("concurrency must be a positive integer")
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        description="Extract video frames at Qianchuan CLICK_COUNT peaks."
    )
    parser.add_argument("--source", choices=("local", "rpa"), default="local")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--input-json")
    input_group.add_argument("--input-csv")
    parser.add_argument("--video")
    parser.add_argument("--output-dir")
    parser.add_argument("--ffmpeg-bin")
    parser.add_argument("--ffmpeg-timeout-sec", type=_positive_timeout, default=DEFAULT_FFMPEG_TIMEOUT_SEC)
    parser.add_argument("--shop-id")
    parser.add_argument("--top-materials-json")
    parser.add_argument("--diagnosis-records-dir")
    parser.add_argument("--batch-manifest")
    parser.add_argument("--target-top-n", type=_positive_int)
    parser.add_argument("--candidate-top-n", type=_positive_int)
    parser.add_argument("--rpa-preflight-timeout-sec", type=_positive_timeout, default=600)
    parser.add_argument("--rpa-preflight-soft-timeout-sec", type=_positive_timeout, default=480)
    parser.add_argument("--rpa-late-artifact-grace-sec", type=_positive_timeout, default=30)
    parser.add_argument("--rpa-result-recovery-attempts", type=_positive_int, default=2)
    parser.add_argument("--rpa-ready-top-materials-json")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--concurrency", type=_positive_int)
    parser.add_argument("--rpa-concurrency", type=_positive_int, default=3)
    parser.add_argument("--postprocess-concurrency", type=_positive_int, default=3)
    parser.set_defaults(
        allow_rpa_refetch=False,
        preflight_only=False,
        rpa_preflight_json=None,
        rpa_wave_barrier=True,
    )
    parser.add_argument("--date-range-type", choices=DATE_RANGE_TYPES)
    parser.add_argument("--custom-start-date")
    parser.add_argument("--custom-end-date")
    parser.add_argument("--list-shop-accounts", action="store_true")
    return parser


def resolve_concurrency(args, argv):
    legacy = args.concurrency
    if legacy is not None:
        if not any(token == "--rpa-concurrency" or token.startswith("--rpa-concurrency=") for token in argv):
            args.rpa_concurrency = legacy
        if not any(token == "--postprocess-concurrency" or token.startswith("--postprocess-concurrency=") for token in argv):
            args.postprocess_concurrency = legacy
    args.concurrency = legacy or 1


def _normalize_account_rows(payload):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RpaAdapterError("RPA authorization list must contain a data array")
    accounts = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        shop_id = str(row.get("shop_id") or "").strip()
        account = str(row.get("account") or "").strip()
        if shop_id:
            accounts.append({"account": account, "shop_id": shop_id})
    return accounts


def _format_account_table(accounts):
    return "\n".join(
        f"{item['account'] or '<unknown-account>'}\t{item['shop_id']}"
        for item in accounts
    )


def _load_top_materials_payload(path, date_range_type, custom_start_date, custom_end_date):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaAdapterError(f"unable to read top materials JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RpaAdapterError("top materials JSON must be an object")
    actual_start_date = payload.get("actualStartDate")
    actual_end_date = payload.get("actualEndDate")
    if not isinstance(actual_start_date, str) or not isinstance(actual_end_date, str):
        raise RpaAdapterError("top materials JSON requires actualStartDate and actualEndDate")
    if date_range_type == "CUSTOM" and (
        actual_start_date != custom_start_date or actual_end_date != custom_end_date
    ):
        raise RpaAdapterError(
            "top materials JSON date range does not match the requested CUSTOM range"
        )
    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise RpaAdapterError("top materials JSON must contain a nonempty materials list")

    material_ids = []
    for material in materials:
        if not isinstance(material, dict):
            raise RpaAdapterError("top materials entries must be objects")
        material_id = str(material.get("materialId") or "").strip()
        if not material_id:
            raise RpaAdapterError("top materials entries require materialId")
        try:
            validate_material_id(material_id)
        except ValueError as exc:
            raise RpaAdapterError(f"invalid top materials materialId {material_id!r}") from exc
        if material_id in material_ids:
            raise RpaAdapterError("top materials entries must not repeat materialId")
        material_ids.append(material_id)
    if payload.get("materialCount") != len(material_ids):
        raise RpaAdapterError("top materials JSON materialCount does not match materials")
    return payload, material_ids


def _write_diagnosis_records_atomically(records, records_dir, material_id):
    target_dir = Path(records_dir)
    target = artifact_path(target_dir, material_id, ".json")
    target_dir.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError):
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def _write_json_atomically_impl(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError):
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


class RpaPreflightDeadlineExceeded(TimeoutError):
    """Raised when a single-material RPA preflight exceeds its wall-clock budget."""



def _normalise_account_rows(payload):
    return _normalize_account_rows(payload)


def _format_account_rows(accounts):
    return _format_account_table(accounts)


def _list_shop_accounts():
    accounts = _normalise_account_rows(list_accounts())
    print("account\tshop_id")
    if accounts:
        print(_format_account_rows(accounts))
    return 0


def _resolve_shop_id(args):
    explicit = str(args.shop_id or "").strip()
    if explicit:
        return explicit

    accounts = _normalise_account_rows(list_accounts())
    if not accounts:
        raise RpaAdapterError(
            "no authorized RPA accounts are available for the current credentials"
        )
    if len(accounts) > 1:
        raise RpaAdapterError(
            "multiple authorized RPA accounts were found; pass --shop-id explicitly:\n"
            + _format_account_rows(accounts)
        )
    return accounts[0]["shop_id"]


def _load_top_materials(path, date_range_type, custom_start_date, custom_end_date):
    return _load_top_materials_payload(
        path, date_range_type, custom_start_date, custom_end_date
    )


def _top_material_ids(path, date_range_type, custom_start_date, custom_end_date):
    _payload, material_ids = _load_top_materials(
        path, date_range_type, custom_start_date, custom_end_date
    )
    return material_ids


def _write_diagnosis_records(records, records_dir, material_id):
    try:
        return _write_diagnosis_records_atomically(records, records_dir, material_id)
    except ValueError as exc:
        raise RpaAdapterError(f"invalid material ID {material_id!r}: {exc}") from exc
    except (OSError, TypeError) as exc:
        raise RpaAdapterError(
            f"unable to write diagnosis records for material {material_id}: {exc}"
        ) from exc


def _read_diagnosis_records(path, material_id):
    source = Path(path)
    try:
        records = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaAdapterError(
            f"unable to read diagnosis records for material {material_id}: {exc}"
        ) from exc
    if not isinstance(records, list):
        raise RpaAdapterError(
            f"diagnosis records for material {material_id} must be a list"
        )
    find_material_video_url(records, material_id)
    return records


def _diagnosis_csv_dir(args):
    if args.diagnosis_records_dir:
        return Path(args.diagnosis_records_dir).parent / "diagnosis-csv"
    return Path(args.output_dir) / "diagnosis-csv"


def _diagnosis_records_dir(args):
    if args.diagnosis_records_dir:
        return Path(args.diagnosis_records_dir)
    return Path(args.output_dir).parent / "diagnosis-records"


def _run_relative_path(args, path):
    return os.path.relpath(Path(path), Path(args.output_dir).parent)


def _write_json_atomically(path, payload):
    try:
        return _write_json_atomically_impl(path, payload)
    except (OSError, TypeError, ValueError) as exc:
        raise RpaAdapterError(
            f"unable to write batch manifest {path}: {exc}"
        ) from exc


def _preflight_path(args):
    return Path(args.output_dir) / RPA_PREFLIGHT_NAME


def _ready_top_materials_path(args):
    if args.rpa_ready_top_materials_json:
        return Path(args.rpa_ready_top_materials_json)
    return Path(args.output_dir) / RPA_READY_TOP_MATERIALS_NAME


def _preflight_enabled(args):
    return bool(args.target_top_n or args.preflight_only)


def _material_entry_with_preflight(material, outcome, status=None, timings=None):
    enriched = dict(material)
    enriched.update(
        {
            "rpaAvailable": bool(outcome.get("rpaAvailable")),
            "failureReason": outcome.get("failureReason"),
            "videoUrl": outcome.get("videoUrl"),
            "rpaSoftTimeoutExceeded": bool(outcome.get("softTimeoutExceeded")),
            "rpaHardTimeoutExceeded": bool(outcome.get("hardTimeoutExceeded")),
            "preflightStatus": status or (
                "selected" if outcome.get("rpaAvailable") else "rejected"
            ),
        }
    )
    if timings is not None:
        enriched["timingsSec"] = timings
        for field in (
            "waveIndex",
            "waveSize",
            "waveStartedAtSec",
            "waveFinishedAtSec",
        ):
            if field in timings:
                enriched[field] = timings[field]
    return enriched


def _ready_top_materials_payload(candidate_payload, selected_materials):
    ready_payload = dict(candidate_payload)
    selected_ids = {str(material.get("materialId") or "") for material in selected_materials}
    ready_payload["materials"] = [
        dict(material)
        for material in candidate_payload["materials"]
        if str(material.get("materialId") or "") in selected_ids
    ]
    ready_payload["materialCount"] = len(ready_payload["materials"])
    return ready_payload


@contextmanager
def _rpa_preflight_deadline(timeout_sec):
    if (
        not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _handle_timeout(_signum, _frame):
        raise RpaPreflightDeadlineExceeded(
            f"RPA preflight exceeded {timeout_sec} seconds"
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_sec)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _preflight_receipt(
    args, candidate_payload, materials, selected, rejected, cancelled=None, started_at=None
):
    target_top_n = args.target_top_n or len(materials)
    return {
        "schemaVersion": 2,
        "requestedTopN": target_top_n,
        "candidateCount": len(materials),
        "candidateLimit": len(materials),
        "preflightConcurrency": args.rpa_concurrency,
        "waveBarrier": args.rpa_wave_barrier,
        "shopId": args.shop_id,
        "dateRange": {
            "type": args.date_range_type,
            "customStartDate": args.custom_start_date,
            "customEndDate": args.custom_end_date,
            "actualStartDate": candidate_payload.get("actualStartDate"),
            "actualEndDate": candidate_payload.get("actualEndDate"),
        },
        "selectedMaterials": [dict(material) for material in selected],
        "rejectedMaterials": [dict(material) for material in rejected],
        "cancelledMaterials": [dict(material) for material in (cancelled or [])],
        "satisfied": len(selected) >= target_top_n,
        "stoppedAfterSatisfied": len(selected) >= target_top_n and len(selected) < len(materials),
        "timingsSec": {"totalSec": _elapsed_since(started_at)} if started_at else {"totalSec": 0.0},
    }


def _write_preflight_artifacts(
    args, candidate_payload, materials, selected, rejected, cancelled=None, started_at=None
):
    receipt = _preflight_receipt(
        args, candidate_payload, materials, selected, rejected, cancelled, started_at
    )
    _write_json_atomically(_preflight_path(args), receipt)
    ready_payload = _ready_top_materials_payload(candidate_payload, selected)
    ready_path = _ready_top_materials_path(args)
    _write_json_atomically(ready_path, ready_payload)
    return receipt, ready_path, ready_payload


def _preflight_failure(material_id, reason, error=None):
    result = {
        "materialId": str(material_id or "").strip(),
        "rpaAvailable": False,
        "failureReason": reason,
        "videoUrl": None,
    }
    result.update(
        {
            "rpaResultStatus": (
                "pending" if reason == "rpa_request_failed" else "final_failed"
            ),
            "retryable": reason == "rpa_request_failed",
            "errorType": type(error).__name__ if error is not None else None,
            "errorMessage": str(error) if error is not None else None,
        }
    )
    return result


def _materialize_preflight_records(args, outcome, material_id, timings=None):
    records = outcome.get("records")
    records_path = outcome.get("diagnosisRecordsPath")
    if isinstance(records, list):
        if records_path:
            return records, Path(records_path)
        if timings is None:
            return records, _write_diagnosis_records(
                records, _diagnosis_records_dir(args), material_id
            )
        return records, _time_call(
            timings,
            "diagnosisRecordWriteSec",
            _write_diagnosis_records,
            records,
            _diagnosis_records_dir(args),
            material_id,
        )
    if records_path:
        return _read_diagnosis_records(records_path, material_id), Path(records_path)
    records = [
        {
            "recordType": "material_info",
            "materialId": material_id,
            "videoUrl": outcome.get("videoUrl"),
        }
    ]
    if timings is None:
        return records, _write_diagnosis_records(
            records, _diagnosis_records_dir(args), material_id
        )
    return records, _time_call(
        timings,
        "diagnosisRecordWriteSec",
        _write_diagnosis_records,
        records,
        _diagnosis_records_dir(args),
        material_id,
    )


def _verify_current_rpa_csv_artifact(args, material_id, started_at_epoch):
    """Return parsed data only for this attempt's expected RPA CSV artifact."""
    try:
        csv_path = artifact_path(_diagnosis_csv_dir(args), material_id, ".csv")
        metadata = csv_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or (
                started_at_epoch is not None
                and metadata.st_mtime < started_at_epoch
            )
        ):
            return None
        records = parse_diagnosis_csv(csv_path)
        availability = preflight_material_availability(records, material_id)
        if not availability.get("rpaAvailable"):
            return None
        return {
            "records": records,
            "diagnosisCsvPath": str(csv_path),
            "videoUrl": availability.get("videoUrl"),
            "rpaCsvVerified": True,
        }
    except (OSError, RpaAdapterError, RpaCsvError, ValueError):
        return None


def _recover_late_rpa_artifacts(
    args, material_id, failure, started_at_epoch=None, deadline=None
):
    """Recover a result that was written after the RPA watchdog fired."""
    if failure.get("failureReason") not in (
        "rpa_request_failed",
        "rpa_csv_artifact_unavailable",
    ):
        return failure
    records_dir = _diagnosis_records_dir(args)
    try:
        records_path = artifact_path(records_dir, material_id, ".json")
    except ValueError:
        return failure
    if deadline is None:
        deadline = time.monotonic() + max(
            0.0, float(getattr(args, "rpa_late_artifact_grace_sec", 5))
        )
    while True:
        try:
            verified_csv = _verify_current_rpa_csv_artifact(
                args, material_id, started_at_epoch
            )
            if verified_csv is not None:
                records = verified_csv["records"]
                records_path.parent.mkdir(parents=True, exist_ok=True)
                _write_diagnosis_records(records, records_path.parent, material_id)
                return {
                    **failure,
                    "rpaAvailable": True,
                    **verified_csv,
                    "diagnosisRecordsPath": str(records_path),
                    "lateArtifactRecovered": True,
                }
        except (OSError, RpaAdapterError, RpaCsvError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return failure
        time.sleep(0.25)


def _rpa_multiprocessing_context_name():
    return multiprocessing_context_name()


def _fetch_material_preflight_worker(params):
    adapter_params = {
        key: value
        for key, value in params.items()
        if key != "diagnosis_records_dir"
    }
    return fetch_material_preflight(**adapter_params)


def _fetch_material_preflight_isolated(
    params,
    timeout_sec,
    worker=None,
    soft_timeout_sec=None,
    late_artifact_grace_sec=0,
):
    worker = _fetch_material_preflight_worker if worker is None else worker
    return run_isolated_preflight(
        params,
        timeout_sec,
        worker,
        _preflight_failure,
        persist_records=_write_diagnosis_records,
        soft_timeout_sec=soft_timeout_sec,
        late_artifact_grace_sec=late_artifact_grace_sec,
    )


def _call_mock_preflight_with_timeout(params, timeout_sec):
    outcome = []
    error = []

    def invoke():
        try:
            outcome.append(fetch_material_preflight(**params))
        except BaseException as exc:
            error.append(exc)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout_sec)
    if worker.is_alive():
        raise RpaPreflightDeadlineExceeded(
            f"RPA preflight exceeded {timeout_sec} seconds"
        )
    if error:
        raise error[0]
    return outcome[0]


def _fetch_material_preflight_for_cli(args, material_id, *, recover_late_artifacts=True):
    started_at_epoch = time.time()
    params = {
        "shop_id": args.shop_id,
        "material_id": material_id,
        "date_range_type": args.date_range_type,
        "custom_start_date": args.custom_start_date,
        "custom_end_date": args.custom_end_date,
        "diagnosis_csv_dir": _diagnosis_csv_dir(args),
        "diagnosis_records_dir": _diagnosis_records_dir(args),
        "timeout_sec": args.rpa_preflight_timeout_sec,
    }
    if hasattr(fetch_material_preflight, "mock_calls"):
        if threading.current_thread() is threading.main_thread():
            with _rpa_preflight_deadline(args.rpa_preflight_timeout_sec):
                outcome = fetch_material_preflight(**params)
        else:
            outcome = _call_mock_preflight_with_timeout(
                params, args.rpa_preflight_timeout_sec
            )
    else:
        outcome = _fetch_material_preflight_isolated(
            params,
            args.rpa_preflight_timeout_sec,
            soft_timeout_sec=args.rpa_preflight_soft_timeout_sec,
            late_artifact_grace_sec=(
                args.rpa_late_artifact_grace_sec if recover_late_artifacts else 0
            ),
        )
    if recover_late_artifacts and not outcome.get("rpaAvailable"):
        outcome = _recover_late_rpa_artifacts(
            args, material_id, outcome, started_at_epoch=started_at_epoch
        )
    return outcome


def _rpa_recovery_attempt(number, outcome, source="request"):
    return {
        "attempt": number,
        "source": source,
        "rpaResultStatus": outcome.get("rpaResultStatus"),
        "failureReason": outcome.get("failureReason"),
        "errorType": outcome.get("errorType"),
        "errorMessage": outcome.get("errorMessage"),
    }


def _resolved_rpa_outcome(outcome, attempts):
    result = dict(outcome)
    result.update(
        {
            "rpaResultStatus": "available",
            "retryable": False,
            "errorType": None,
            "errorMessage": None,
            "rpaRecoveryAttempts": attempts,
        }
    )
    return result


def _recover_stream_rpa_result(
    args, material_id, outcome, *, started_at_epoch, fetch_once
):
    """Resolve a pending request using one artifact grace budget and bounded retries."""
    try:
        max_attempts = max(1, int(getattr(args, "rpa_result_recovery_attempts", 2)))
    except (TypeError, ValueError):
        max_attempts = 2
    deadline = time.monotonic() + max(
        0.0, float(getattr(args, "rpa_late_artifact_grace_sec", 5))
    )
    attempts = []

    for attempt_number in range(1, max_attempts + 1):
        outcome = dict(outcome)
        if outcome.get("rpaAvailable"):
            verified_csv = _verify_current_rpa_csv_artifact(
                args, material_id, started_at_epoch
            )
            if verified_csv is not None:
                outcome.update(verified_csv)
                attempts.append(_rpa_recovery_attempt(attempt_number, outcome))
                return _resolved_rpa_outcome(outcome, attempts)
            outcome.update(
                {
                    "rpaAvailable": False,
                    "failureReason": "rpa_csv_artifact_unavailable",
                    "rpaResultStatus": "pending",
                    "retryable": True,
                    "errorType": "RpaCsvArtifactUnavailable",
                    "errorMessage": (
                        "RPA response did not produce a verified current CSV artifact"
                    ),
                }
            )

        outcome.setdefault(
            "rpaResultStatus",
            "pending"
            if outcome.get("failureReason") in (
                "rpa_request_failed",
                "missing_video_url",
            )
            else "final_failed",
        )
        outcome.setdefault(
            "retryable",
            outcome.get("failureReason") in (
                "rpa_request_failed",
                "missing_video_url",
            ),
        )
        attempts.append(_rpa_recovery_attempt(attempt_number, outcome))
        recovered = _recover_late_rpa_artifacts(
            args,
            material_id,
            outcome,
            started_at_epoch=started_at_epoch,
            deadline=deadline,
        )
        if recovered.get("rpaAvailable"):
            attempts.append(
                _rpa_recovery_attempt(attempt_number, recovered, "late_artifact")
            )
            return _resolved_rpa_outcome(recovered, attempts)
        if not outcome["retryable"] or attempt_number == max_attempts:
            break
        outcome = fetch_once()

    outcome = dict(outcome)
    outcome.update(
        {
            "rpaResultStatus": "final_failed",
            "retryable": False,
            "rpaRecoveryAttempts": attempts,
        }
    )
    if outcome.get("failureReason") == "rpa_request_failed":
        outcome["failureReason"] = "rpa_csv_artifact_unavailable"
    return outcome


def _run_rpa_preflight(args, candidate_payload):
    def fetch_material(args_, material_id):
        started_at_epoch = time.time()

        def fetch_once():
            return _fetch_material_preflight_for_cli(
                args_, material_id, recover_late_artifacts=False
            )

        return _recover_stream_rpa_result(
            args_,
            material_id,
            fetch_once(),
            started_at_epoch=started_at_epoch,
            fetch_once=fetch_once,
        )

    def materialize(args_, outcome, material_id):
        records, path = _materialize_preflight_records(args_, outcome, material_id)
        if outcome.get("diagnosisCsvPath"):
            outcome["diagnosisCsvPath"] = _run_relative_path(
                args_, outcome["diagnosisCsvPath"]
            )
        return records, _run_relative_path(args_, path)

    def entry(material, outcome, status, timings=None):
        result = _material_entry_with_preflight(material, outcome, status, timings)
        return result

    def write_artifacts(args_, candidate, materials, selected, rejected, cancelled, started):
        return _write_preflight_artifacts(
            args_, candidate, materials, selected, rejected, cancelled, started
        )

    return rpa_preflight.run_preflight(
        args,
        candidate_payload,
        fetch_material=fetch_material,
        materialize_records=materialize,
        material_entry=entry,
        failure_entry=_preflight_failure,
        write_artifacts=write_artifacts,
        elapsed=_elapsed_since,
    )


def _stream_entry(material, rank):
    return rpa_batch_runner.stream_entry(material, rank, _empty_material_timings)


def _stream_public_entry(entry):
    return rpa_batch_runner.public_entry(entry)


def _stream_ordered(entries):
    return rpa_batch_runner.ordered(entries, _stream_public_entry)


def _stream_accounted_indices(*entry_maps):
    return rpa_batch_runner.accounted_indices(*entry_maps)


def _new_streaming_batch_manifest(args, candidates, entries, submitted, selected, rejected, cancelled):
    return schemas.streaming_manifest(
        args, candidates, entries, submitted, selected, rejected, cancelled,
        public_entry=_stream_public_entry,
        ordered=_stream_ordered,
        stage_counts=stage_counts,
    )


def _resolve_run_retained_path(args, path):
    retained_path = Path(path)
    if retained_path.is_absolute():
        return retained_path
    output_parent = Path(args.output_dir).parent
    run_relative = output_parent / retained_path
    if run_relative.exists():
        return run_relative
    return retained_path


def _load_streaming_resume_manifest(args, candidates):
    return schemas.load_streaming_resume(
        _batch_manifest_path(args), args, candidates, RpaAdapterError
    )


def _stream_entry_from_existing_manifest(manifest):
    entries = {}
    if not isinstance(manifest, dict):
        return entries
    materials = manifest.get("materials")
    if not isinstance(materials, list):
        return entries
    for index, entry in enumerate(materials):
        if isinstance(entry, dict):
            entries[index] = dict(entry)
    return entries


def _existing_stream_records(args, material_id, entry):
    candidates = []
    records_path = entry.get("diagnosisRecordsPath") if isinstance(entry, dict) else None
    if isinstance(records_path, str) and records_path.strip():
        candidates.append(_resolve_run_retained_path(args, records_path))
    candidates.append(_diagnosis_records_dir(args) / f"{material_id}.json")
    seen = set()
    for candidate in candidates:
        candidate = Path(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if not candidate.is_file():
            continue
        try:
            return _read_diagnosis_records(candidate, material_id), candidate
        except RpaAdapterError:
            continue
    return None, None


def _existing_stream_summary_entry(args, entry):
    material_id = entry["materialId"]
    candidates = []
    summary_json = entry.get("summaryJson")
    summary_md = entry.get("summaryMd")
    if isinstance(summary_json, str) and isinstance(summary_md, str):
        candidates.append(
            (
                _resolve_run_retained_path(args, summary_json),
                _resolve_run_retained_path(args, summary_md),
            )
        )
    candidates.append(
        (
            Path(args.output_dir) / material_id / "summary.json",
            Path(args.output_dir) / material_id / "summary.md",
        )
    )
    for json_path, markdown_path in candidates:
        candidate = dict(entry)
        candidate.update(
            {
                "status": "succeeded",
                "postprocessStatus": "succeeded",
                "postprocessSucceeded": True,
                "summaryJson": str(json_path),
                "summaryMd": str(markdown_path),
            }
        )
        if _material_success_is_valid(material_id, candidate):
            return candidate
    return None


def _resume_streaming_state(args, candidates, entries):
    if not args.resume:
        return {}, {}, {}, {}, []
    manifest = _load_streaming_resume_manifest(args, candidates)
    if manifest is not None:
        for index, entry in _stream_entry_from_existing_manifest(manifest).items():
            if index in entries:
                entries[index].update(entry)

    submitted = {}
    selected = {}
    rejected = {}
    cancelled = {}
    postprocess_queue = []
    for index in sorted(entries):
        entry = dict(entries[index])
        material_id = entry["materialId"]
        records, records_path = _existing_stream_records(args, material_id, entry)
        if records is not None:
            entry.update(
                {
                    "rpaStatus": "selected",
                    "postprocessStatus": "queued",
                    "status": "running",
                    "failureReason": None,
                    "errorType": None,
                    "errorMessage": None,
                    "diagnosisRecordsPath": str(records_path),
                    "recordsPersisted": True,
                    "records": records,
                }
            )
            summary_entry = _existing_stream_summary_entry(args, entry)
            if summary_entry is not None:
                entry.update(summary_entry)
            entries[index] = entry
            submitted[index] = entry
            selected[index] = entry
            if entry["postprocessStatus"] != "succeeded":
                if entry.get("postprocessAttemptCount", 0) < 2:
                    postprocess_queue.append(index)
                else:
                    entry.update(
                        {
                            "postprocessStatus": "failed",
                            "postprocessSucceeded": False,
                            "status": "failed",
                        }
                    )
            continue

        if entry.get("rpaStatus") == "rejected":
            entry.update({"status": "failed", "postprocessStatus": "not_started"})
            entries[index] = entry
            submitted[index] = entry
            rejected[index] = entry
            continue

        if entry.get("rpaStatus") == "cancelled" or entry.get("status") == "cancelled":
            entries[index] = entry
            cancelled[index] = entry
            continue

        entries[index] = _stream_entry(candidates[index], index + 1)
    return submitted, selected, rejected, cancelled, postprocess_queue


def _attempt_stream_rpa(args, entry):
    started_at = time.perf_counter()
    started_at_epoch = time.time()
    timings = _empty_material_timings()
    material_id = entry["materialId"]
    def fetch_once():
        request_started_at = time.perf_counter()
        try:
            return _fetch_material_preflight_for_cli(
                args, material_id, recover_late_artifacts=False
            )
        except RpaPreflightDeadlineExceeded as exc:
            return _preflight_failure(material_id, "rpa_request_failed", exc)
        except RpaAdapterError as exc:
            return _preflight_failure(material_id, "rpa_request_failed", exc)
        except Exception as exc:
            return _preflight_failure(material_id, "unknown_preflight_failure", exc)
        finally:
            timings["rpaFetchSec"] = round(
                timings["rpaFetchSec"] + _elapsed_since(request_started_at), 6
            )

    outcome = _recover_stream_rpa_result(
        args,
        material_id,
        fetch_once(),
        started_at_epoch=started_at_epoch,
        fetch_once=fetch_once,
    )

    updated = dict(entry)
    updated["timingsSec"] = timings
    if not outcome.get("rpaAvailable"):
        updated.update(
            {
                "rpaStatus": "rejected",
                "status": "failed",
                "failureReason": outcome.get("failureReason"),
                "rpaResultStatus": outcome.get("rpaResultStatus"),
                "rpaRecoveryAttempts": outcome.get("rpaRecoveryAttempts", []),
                "errorType": outcome.get("errorType"),
                "errorMessage": outcome.get("errorMessage"),
                "rpaSoftTimeoutExceeded": bool(outcome.get("softTimeoutExceeded")),
                "rpaHardTimeoutExceeded": bool(outcome.get("hardTimeoutExceeded")),
            }
        )
        timings["totalSec"] = _elapsed_since(started_at)
        return updated

    try:
        records, records_path = _materialize_preflight_records(
            args, outcome, material_id, timings
        )
    except RpaAdapterError as exc:
        updated.update(
            {
                "rpaStatus": "rejected",
                "status": "failed",
                "failureReason": "rpa_payload_invalid",
                "rpaResultStatus": "final_failed",
                "rpaRecoveryAttempts": outcome.get("rpaRecoveryAttempts", []),
                "errorType": type(exc).__name__,
                "errorMessage": str(exc),
            }
        )
        timings["totalSec"] = _elapsed_since(started_at)
        return updated

    updated.update(
        {
            "rpaStatus": "selected",
            "postprocessStatus": "queued",
            "status": "running",
            "diagnosisRecordsPath": _run_relative_path(args, records_path),
            "diagnosisCsvPath": (
                _run_relative_path(args, outcome["diagnosisCsvPath"])
                if outcome.get("diagnosisCsvPath")
                else None
            ),
            "records": records,
            "recordsPersisted": True,
            "rpaCsvVerified": bool(outcome.get("rpaCsvVerified")),
            "lateArtifactRecovered": bool(outcome.get("lateArtifactRecovered")),
            "rpaResultStatus": outcome.get("rpaResultStatus"),
            "rpaRecoveryAttempts": outcome.get("rpaRecoveryAttempts", []),
            "errorType": outcome.get("errorType"),
            "errorMessage": outcome.get("errorMessage"),
            "rpaSoftTimeoutExceeded": bool(outcome.get("softTimeoutExceeded")),
            "rpaHardTimeoutExceeded": bool(outcome.get("hardTimeoutExceeded")),
        }
    )
    timings["totalSec"] = _elapsed_since(started_at)
    return updated


def _attempt_stream_postprocess(args, entry):
    started_at = time.perf_counter()
    timings = dict(entry["timingsSec"])
    material_id = entry["materialId"]
    substage = "records"
    try:
        records = entry.get("records")
        if records is None:
            records, _records_path = _existing_stream_records(args, material_id, entry)
        if records is None:
            raise RpaAdapterError("persisted RPA diagnosis records are unavailable")
        video_url = find_material_video_url(records, material_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = artifact_path(temp_dir, material_id, ".mp4")
            substage = "download"
            _time_call(timings, "videoDownloadSec", download_video, video_url, video_path)
            substage = "frame_extraction"
            result = _time_call(
                timings,
                "frameExtractionSec",
                process_records,
                records,
                f"rpa://{FUNCTION_CODE}/{material_id}",
                video_path,
                args.output_dir,
                ffmpeg_bin=args.ffmpeg_bin,
                ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
                video_source="rpa-temporary-download",
            )
    except HANDLED_MATERIAL_ERRORS as exc:
        timings["totalSec"] = _elapsed_since(started_at)
        return {
            "result": None,
            "error": exc,
            "timingsSec": timings,
            "substage": substage,
        }
    timings["totalSec"] = _elapsed_since(started_at)
    return {
        "result": result,
        "error": None,
        "timingsSec": timings,
        "substage": "completed",
    }


def _merge_stream_postprocess_timings(entry_timings, attempt_timings):
    merged = dict(entry_timings)
    merged.update(attempt_timings)
    try:
        rpa_total = float(entry_timings.get("totalSec") or 0.0)
    except (TypeError, ValueError):
        rpa_total = 0.0
    try:
        postprocess_total = float(
            attempt_timings.get("postprocessSec", attempt_timings.get("totalSec", 0.0))
            or 0.0
        )
    except (TypeError, ValueError):
        postprocess_total = 0.0
    end_to_end = round(rpa_total + postprocess_total, 6)
    merged["postprocessSec"] = round(postprocess_total, 6)
    merged["endToEndSec"] = end_to_end
    merged["totalSec"] = end_to_end
    return merged


def _run_streaming_rpa_batch(args, candidate_payload):
    manifest_path = _batch_manifest_path(args)
    manifest, failures, last_result = rpa_batch_runner.run_streaming_batch(
        args,
        candidate_payload,
        stream_entry=_stream_entry,
        resume_state=_resume_streaming_state,
        attempt_rpa=_attempt_stream_rpa,
        attempt_postprocess=_attempt_stream_postprocess,
        merge_timings=_merge_stream_postprocess_timings,
        new_manifest=_new_streaming_batch_manifest,
        write_json=_write_json_atomically,
        manifest_path=manifest_path,
        ready_payload=_ready_top_materials_payload,
        ready_path=_ready_top_materials_path(args),
        entry_result=_entry_result,
        elapsed=_elapsed_since,
        accounted_indices=_stream_accounted_indices,
        ordered=_stream_ordered,
    )
    return manifest_path, last_result, failures


def _batch_manifest_path(args):
    if args.batch_manifest:
        return Path(args.batch_manifest)
    return Path(args.output_dir) / BATCH_MANIFEST_NAME


def _streaming_lock_path(args):
    return Path(args.output_dir) / ".batch-run.lock"


def _manifest_has_unfinished_streaming_work(manifest):
    if not isinstance(manifest, dict):
        return False
    for entry in manifest.get("materials", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "running" or entry.get("rpaStatus") == "submitted":
            return True
    for key in ("submittedMaterials",):
        for entry in manifest.get(key, []):
            if isinstance(entry, dict) and (
                entry.get("status") == "running"
                or entry.get("rpaStatus") == "submitted"
            ):
                return True
    return False


def _acquire_streaming_lock(args):
    manifest_path = _batch_manifest_path(args)
    existing = read_json(manifest_path)
    requested_run_id = existing.get("runId") if args.resume and isinstance(existing, dict) else None
    try:
        lock = acquire_run_lock(
            _streaming_lock_path(args), manifest_path, run_id=requested_run_id
        )
    except BatchLockError as exc:
        if not exc.stale:
            raise RpaAdapterError(str(exc)) from exc
        if _manifest_has_unfinished_streaming_work(existing) and not args.resume:
            raise RpaAdapterError(
                "stale streaming batch requires --resume or repair-manifest before retry"
            ) from exc
        try:
            _streaming_lock_path(args).unlink()
        except FileNotFoundError:
            pass
        lock = acquire_run_lock(
            _streaming_lock_path(args), manifest_path, run_id=requested_run_id
        )
    args.run_id = lock.payload["runId"]
    return lock


def _new_batch_manifest(args, material_ids):
    return schemas.build_batch_manifest(args, material_ids, _empty_material_timings)


def _load_resume_manifest(path, expected_manifest):
    return schemas.load_batch_resume(path, expected_manifest, RpaAdapterError)


def _material_success_is_valid(material_id, entry):
    errors, _warnings = _material_output_issues(material_id, entry)
    return not errors


def _entry_result(entry):
    return (
        Path(entry["summaryJson"]).parent,
        Path(entry["summaryJson"]),
        Path(entry["summaryMd"]),
    )


def _empty_material_timings():
    return {field: 0.0 for field in MATERIAL_TIMING_FIELDS}


def _elapsed_since(start_time):
    return round(max(0.0, time.perf_counter() - start_time), 6)


def _time_call(timings, field, function, *args, **kwargs):
    started_at = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        timings[field] = _elapsed_since(started_at)


def _set_last_material_timings(timings):
    _MATERIAL_TIMING_STATE.timings = timings


def _pop_last_material_timings():
    timings = getattr(_MATERIAL_TIMING_STATE, "timings", None)
    if hasattr(_MATERIAL_TIMING_STATE, "timings"):
        delattr(_MATERIAL_TIMING_STATE, "timings")
    return timings


def _timings_or_fallback(started_at):
    timings = _pop_last_material_timings()
    if timings is None:
        timings = _empty_material_timings()
        timings["totalSec"] = _elapsed_since(started_at)
    elif timings.get("totalSec", 0.0) <= 0:
        timings["totalSec"] = _elapsed_since(started_at)
    return timings


def _start_material_attempt(entry):
    entry.update(
        {
            "status": "running",
            "attempts": entry.get("attempts", 0) + 1,
            "errorType": None,
            "errorMessage": None,
            "timingsSec": _empty_material_timings(),
        }
    )


def _attempt_rpa_material(args, material_id):
    started_at = time.perf_counter()
    try:
        result = _process_rpa_material(args, material_id)
        return {
            "materialId": material_id,
            "result": result,
            "error": None,
            "timingsSec": _timings_or_fallback(started_at),
        }
    except HANDLED_MATERIAL_ERRORS as exc:
        return {
            "materialId": material_id,
            "result": None,
            "error": exc,
            "timingsSec": _timings_or_fallback(started_at),
        }


def _apply_material_attempt(manifest, material_id, attempt):
    entry = manifest["materials"][material_id]
    entry["timingsSec"] = attempt["timingsSec"]
    error = attempt["error"]
    if error is not None:
        entry.update(
            {
                "status": "failed",
                "errorType": type(error).__name__,
                "errorMessage": (
                    f"{type(error).__name__} while processing material {material_id}"
                ),
            }
        )
        return

    result_dir, summary_json, summary_md = attempt["result"]
    entry.update(
        {
            "status": "succeeded",
            "summaryJson": str(summary_json),
            "summaryMd": str(summary_md),
        }
    )
    if not _material_success_is_valid(material_id, entry):
        entry.update(
            {
                "status": "failed",
                "errorType": "InputDataError",
                "errorMessage": (
                    f"InputDataError while processing material {material_id}"
                ),
            }
        )


def _iter_pending_material_ids(args, manifest, material_ids):
    for material_id in material_ids:
        entry = manifest["materials"][material_id]
        if args.resume and _material_success_is_valid(material_id, entry):
            continue
        yield material_id


def _verify_rpa_batch(args, material_ids):
    return batch_verification.verify_batch(
        args,
        material_ids,
        manifest_path=_batch_manifest_path(args),
        load_resume_manifest=_load_resume_manifest,
        new_batch_manifest=_new_batch_manifest,
        material_output_issues_fn=_material_output_issues,
        write_json=_write_json_atomically,
        verification_name=BATCH_VERIFICATION_NAME,
        error_type=RpaAdapterError,
    )


def _verify_streaming_rpa_batch(args, material_ids, manifest):
    return batch_verification.verify_streaming_batch(
        args,
        material_ids,
        manifest,
        material_output_issues_fn=_material_output_issues,
        write_json=_write_json_atomically,
        verification_name=BATCH_VERIFICATION_NAME,
    )


STREAMING_BATCH_RUNNING = "RUNNING"
STREAMING_BATCH_COMPLETED = "COMPLETED"
STREAMING_BATCH_INVALID_MANIFEST = "INVALID_MANIFEST"
STREAMING_BATCH_NOT_FOUND = "NOT_FOUND"


def _streaming_batch_status(args):
    manifest_path = _batch_manifest_path(args)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return STREAMING_BATCH_NOT_FOUND, f"streaming batch manifest is missing: {manifest_path}"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return STREAMING_BATCH_INVALID_MANIFEST, (
            f"streaming batch manifest is unreadable: {manifest_path}: {exc}"
        )
    try:
        manifest = validate_manifest(manifest)
    except SchemaError as exc:
        return STREAMING_BATCH_INVALID_MANIFEST, str(exc)
    material_ids = manifest["materialIds"]
    accounted_ids = {
        str(entry.get("materialId") or "").strip()
        for field in ("selectedMaterials", "rejectedMaterials", "cancelledMaterials")
        for entry in manifest[field]
        if isinstance(entry, dict) and str(entry.get("materialId") or "").strip()
    }
    if accounted_ids >= set(material_ids):
        return STREAMING_BATCH_COMPLETED, None
    return STREAMING_BATCH_RUNNING, None


def _run_rpa_batch(args, material_ids):
    return rpa_batch_runner.run_legacy_batch(
        args,
        material_ids,
        manifest_path=_batch_manifest_path(args),
        new_manifest=_new_batch_manifest,
        load_resume_manifest=_load_resume_manifest,
        iter_pending=_iter_pending_material_ids,
        start_attempt=_start_material_attempt,
        attempt_material=_attempt_rpa_material,
        apply_attempt=_apply_material_attempt,
        write_json=_write_json_atomically,
        entry_result=_entry_result,
        elapsed=_elapsed_since,
    )


def _process_rpa_material(args, material_id):
    records = getattr(args, "rpa_preflight_records", {}).get(material_id)
    result, timings = material_processing.process_rpa_material(
        args,
        material_id,
        preflight_records=records,
        fetch_records=fetch_material_diagnosis,
        diagnosis_csv_dir=_diagnosis_csv_dir(args),
        diagnosis_records_dir=args.diagnosis_records_dir,
        write_records=_write_diagnosis_records,
        find_video=find_material_video_url,
        download_video=download_video,
        process_records=process_records,
        artifact_path=artifact_path,
        function_code=FUNCTION_CODE,
    )
    _set_last_material_timings(timings)
    return result


def _load_preflight_records(args, candidate_payload, material_ids):
    try:
        receipt_path = Path(args.rpa_preflight_json)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaAdapterError(f"unable to read RPA preflight receipt: {exc}") from exc
    try:
        validate_preflight_receipt(
            receipt, args=args, candidate_payload=candidate_payload, material_ids=material_ids
        )
    except SchemaError as exc:
        raise RpaAdapterError(str(exc)) from exc
    selected = receipt["selectedMaterials"]
    by_id = {str(item.get("materialId") or "").strip(): item for item in selected if isinstance(item, dict)}
    records_by_id = {}
    receipt_run_dir = receipt_path.parent.parent
    for material_id in material_ids:
        entry = by_id.get(material_id)
        try:
            if not entry or entry.get("preflightStatus") != "selected":
                raise RpaAdapterError(f"RPA preflight receipt does not select material {material_id}")
            relative_path = entry.get("diagnosisRecordsPath")
            if not isinstance(relative_path, str) or not relative_path:
                raise RpaAdapterError(f"RPA preflight records path is missing for material {material_id}")
            records = json.loads((receipt_run_dir / relative_path).read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise RpaAdapterError(f"RPA preflight records must be a list for material {material_id}")
            find_material_video_url(records, material_id)
        except (OSError, UnicodeError, json.JSONDecodeError, RpaAdapterError) as exc:
            if args.allow_rpa_refetch:
                continue
            raise RpaAdapterError(str(exc)) from exc
        records_by_id[material_id] = records
    return records_by_id


def process_local_input(args):
    """Process local JSON/CSV input without entering the RPA workflow."""
    if args.input_csv:
        records = parse_diagnosis_csv(args.input_csv)
        material_id = ""
        if args.diagnosis_records_dir or not args.video:
            material_id, _points = parse_click_series(records)
        if args.diagnosis_records_dir:
            _write_diagnosis_records(
                records,
                args.diagnosis_records_dir,
                material_id or Path(args.video).stem,
            )
        if args.video:
            return process_records(
                records,
                args.input_csv,
                args.video,
                args.output_dir,
                ffmpeg_bin=args.ffmpeg_bin,
                ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
            )

        video_url = find_material_video_url(records, material_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / (
                f"{material_id or Path(args.input_csv).stem}.mp4"
            )
            download_video(video_url, video_path)
            return process_records(
                records,
                args.input_csv,
                video_path,
                args.output_dir,
                ffmpeg_bin=args.ffmpeg_bin,
                ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
            )

    return process_local(
        args.input_json,
        args.video,
        args.output_dir,
        ffmpeg_bin=args.ffmpeg_bin,
        ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
    )


def main(argv=None):
    parser = build_parser()
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(parsed_argv)
    resolve_concurrency(args, parsed_argv)
    try:
        if args.list_shop_accounts:
            return _list_shop_accounts()
        if (args.resume or args.batch_manifest or args.verify) and not (
            args.source == "rpa" and args.top_materials_json
        ):
            parser.error(
                "--resume, --batch-manifest, and --verify require --source rpa "
                "with --top-materials-json"
            )
        if args.verify and args.resume:
            parser.error("--verify and --resume cannot be used together")
        if args.source == "rpa":
            if not args.output_dir:
                parser.error("--output-dir is required")
            if not args.date_range_type or not (
                args.material_id or args.top_materials_json
            ):
                parser.error(
                    "--date-range-type and either --material-id "
                    "or --top-materials-json are required for --source rpa"
                )
            if args.date_range_type == "CUSTOM" and not (
                args.custom_start_date and args.custom_end_date
            ):
                parser.error(
                    "CUSTOM requires --custom-start-date and "
                    "--custom-end-date"
                )
            if args.preflight_only and not args.top_materials_json:
                parser.error("--preflight-only requires --top-materials-json")
            if args.preflight_only and not args.target_top_n:
                parser.error("--preflight-only requires --target-top-n")
            if args.target_top_n and not args.top_materials_json:
                parser.error("--target-top-n requires --top-materials-json")
            args.shop_id = _resolve_shop_id(args)
            if args.top_materials_json:
                top_materials_payload, material_ids = _load_top_materials(
                    args.top_materials_json,
                    args.date_range_type,
                    args.custom_start_date,
                    args.custom_end_date,
                )
            else:
                top_materials_payload = None
                material_ids = [args.material_id]
            if args.top_materials_json:
                if args.target_top_n and not args.preflight_only:
                    if args.rpa_preflight_json:
                        parser.error(
                            "--rpa-preflight-json is incompatible with streaming --target-top-n"
                        )
                    if args.verify:
                        streaming_status, streaming_error = _streaming_batch_status(args)
                        if streaming_status == STREAMING_BATCH_RUNNING:
                            parser.error("--verify is not available for a running streaming batch")
                        if streaming_status != STREAMING_BATCH_COMPLETED:
                            raise RpaAdapterError(streaming_error)
                        verification_path, receipt = _verify_rpa_batch(
                            args, material_ids
                        )
                        print(f"batch_verification={verification_path}")
                        return (
                            0
                            if receipt["valid"]
                            else BATCH_VERIFICATION_FAILURE_EXIT_CODE
                        )
                    with _acquire_streaming_lock(args):
                        manifest_path, result, failed = _run_streaming_rpa_batch(
                            args, top_materials_payload
                        )
                    manifest_snapshot = read_json(manifest_path) or {}
                    print(
                        "stage_counts="
                        + json.dumps(
                            manifest_snapshot.get("stageCounts", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    if failed:
                        print(f"batch_manifest={manifest_path}", file=sys.stderr)
                        return BATCH_PARTIAL_FAILURE_EXIT_CODE
                    result_dir, json_path, markdown_path = result
                elif _preflight_enabled(args):
                    receipt, ready_path, ready_payload = _run_rpa_preflight(
                        args, top_materials_payload
                    )
                    print(f"rpa_preflight={_preflight_path(args)}")
                    print(f"rpa_ready_top_materials_json={ready_path}")
                    material_ids = [
                        str(material["materialId"])
                        for material in ready_payload["materials"]
                    ]
                    if not receipt["satisfied"]:
                        raise RpaAdapterError(
                            "RPA preflight did not satisfy requested Top N"
                        )
                    if args.preflight_only:
                        return 0
                if not args.target_top_n or args.preflight_only:
                    if args.rpa_preflight_json:
                        args.rpa_preflight_records = _load_preflight_records(
                            args, top_materials_payload, material_ids
                        )
                    if args.verify:
                        verification_path, receipt = _verify_rpa_batch(
                            args, material_ids
                        )
                        print(f"batch_verification={verification_path}")
                        return (
                            0
                            if receipt["valid"]
                            else BATCH_VERIFICATION_FAILURE_EXIT_CODE
                        )
                    if not args.preflight_only:
                        manifest_path, result, failures = _run_rpa_batch(
                            args, material_ids
                        )
                        if failures:
                            print(f"batch_manifest={manifest_path}", file=sys.stderr)
                            return BATCH_PARTIAL_FAILURE_EXIT_CODE
                        result_dir, json_path, markdown_path = result
            else:
                result_dir, json_path, markdown_path = _process_rpa_material(
                    args, material_ids[0]
                )

        else:
            if not args.output_dir:
                parser.error("--output-dir is required")
            if not (args.input_json or args.input_csv) or (
                args.input_json and not args.video
            ):
                parser.error(
                    "--input-json requires --video; --input-csv can use "
                    "material_info.videoUrl when --video is omitted"
                )
            result_dir, json_path, markdown_path = process_local_input(args)
    except InputDataError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FfmpegError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except RpaAdapterError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except RpaCsvError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"result_dir={result_dir}")
    print(f"summary_json={json_path}")
    print(f"summary_md={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
