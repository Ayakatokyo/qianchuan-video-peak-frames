"""Validation of retained per-material extraction outputs."""

import json
import os
from pathlib import Path

from batch_runtime import stage_counts
from schemas import SchemaError, validate_manifest


def material_output_issues(material_id, entry):
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


def verify_streaming_batch(
    args,
    material_ids,
    manifest,
    *,
    material_output_issues_fn=material_output_issues,
    write_json,
    verification_name,
):
    errors = []
    warnings = []
    selected = manifest.get("selectedMaterials")
    rejected = manifest.get("rejectedMaterials")
    cancelled = manifest.get("cancelledMaterials")
    if not isinstance(selected, list):
        raise RuntimeError("streaming batch manifest has invalid selectedMaterials")
    if not isinstance(rejected, list):
        raise RuntimeError("streaming batch manifest has invalid rejectedMaterials")
    if not isinstance(cancelled, list):
        raise RuntimeError("streaming batch manifest has invalid cancelledMaterials")

    selected_by_id = {}
    for entry in selected:
        if not isinstance(entry, dict):
            errors.append("streaming selected material entry is invalid")
            continue
        material_id = str(entry.get("materialId") or "").strip()
        if material_id:
            selected_by_id[material_id] = entry

    manifest_material_ids = manifest.get("materialIds")
    try:
        manifest_target_top_n = int(manifest.get("targetTopN") or 0)
    except (TypeError, ValueError):
        manifest_target_top_n = 0
    if manifest_target_top_n <= 0:
        manifest_target_top_n = None
    target_top_n = args.target_top_n or manifest_target_top_n
    if (
        target_top_n
        and isinstance(manifest_material_ids, list)
        and set(material_ids) == set(manifest_material_ids)
    ):
        if len(selected) < target_top_n:
            errors.append("streaming batch selectedMaterials does not satisfy targetTopN")
        material_ids_to_verify = [
            str(entry.get("materialId") or "").strip()
            for entry in selected
            if isinstance(entry, dict) and str(entry.get("materialId") or "").strip()
        ]
    else:
        material_ids_to_verify = material_ids

    for material_id in material_ids_to_verify:
        entry = selected_by_id.get(material_id)
        if entry is None:
            errors.append(f"material {material_id} is missing from streaming selectedMaterials")
            continue
        if entry.get("rpaStatus") != "selected":
            errors.append(f"material {material_id} is not RPA selected")
            continue
        if entry.get("postprocessStatus") != "succeeded":
            errors.append(f"material {material_id} postprocess is not succeeded")
            continue
        material_errors, material_warnings = material_output_issues_fn(material_id, entry)
        errors.extend(material_errors)
        warnings.extend(
            {"materialId": material_id, "warning": warning}
            for warning in material_warnings
        )

    receipt = {
        "schemaVersion": 2,
        "manifestSchemaVersion": 2,
        "valid": not errors,
        "materialCount": len(material_ids_to_verify),
        "materialIds": material_ids_to_verify,
        "selectedCount": len(selected),
        "rejectedCount": len(rejected),
        "cancelledCount": len(cancelled),
        "stageCounts": manifest.get("stageCounts")
        if isinstance(manifest.get("stageCounts"), dict)
        else stage_counts(
            selected,
            base_dir=Path(args.output_dir).parent,
            report_ready=(manifest.get("reportReady") or {}).get("status") == "ready",
        ),
        "warningCount": len(warnings),
        "warnings": warnings,
        "errors": errors,
    }
    receipt_path = Path(args.output_dir) / verification_name
    write_json(receipt_path, receipt)
    return receipt_path, receipt


def verify_batch(
    args,
    material_ids,
    *,
    manifest_path,
    load_resume_manifest,
    new_batch_manifest,
    material_output_issues_fn=material_output_issues,
    write_json,
    verification_name,
    error_type,
):
    try:
        raw_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(
            f"unable to read batch manifest for verification: {manifest_path}"
        ) from exc
    try:
        manifest = validate_manifest(raw_manifest)
        return verify_streaming_batch(
            args,
            material_ids,
            manifest,
            material_output_issues_fn=material_output_issues_fn,
            write_json=write_json,
            verification_name=verification_name,
        )
    except (SchemaError, RuntimeError) as exc:
        raise error_type(str(exc)) from exc
