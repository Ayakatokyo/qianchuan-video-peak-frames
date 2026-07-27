#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
import uuid

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
    find_material_video_url,
    list_accounts,
)
from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv


DATE_RANGE_TYPES = (
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_15_DAYS",
    "LAST_WEEK",
    "THIS_MONTH",
    "LAST_MONTH",
    "CUSTOM",
)

BATCH_MANIFEST_NAME = "batch-manifest.json"
BATCH_MANIFEST_VERSION = 1
BATCH_PARTIAL_FAILURE_EXIT_CODE = 5
BATCH_VERIFICATION_NAME = "batch-verification.json"
BATCH_VERIFICATION_FAILURE_EXIT_CODE = 6
MATERIAL_TIMING_FIELDS = (
    "rpaFetchSec",
    "diagnosisRecordWriteSec",
    "videoDownloadSec",
    "frameExtractionSec",
    "totalSec",
)
HANDLED_MATERIAL_ERRORS = (InputDataError, FfmpegError, RpaAdapterError, RpaCsvError)
_MATERIAL_TIMING_STATE = threading.local()


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
        raise argparse.ArgumentTypeError(
            "concurrency must be a positive integer"
        )
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
    parser.add_argument(
        "--ffmpeg-timeout-sec",
        type=_positive_timeout,
        default=DEFAULT_FFMPEG_TIMEOUT_SEC,
    )
    parser.add_argument("--shop-id")
    parser.add_argument("--material-id")
    parser.add_argument("--top-materials-json")
    parser.add_argument("--diagnosis-records-dir")
    parser.add_argument("--batch-manifest")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--concurrency", type=_positive_int, default=1)
    parser.add_argument("--date-range-type", choices=DATE_RANGE_TYPES)
    parser.add_argument("--custom-start-date")
    parser.add_argument("--custom-end-date")
    parser.add_argument("--list-shop-accounts", action="store_true")
    return parser


def _normalise_account_rows(payload):
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


def _format_account_rows(accounts):
    return "\n".join(
        f"{item['account'] or '<unknown-account>'}\t{item['shop_id']}"
        for item in accounts
    )


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


def _top_material_ids(path, date_range_type, custom_start_date, custom_end_date):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaAdapterError(f"unable to read top materials JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RpaAdapterError("top materials JSON must be an object")
    actual_start_date = payload.get("actualStartDate")
    actual_end_date = payload.get("actualEndDate")
    if not isinstance(actual_start_date, str) or not isinstance(actual_end_date, str):
        raise RpaAdapterError(
            "top materials JSON requires actualStartDate and actualEndDate"
        )
    if date_range_type == "CUSTOM" and (
        actual_start_date != custom_start_date or actual_end_date != custom_end_date
    ):
        raise RpaAdapterError(
            "top materials JSON date range does not match the requested CUSTOM range"
        )
    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise RpaAdapterError(
            "top materials JSON must contain a nonempty materials list"
        )

    material_ids = []
    for material in materials:
        if not isinstance(material, dict):
            raise RpaAdapterError("top materials entries must be objects")
        material_id = str(material.get("materialId") or "").strip()
        if not material_id:
            raise RpaAdapterError("top materials entries require materialId")
        if material_id in material_ids:
            raise RpaAdapterError("top materials entries must not repeat materialId")
        material_ids.append(material_id)
    if payload.get("materialCount") != len(material_ids):
        raise RpaAdapterError(
            "top materials JSON materialCount does not match materials"
        )
    return material_ids


def _write_diagnosis_records(records, records_dir, material_id):
    target_dir = Path(records_dir)
    target = target_dir / f"{material_id}.json"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpaAdapterError(
            f"unable to create diagnosis records directory {target_dir}: {exc}"
        ) from exc

    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError) as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise RpaAdapterError(
            f"unable to write diagnosis records for material {material_id}: {exc}"
        ) from exc
    return target


def _diagnosis_csv_dir(args):
    if args.diagnosis_records_dir:
        return Path(args.diagnosis_records_dir).parent / "diagnosis-csv"
    return Path(args.output_dir) / "diagnosis-csv"


def _write_json_atomically(path, payload):
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpaAdapterError(
            f"unable to create batch manifest directory {target.parent}: {exc}"
        ) from exc

    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError) as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise RpaAdapterError(
            f"unable to write batch manifest {target}: {exc}"
        ) from exc
    return target


