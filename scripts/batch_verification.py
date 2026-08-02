"""Validation of retained per-material extraction outputs."""

import json
import os
from pathlib import Path


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
