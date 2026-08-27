#!/usr/bin/env python3
"""Repair retained Qianchuan batch artifacts without calling RPA."""

import argparse
import json
import os
from pathlib import Path
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


def _default_postprocess(args, entry):
    attempt = pipeline._attempt_wave_postprocess(args, entry)
    if attempt["error"] is not None:
        raise attempt["error"]
    _result_dir, summary_json, summary_md = attempt["result"]
    return {"summaryJson": str(summary_json), "summaryMd": str(summary_md)}


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
    ordered = sorted(materials, key=lambda item: int(item.get("rank") or 0))
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
):
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
    postprocess = postprocess or _default_postprocess
    args = SimpleNamespace(
        output_dir=str(peak_dir),
        ffmpeg_bin=None,
        ffmpeg_timeout_sec=pipeline.DEFAULT_FFMPEG_TIMEOUT_SEC,
    )
    actions = []
    errors = []

    for raw_entry in materials:
        entry = dict(raw_entry)
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
            # An interrupted worker may have checkpointed a successful status
            # before writing summary/JPEG artifacts. Trust the retained files,
            # not the status flag, when deciding whether local postprocess is
            # still required.
            if not _valid_summary(material_id, entry):
                try:
                    result = postprocess(args, entry)
                    entry.update(
                        {
                            "postprocessStatus": "succeeded",
                            "postprocessSucceeded": True,
                            "status": "succeeded",
                            "summaryJson": str(Path(result["summaryJson"]).resolve()),
                            "summaryMd": str(Path(result["summaryMd"]).resolve()),
                            "errorType": None,
                            "errorMessage": None,
                        }
                    )
                    actions.append({"materialId": material_id, "action": "records_to_postprocess"})
                except Exception as exc:
                    entry.update(
                        {
                            "postprocessStatus": "failed",
                            "postprocessSucceeded": False,
                            "status": "failed",
                            "errorType": type(exc).__name__,
                            "errorMessage": str(exc),
                        }
                    )
                    errors.append(f"{material_id}: postprocess repair failed: {exc}")

        if entry.get("summaryJson") and entry.get("summaryMd"):
            entry["summaryJson"] = str(_resolve(root, entry["summaryJson"], Path(entry["summaryJson"])))
            entry["summaryMd"] = str(_resolve(root, entry["summaryMd"], Path(entry["summaryMd"])))
            if _valid_summary(material_id, entry):
                entry.update({"postprocessStatus": "succeeded", "postprocessSucceeded": True, "status": "succeeded"})
                if records is not None:
                    entry["rpaStatus"] = "selected"
                if not any(item.get("materialId") == material_id for item in actions):
                    actions.append({"materialId": material_id, "action": "summary_to_manifest"})
        raw_entry.clear()
        raw_entry.update(entry)

    _rebuild_manifest(manifest, materials, base_dir=peak_dir.parent)
    write_json_atomically(manifest_path, manifest)
    receipt = {
        "schemaVersion": 1,
        "manifestPath": str(manifest_path),
        "rpaCalls": 0,
        "actionCount": len(actions),
        "actions": actions,
        "stageCounts": manifest["stageCounts"],
        "errors": errors,
        "valid": not errors,
    }
    write_json_atomically(peak_dir / REPAIR_RECEIPT_NAME, receipt)
    return receipt


def build_parser():
    parser = argparse.ArgumentParser(description="Repair retained Qianchuan batch artifacts without RPA.")
    parser.add_argument("--output-dir", required=True, help="Existing peak-frames directory")
    parser.add_argument("--diagnosis-records-dir")
    parser.add_argument("--diagnosis-csv-dir")
    parser.add_argument("--top-materials-json")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        receipt = repair_batch(
            args.output_dir,
            diagnosis_records_dir=args.diagnosis_records_dir,
            diagnosis_csv_dir=args.diagnosis_csv_dir,
            top_materials_json=args.top_materials_json,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"repair-manifest failed: {exc}")
        return 2
    print(f"repair_manifest={Path(args.output_dir) / REPAIR_RECEIPT_NAME}")
    print("stage_counts=" + json.dumps(receipt["stageCounts"], sort_keys=True))
    return 0 if receipt["valid"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