def _batch_manifest_path(args):
    if args.batch_manifest:
        return Path(args.batch_manifest)
    return Path(args.output_dir) / BATCH_MANIFEST_NAME


def _new_batch_manifest(args, material_ids):
    return {
        "schemaVersion": BATCH_MANIFEST_VERSION,
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
                "timingsSec": _empty_material_timings(),
            }
            for material_id in material_ids
        },
    }


def _load_resume_manifest(path, expected_manifest):
    source = Path(path)
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaAdapterError(
            f"unable to read batch manifest for resume: {source}"
        ) from exc

    expected_fields = ("schemaVersion", "source", "shopId", "dateRange", "materialIds")
    if not isinstance(manifest, dict) or any(
        manifest.get(field) != expected_manifest[field]
        for field in expected_fields
    ):
        raise RpaAdapterError(
            "batch manifest does not match the requested RPA batch"
        )
    materials = manifest.get("materials")
    if not isinstance(materials, dict) or any(
        not isinstance(materials.get(material_id), dict)
        for material_id in expected_manifest["materialIds"]
    ):
        raise RpaAdapterError("batch manifest has invalid material entries")
    return manifest


def _material_success_is_valid(material_id, entry):
    errors, _warnings = _material_output_issues(material_id, entry)
    return not errors


def _material_output_issues(material_id, entry):
    errors = []
    warnings = []
    if entry.get("status") != "succeeded":
        return [f"material {material_id} is not succeeded"], warnings
    summary_json = entry.get("summaryJson")
    summary_md = entry.get("summaryMd")
    if not isinstance(summary_json, str) or not isinstance(summary_md, str):
        return [f"material {material_id} is missing retained summary paths"], warnings
    json_path = Path(summary_json)
    markdown_path = Path(summary_md)
    try:
        if not json_path.is_file() or not markdown_path.is_file():
            return [f"material {material_id} retained summary is missing"], warnings
        summary = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [f"material {material_id} retained summary JSON is unreadable"], warnings
    if not isinstance(summary, dict) or summary.get("materialId") != material_id:
        return [f"material {material_id} retained summary has the wrong materialId"], warnings
    frames = summary.get("frames")
    if not isinstance(frames, list) or not frames:
        return [f"material {material_id} retained summary has no frames"], warnings
    summary_warnings = summary.get("warnings")
    if not isinstance(summary_warnings, list) or not all(
        isinstance(warning, str) for warning in summary_warnings
    ):
        return [f"material {material_id} retained summary has invalid warnings"], warnings
    warnings.extend(summary_warnings)
    for frame in frames:
        if not isinstance(frame, dict):
            return [f"material {material_id} retained summary has an invalid frame"], warnings
        frame_name = frame.get("framePath")
        if not isinstance(frame_name, str) or Path(frame_name).name != frame_name:
            return [f"material {material_id} retained frame path is unsafe"], warnings
        try:
            frame_path = json_path.parent / frame_name
            if frame_path.stat().st_size <= 0:
                return [f"material {material_id} retained frame is empty"], warnings
            with frame_path.open("rb") as frame_file:
                start_marker = frame_file.read(2)
                frame_file.seek(-2, os.SEEK_END)
                end_marker = frame_file.read(2)
                if start_marker != b"\xff\xd8" or end_marker != b"\xff\xd9":
                    return [
                        f"material {material_id} retained frame is not a structurally valid JPEG"
                    ], warnings
        except OSError:
            return [f"material {material_id} retained frame is missing"], warnings
    try:
        if markdown_path.stat().st_size <= 0:
            return [f"material {material_id} retained Markdown summary is empty"], warnings
    except OSError:
        return [f"material {material_id} retained Markdown summary is missing"], warnings
    return errors, warnings


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
    manifest_path = _batch_manifest_path(args)
    manifest = _load_resume_manifest(
        manifest_path, _new_batch_manifest(args, material_ids)
    )
    errors = []
    warnings = []
    for material_id in material_ids:
        material_errors, material_warnings = _material_output_issues(
            material_id, manifest["materials"][material_id]
        )
        errors.extend(material_errors)
        warnings.extend(
            {"materialId": material_id, "warning": warning}
            for warning in material_warnings
        )
    receipt = {
        "schemaVersion": 1,
        "valid": not errors,
        "materialCount": len(material_ids),
        "materialIds": material_ids,
        "warningCount": len(warnings),
        "warnings": warnings,
        "errors": errors,
    }
    receipt_path = Path(args.output_dir) / BATCH_VERIFICATION_NAME
    _write_json_atomically(receipt_path, receipt)
    return receipt_path, receipt


