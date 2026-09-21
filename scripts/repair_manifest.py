#!/usr/bin/env python3
"""Repair retained Qianchuan batch artifacts without calling RPA."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import extract_peak_frames as pipeline
from batch_runtime import new_run_id, stage_counts, write_json_atomically
from batch_verification import material_output_issues
from rpa_csv_parser import parse_diagnosis_csv
from rpa_artifact_receipts import (
    RpaArtifactReceiptError,
    load_records_persisted_receipt,
)
from path_safety import artifact_path, material_dir, validate_material_id


REPAIR_RECEIPT_NAME = "repair-manifest.json"


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _resolve(root, path, fallback):
    if isinstance(path, str) and path.strip():
        candidate = Path(path)
        return candidate if candidate.is_absolute() else root / candidate
    return fallback


def _relative(root, path):
    return os.path.relpath(Path(path), root)


def _write_records(path, records):
    return write_json_atomically(path, records)


def _normalize_summary_paths(root, entry):
    for key in ("summaryJson", "summaryMd", "diagnosisRecordsPath", "diagnosisCsvPath"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            entry[key] = str(_resolve(root, value, Path(value)))


def _valid_summary(material_id, entry):
    candidate = dict(entry)
    candidate["status"] = "succeeded"
    errors, _warnings = material_output_issues(material_id, candidate)
    return not errors


def _normalize_postprocess_attempts(entry):
    """Normalize the short-lived integer regression without losing its count."""
    attempts = entry.get("postprocessAttempts")
    count = entry.get("postprocessAttemptCount", 0)
    if type(count) is not int or count < 0:
        count = 0
    if type(attempts) is int:
        count = max(count, attempts)
        attempts = []
    elif not isinstance(attempts, list):
        attempts = []
    count = max(count, len(attempts))
    entry["postprocessAttemptCount"] = count
    entry["postprocessAttempts"] = attempts
    return attempts


def _default_postprocess(args, entry):
    attempt = pipeline._attempt_wave_postprocess(args, entry)
    if attempt["error"] is not None:
        raise attempt["error"]
    _result_dir, summary_json, summary_md = attempt["result"]
    return {
        "summaryJson": str(summary_json),
        "summaryMd": str(summary_md),
        "timingsSec": attempt.get("timingsSec"),
        **{
            key: attempt[key]
            for key in (
                "videoUrlSource",
                "videoUrlFallbackReason",
                "videoPath",
                "videoBytes",
                "videoSha256",
            )
            if key in attempt
        },
    }


def _load_receipted_records(root, material_id):
    """Return durable RPA records when an interrupted manifest missed the checkpoint."""
    try:
        recovered = load_records_persisted_receipt(
            root / "rpa-receipts",
            material_id=material_id,
            base_dir=root,
        )
    except RpaArtifactReceiptError:
        return None

    records_path = recovered["recordsPath"]
    records = pipeline._read_diagnosis_records(records_path, material_id)
    pipeline.find_material_video_url(records, material_id)
    return {
        "records": records,
        "recordsPath": records_path,
        "csvPath": recovered["csvPath"],
        "clientAttemptId": recovered["receipt"]["clientAttemptId"],
    }


def _rebuild_manifest(manifest, materials, base_dir=None):
    ordered = [
        {key: value for key, value in item.items() if key != "records"}
        for item in sorted(materials, key=lambda item: int(item.get("rank") or 0))
    ]
    submitted = [
        item for item in ordered
        if item.get("rpaStatus") in {"selected", "rejected"}
        or item.get("status") in {"running", "succeeded", "failed"}
    ]
    selected = [item for item in submitted if item.get("rpaStatus") == "selected"]
    rejected = [item for item in submitted if item.get("rpaStatus") == "rejected"]
    cancelled = [item for item in ordered if item.get("rpaStatus") == "cancelled"]
    manifest["materials"] = ordered
    manifest["submittedMaterials"] = submitted
    manifest["selectedMaterials"] = selected
    manifest["rejectedMaterials"] = rejected
    manifest["cancelledMaterials"] = cancelled
    manifest["stageCounts"] = stage_counts(
        submitted,
        base_dir=base_dir,
        report_ready=(manifest.get("reportReady") or {}).get("status") == "ready",
    )
    return manifest


def _manifest_from_top_materials(payload):
    materials = payload.get("materials") if isinstance(payload, dict) else None
    if not isinstance(materials, list) or not materials:
        raise ValueError("top materials JSON must contain a non-empty materials list")
    entries = []
    for rank, material in enumerate(materials, start=1):
        entry = dict(material)
        entry.update(
            {
                "rank": material.get("rank") or rank,
                "materialId": str(material.get("materialId") or "").strip(),
                "rpaStatus": "unknown",
                "postprocessStatus": "pending",
                "postprocessSucceeded": False,
                "recordsPersisted": False,
                "status": "pending",
                "failureReason": None,
                "errorType": None,
                "errorMessage": None,
                "diagnosisRecordsPath": None,
                "diagnosisCsvPath": None,
                "summaryJson": None,
                "summaryMd": None,
                "timingsSec": {},
            }
        )
        if not entry["materialId"]:
            raise ValueError("top materials JSON contains a material without materialId")
        entries.append(entry)
    return {
        "schemaVersion": 2,
        "source": "repair-manifest",
        "runId": new_run_id(),
        "targetTopN": len(entries),
        "candidateCount": len(entries),
        "candidateLimit": len(entries),
        "dateRange": {
            "type": "CUSTOM",
            "customStartDate": payload.get("actualStartDate"),
            "customEndDate": payload.get("actualEndDate"),
        },
        "materialIds": [entry["materialId"] for entry in entries],
        "materials": entries,
        "submittedMaterials": [],
        "selectedMaterials": [],
        "rejectedMaterials": [],
        "cancelledMaterials": [],
    }


def repair_batch(
    peak_output_dir,
    *,
    diagnosis_records_dir=None,
    diagnosis_csv_dir=None,
    top_materials_json=None,
    postprocess=None,
    download_concurrency=2,
    postprocess_concurrency=2,
    max_materials_per_run=2,
):
    if type(download_concurrency) is not int or download_concurrency <= 0:
        raise ValueError("download_concurrency must be a positive integer")
    if type(postprocess_concurrency) is not int or postprocess_concurrency <= 0:
        raise ValueError("postprocess_concurrency must be a positive integer")
    if type(max_materials_per_run) is not int or max_materials_per_run <= 0:
        raise ValueError("max_materials_per_run must be a positive integer")
    peak_dir = Path(peak_output_dir)
    root = peak_dir.parent
    manifest_path = peak_dir / "batch-manifest.json"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
    elif top_materials_json:
        manifest = _manifest_from_top_materials(_read_json(top_materials_json))
    else:
        raise ValueError(
            "batch-manifest.json is missing; --top-materials-json is required to rebuild it"
        )
    materials = manifest.get("materials")
    if not isinstance(materials, list):
        raise ValueError("streaming batch manifest must contain a materials list")
    records_dir = Path(diagnosis_records_dir) if diagnosis_records_dir else root / "diagnosis-records"
    csv_dir = Path(diagnosis_csv_dir) if diagnosis_csv_dir else root / "diagnosis-csv"
    parallel_postprocess = postprocess is None
    postprocess = postprocess or _default_postprocess
    args = SimpleNamespace(
        output_dir=str(peak_dir),
        ffmpeg_bin=None,
        ffmpeg_timeout_sec=pipeline.DEFAULT_FFMPEG_TIMEOUT_SEC,
        download_concurrency=download_concurrency,
        postprocess_concurrency=postprocess_concurrency,
        _video_download_semaphore=threading.BoundedSemaphore(
            download_concurrency
        ),
        _ffmpeg_semaphore=threading.BoundedSemaphore(postprocess_concurrency),
    )
    manifest["downloadConcurrency"] = download_concurrency
    manifest["postprocessConcurrency"] = postprocess_concurrency
    actions = []
    errors = []
    pending_postprocess = []
    checkpoint_lock = threading.RLock()

    def checkpoint():
        with checkpoint_lock:
            _rebuild_manifest(manifest, materials, base_dir=peak_dir.parent)
            write_json_atomically(manifest_path, manifest)

    def finalize_entry(raw_entry, entry, material_id, records):
        if entry.get("summaryJson") and entry.get("summaryMd"):
            entry["summaryJson"] = str(
                _resolve(root, entry["summaryJson"], Path(entry["summaryJson"]))
            )
            entry["summaryMd"] = str(
                _resolve(root, entry["summaryMd"], Path(entry["summaryMd"]))
            )
            if _valid_summary(material_id, entry):
                entry.update(
                    {
                        "postprocessStatus": "succeeded",
                        "postprocessSucceeded": True,
                        "status": "succeeded",
                    }
                )
                if records is not None:
                    entry["rpaStatus"] = "selected"
                if not any(
                    item.get("materialId") == material_id for item in actions
                ):
                    actions.append(
                        {"materialId": material_id, "action": "summary_to_manifest"}
                    )
        raw_entry.clear()
        raw_entry.update(entry)

    def apply_postprocess_result(entry, material_id, result=None, error=None):
        finished_at = time.monotonic()
        attempts = _normalize_postprocess_attempts(entry)
        attempt = attempts[-1] if attempts else None
        if attempt is not None:
            attempt.update(
                {
                    "finishedAtSec": finished_at,
                    "durationSec": max(
                        0.0,
                        finished_at - attempt.get("startedAtSec", finished_at),
                    ),
                    "substage": "postprocess",
                }
            )
        if error is None:
            attempt_timings = result.get("timingsSec")
            if isinstance(attempt_timings, dict):
                entry["timingsSec"] = pipeline._merge_wave_postprocess_timings(
                    entry.get("timingsSec", {}), attempt_timings
                )
            for key in (
                "videoUrlSource",
                "videoUrlFallbackReason",
                "videoPath",
                "videoBytes",
                "videoSha256",
            ):
                if key in result:
                    entry[key] = result[key]
            entry.update(
                {
                    "postprocessStatus": "succeeded",
                    "postprocessSubstage": "completed",
                    "postprocessSucceeded": True,
                    "status": "succeeded",
                    "summaryJson": str(Path(result["summaryJson"]).resolve()),
                    "summaryMd": str(Path(result["summaryMd"]).resolve()),
                    "errorType": None,
                    "errorMessage": None,
                }
            )
            if attempt is not None:
                attempt["status"] = "succeeded"
            actions.append(
                {"materialId": material_id, "action": "records_to_postprocess"}
            )
            return
        entry.update(
            {
                "postprocessStatus": "failed",
                "postprocessSucceeded": False,
                "status": "failed",
                "errorType": type(error).__name__,
                "errorMessage": str(error),
            }
        )
        if attempt is not None:
            attempt.update(
                {
                    "status": "failed",
                    "errorType": type(error).__name__,
                    "errorMessage": str(error),
                }
            )
        errors.append(f"{material_id}: postprocess repair failed: {error}")

    for raw_entry in materials:
        entry = dict(raw_entry)
        _normalize_postprocess_attempts(entry)
        material_id = str(entry.get("materialId") or "").strip()
        if not material_id:
            errors.append("manifest material has no materialId")
            continue
        try:
            validate_material_id(material_id)
        except ValueError as exc:
            errors.append(f"{material_id}: invalid material ID: {exc}")
            continue
        _normalize_summary_paths(root, entry)
        if not entry.get("summaryJson"):
            default_summary = material_dir(peak_dir, material_id) / "summary.json"
            if default_summary.is_file():
                entry["summaryJson"] = str(default_summary)
        if not entry.get("summaryMd"):
            default_markdown = material_dir(peak_dir, material_id) / "summary.md"
            if default_markdown.is_file():
                entry["summaryMd"] = str(default_markdown)
        records_path = _resolve(root, entry.get("diagnosisRecordsPath"), artifact_path(records_dir, material_id, ".json"))
        csv_path = _resolve(root, entry.get("diagnosisCsvPath"), artifact_path(csv_dir, material_id, ".csv"))
        records = None
        try:
            receipted = _load_receipted_records(root, material_id)
        except Exception as exc:
            errors.append(f"{material_id}: persisted receipt is unusable: {exc}")
            receipted = None
        if receipted is not None:
            records = receipted["records"]
            records_path = receipted["recordsPath"]
            csv_path = receipted["csvPath"]
            entry.update(
                {
                    "diagnosisRecordsPath": _relative(root, records_path),
                    "diagnosisCsvPath": _relative(root, csv_path),
                    "recordsPersisted": True,
                    "rpaResultStatus": "available",
                    "rpaClientAttemptId": receipted["clientAttemptId"],
                    "rpaCsvVerified": True,
                }
            )
            actions.append({"materialId": material_id, "action": "receipt_to_records"})
        elif records_path.is_file():
            try:
                records = pipeline._read_diagnosis_records(records_path, material_id)
            except Exception as exc:
                errors.append(f"{material_id}: records unreadable: {exc}")
        elif csv_path.is_file():
            try:
                records = parse_diagnosis_csv(csv_path)
                pipeline.find_material_video_url(records, material_id)
                _write_records(records_path, records)
                entry["diagnosisRecordsPath"] = _relative(root, records_path)
                entry["recordsPersisted"] = True
                actions.append({"materialId": material_id, "action": "csv_to_records"})
            except Exception as exc:
                errors.append(f"{material_id}: CSV repair failed: {exc}")

        if records is not None:
            entry["rpaStatus"] = "selected"
            entry["recordsPersisted"] = True
            entry["records"] = records
            entry["diagnosisRecordsPath"] = _relative(root, records_path)
            entry.setdefault("timingsSec", pipeline._empty_material_timings())
            # An interrupted worker may have checkpointed a successful status
            # before writing summary/JPEG artifacts. Trust the retained files,
            # not the status flag, when deciding whether local postprocess is
            # still required.
            if not _valid_summary(material_id, entry):
                if parallel_postprocess:
                    entry.update(
                        {
                            "postprocessStatus": "queued",
                            "postprocessSucceeded": False,
                            "status": "pending",
                        }
                    )
                    raw_entry.clear()
                    raw_entry.update(entry)
                    pending_postprocess.append(
                        (raw_entry, entry, material_id, records)
                    )
                    continue
                try:
                    result = postprocess(args, entry)
                except Exception as exc:
                    apply_postprocess_result(entry, material_id, error=exc)
                else:
                    apply_postprocess_result(entry, material_id, result=result)

        finalize_entry(raw_entry, entry, material_id, records)

    if pending_postprocess:
        # Prefer materials that have not yet had a local postprocess attempt.
        # Manifest order remains rank-stable; only this bounded execution queue
        # is rotated so a repeatedly failing item cannot starve later items.
        pending_postprocess.sort(
            key=lambda item: (
                item[1].get("postprocessAttemptCount", 0),
                int(item[1].get("rank") or 0),
            )
        )
        queued_by_material = {
            material_id: (raw_entry, entry)
            for raw_entry, entry, material_id, _records in pending_postprocess
        }

        def checkpoint_postprocess(material_id, updates):
            queued = queued_by_material.get(material_id)
            if queued is None:
                return
            raw_entry, live_entry = queued
            with checkpoint_lock:
                current = dict(raw_entry)
                incoming_timings = updates.get("timingsSec")
                if isinstance(incoming_timings, dict):
                    current_timings = dict(current.get("timingsSec") or {})
                    current_timings.update(incoming_timings)
                    current["timingsSec"] = current_timings
                current.update(
                    {key: value for key, value in updates.items() if key != "timingsSec"}
                )
                raw_entry.clear()
                raw_entry.update(current)
                live_entry.clear()
                live_entry.update(current)
                checkpoint()

        args._postprocess_checkpoint = checkpoint_postprocess
        attempted_postprocess = pending_postprocess[:max_materials_per_run]
        for raw_entry, entry, _material_id, _records in attempted_postprocess:
            started_at = time.monotonic()
            attempts = _normalize_postprocess_attempts(entry)
            entry["postprocessAttemptCount"] += 1
            attempts.append(
                {
                    "phase": "parallel",
                    "startedAtSec": started_at,
                    "status": "running",
                }
            )
            raw_entry["postprocessAttemptCount"] = entry[
                "postprocessAttemptCount"
            ]
            raw_entry["postprocessAttempts"] = list(attempts)
            entry["status"] = "running"
            raw_entry["status"] = "running"
        # Persist the bounded active set before local work starts, then
        # checkpoint downloads and each completion independently.
        checkpoint()
        queue_workers = min(
            len(attempted_postprocess),
            download_concurrency + postprocess_concurrency,
        )
        with ThreadPoolExecutor(max_workers=queue_workers) as executor:
            futures = {
                executor.submit(postprocess, args, dict(entry)): (
                    raw_entry,
                    entry,
                    material_id,
                    records,
                )
                for raw_entry, entry, material_id, records in attempted_postprocess
            }
            for future in as_completed(futures):
                raw_entry, entry, material_id, records = futures[future]
                with checkpoint_lock:
                    try:
                        result = future.result()
                    except Exception as exc:
                        apply_postprocess_result(entry, material_id, error=exc)
                    else:
                        apply_postprocess_result(entry, material_id, result=result)
                    finalize_entry(raw_entry, entry, material_id, records)
                    checkpoint()
        del args._postprocess_checkpoint
    else:
        attempted_postprocess = []

    checkpoint()
    remaining_postprocess = sum(
        not _valid_summary(material_id, dict(raw_entry))
        for raw_entry, _entry, material_id, _records in pending_postprocess
    )
    receipt = {
        "schemaVersion": 1,
        "manifestPath": str(manifest_path),
        "rpaCalls": 0,
        "actionCount": len(actions),
        "actions": actions,
        "stageCounts": manifest["stageCounts"],
        "errors": errors,
        "valid": not errors,
        "maxMaterialsPerRun": max_materials_per_run,
        "postprocessAttemptedCount": len(attempted_postprocess),
        "postprocessRemainingCount": remaining_postprocess,
        "postprocessBatchComplete": remaining_postprocess == 0,
    }
    write_json_atomically(peak_dir / REPAIR_RECEIPT_NAME, receipt)
    return receipt


def build_parser():
    parser = argparse.ArgumentParser(description="Repair retained Qianchuan batch artifacts without RPA.")
    parser.add_argument("--output-dir", required=True, help="Existing peak-frames directory")
    parser.add_argument("--diagnosis-records-dir")
    parser.add_argument("--diagnosis-csv-dir")
    parser.add_argument("--top-materials-json")
    parser.add_argument("--download-concurrency", type=int, default=2)
    parser.add_argument("--postprocess-concurrency", type=int, default=2)
    parser.add_argument("--max-materials-per-run", type=int, default=2)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        receipt = repair_batch(
            args.output_dir,
            diagnosis_records_dir=args.diagnosis_records_dir,
            diagnosis_csv_dir=args.diagnosis_csv_dir,
            top_materials_json=args.top_materials_json,
            download_concurrency=args.download_concurrency,
            postprocess_concurrency=args.postprocess_concurrency,
            max_materials_per_run=args.max_materials_per_run,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"repair-manifest failed: {exc}")
        return 2
    print(f"repair_manifest={Path(args.output_dir) / REPAIR_RECEIPT_NAME}")
    print("stage_counts=" + json.dumps(receipt["stageCounts"], sort_keys=True))
    print(f"postprocess_remaining={receipt['postprocessRemainingCount']}")
    return 0 if receipt["valid"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
