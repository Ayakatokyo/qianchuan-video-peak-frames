#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
import tempfile
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


def build_parser():
    parser = argparse.ArgumentParser(
        description="Extract video frames at Qianchuan CLICK_COUNT peaks."
    )
    parser.add_argument("--source", choices=("local", "rpa"), default="local")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--input-json")
    input_group.add_argument("--input-csv")
    parser.add_argument("--video")
    parser.add_argument("--output-dir", required=True)
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
    parser.add_argument("--date-range-type", choices=DATE_RANGE_TYPES)
    parser.add_argument("--custom-start-date")
    parser.add_argument("--custom-end-date")
    return parser


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
        "dateRange": {
            "type": args.date_range_type,
            "customStartDate": args.custom_start_date,
            "customEndDate": args.custom_end_date,
        },
        "materialIds": material_ids,
        "materials": {
            material_id: {
                "status": "pending",
                "attempts": 0,
                "summaryJson": None,
                "summaryMd": None,
                "errorType": None,
                "errorMessage": None,
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
    manifest_path = _batch_manifest_path(args)
    expected_manifest = _new_batch_manifest(args, material_ids)
    manifest = (
        _load_resume_manifest(manifest_path, expected_manifest)
        if args.resume
        else expected_manifest
    )
    _write_json_atomically(manifest_path, manifest)

    failures = []
    last_result = None
    for material_id in material_ids:
        entry = manifest["materials"][material_id]
        if args.resume and _material_success_is_valid(material_id, entry):
            last_result = _entry_result(entry)
            continue

        entry.update(
            {
                "status": "running",
                "attempts": entry.get("attempts", 0) + 1,
                "errorType": None,
                "errorMessage": None,
            }
        )
        _write_json_atomically(manifest_path, manifest)
        try:
            result = _process_rpa_material(args, material_id)
            result_dir, summary_json, summary_md = result
            entry.update(
                {
                    "status": "succeeded",
                    "summaryJson": str(summary_json),
                    "summaryMd": str(summary_md),
                }
            )
            if not _material_success_is_valid(material_id, entry):
                raise InputDataError(
                    f"material {material_id} did not produce valid retained outputs"
                )
            last_result = (Path(result_dir), Path(summary_json), Path(summary_md))
        except (InputDataError, FfmpegError, RpaAdapterError, RpaCsvError) as exc:
            entry.update(
                {
                    "status": "failed",
                    "errorType": type(exc).__name__,
                    "errorMessage": (
                        f"{type(exc).__name__} while processing material {material_id}"
                    ),
                }
            )
            failures.append(material_id)
        _write_json_atomically(manifest_path, manifest)

    return manifest_path, last_result, failures


def _process_rpa_material(args, material_id):
    records = fetch_material_diagnosis(
        shop_id=args.shop_id,
        material_id=material_id,
        date_range_type=args.date_range_type,
        custom_start_date=args.custom_start_date,
        custom_end_date=args.custom_end_date,
        diagnosis_csv_dir=_diagnosis_csv_dir(args),
    )
    if args.diagnosis_records_dir:
        _write_diagnosis_records(
            records, args.diagnosis_records_dir, material_id
        )
    video_url = find_material_video_url(records, material_id)
    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = Path(temp_dir) / f"{material_id}.mp4"
        download_video(video_url, video_path)
        return process_records(
            records,
            f"rpa://{FUNCTION_CODE}/{material_id}",
            video_path,
            args.output_dir,
            ffmpeg_bin=args.ffmpeg_bin,
            ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
            video_source="rpa-temporary-download",
        )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
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
            if not args.shop_id or not args.date_range_type or not (
                args.material_id or args.top_materials_json
            ):
                parser.error(
                    "--shop-id, --date-range-type, and either --material-id "
                    "or --top-materials-json are required for --source rpa"
                )
            if args.date_range_type == "CUSTOM" and not (
                args.custom_start_date and args.custom_end_date
            ):
                parser.error(
                    "CUSTOM requires --custom-start-date and "
                    "--custom-end-date"
                )
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