def _run_rpa_batch(args, material_ids):
    batch_started_at = time.perf_counter()
    manifest_path = _batch_manifest_path(args)
    expected_manifest = _new_batch_manifest(args, material_ids)
    manifest = (
        _load_resume_manifest(manifest_path, expected_manifest)
        if args.resume
        else expected_manifest
    )
    manifest["concurrency"] = args.concurrency
    manifest.setdefault("timingsSec", {})["totalSec"] = 0.0
    _write_json_atomically(manifest_path, manifest)

    pending_material_ids = list(_iter_pending_material_ids(args, manifest, material_ids))
    for material_id in pending_material_ids:
        _start_material_attempt(manifest["materials"][material_id])
    _write_json_atomically(manifest_path, manifest)

    if args.concurrency == 1:
        for material_id in pending_material_ids:
            attempt = _attempt_rpa_material(args, material_id)
            _apply_material_attempt(manifest, material_id, attempt)
            _write_json_atomically(manifest_path, manifest)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(_attempt_rpa_material, args, material_id): material_id
                for material_id in pending_material_ids
            }
            for future in as_completed(futures):
                material_id = futures[future]
                _apply_material_attempt(manifest, material_id, future.result())
                _write_json_atomically(manifest_path, manifest)

    manifest["timingsSec"]["totalSec"] = _elapsed_since(batch_started_at)
    _write_json_atomically(manifest_path, manifest)

    failures = [
        material_id
        for material_id in material_ids
        if manifest["materials"][material_id].get("status") == "failed"
    ]
    last_result = None
    for material_id in reversed(material_ids):
        entry = manifest["materials"][material_id]
        if entry.get("status") == "succeeded":
            last_result = _entry_result(entry)
            break

    return manifest_path, last_result, failures


def _process_rpa_material(args, material_id):
    timings = _empty_material_timings()
    total_started_at = time.perf_counter()
    try:
        records = _time_call(
            timings,
            "rpaFetchSec",
            fetch_material_diagnosis,
            shop_id=args.shop_id,
            material_id=material_id,
            date_range_type=args.date_range_type,
            custom_start_date=args.custom_start_date,
            custom_end_date=args.custom_end_date,
            diagnosis_csv_dir=_diagnosis_csv_dir(args),
        )
        if args.diagnosis_records_dir:
            _time_call(
                timings,
                "diagnosisRecordWriteSec",
                _write_diagnosis_records,
                records,
                args.diagnosis_records_dir,
                material_id,
            )
        video_url = find_material_video_url(records, material_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / f"{material_id}.mp4"
            _time_call(timings, "videoDownloadSec", download_video, video_url, video_path)
            return _time_call(
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
    finally:
        timings["totalSec"] = _elapsed_since(total_started_at)
        _set_last_material_timings(timings)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
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
            args.shop_id = _resolve_shop_id(args)
            material_ids = (
                _top_material_ids(
                    args.top_materials_json,
                    args.date_range_type,
                    args.custom_start_date,
                    args.custom_end_date,
                )
                if args.top_materials_json
                else [args.material_id]
            )
            if args.top_materials_json:
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
                    result_dir, json_path, markdown_path = process_records(
                        records,
                        args.input_csv,
                        args.video,
                        args.output_dir,
                        ffmpeg_bin=args.ffmpeg_bin,
                        ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
                    )
                else:
                    video_url = find_material_video_url(records, material_id)
                    with tempfile.TemporaryDirectory() as temp_dir:
                        video_path = Path(temp_dir) / (
                            f"{material_id or Path(args.input_csv).stem}.mp4"
                        )
                        download_video(video_url, video_path)
                        result_dir, json_path, markdown_path = process_records(
                            records,
                            args.input_csv,
                            video_path,
                            args.output_dir,
                            ffmpeg_bin=args.ffmpeg_bin,
                            ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
                        )
            else:
                result_dir, json_path, markdown_path = process_local(
                    args.input_json,
                    args.video,
                    args.output_dir,
                    ffmpeg_bin=args.ffmpeg_bin,
                    ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
                )
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
