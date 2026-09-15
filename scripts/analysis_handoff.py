"""Read immutable analysis inputs and assemble model-authored JSON fragments."""

import hashlib
import json
from pathlib import Path

from analysis_units_lib import UNIT_DEFINITIONS
from batch_runtime import write_json_atomically


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def inspect_inputs(analysis_dir):
    """Check the manifest and actual images, without rewriting existing facts."""
    root = Path(analysis_dir).resolve()
    manifest_path = root / "analysis-units" / "analysis-unit-manifest.json"
    manifest = read_object(manifest_path)
    expected = dict(UNIT_DEFINITIONS)
    units = {}
    for entry in manifest.get("units", []):
        unit_id = entry.get("unitId")
        if unit_id not in expected or unit_id in units or entry.get("path") != expected[unit_id]:
            raise ValueError("unexpected or duplicate analysis unit")
        path = manifest_path.parent / entry["path"]
        if path.is_symlink() or path.stat().st_size != entry["byteSize"] or file_hash(path) != entry["sha256"]:
            raise ValueError(f"analysis unit hash/size mismatch: {unit_id}")
        units[unit_id] = read_object(path)
    if set(units) != set(expected):
        raise ValueError("five complete analysis units are required")
    prompt = (manifest_path.parent / manifest["promptPath"]).resolve()
    if prompt != root / "diagnostic-analysis-prompt.md" or not prompt.is_file():
        raise ValueError("analysis prompt must be next to analysis-units")
    frames = []
    seen = set()
    missing_count = 0
    materials = units["chapter04-highlight"].get("materials", [])
    if [m["materialId"] for m in materials] != manifest["materialIds"]:
        raise ValueError("highlight material order does not match manifest")
    for material in materials:
        missing_count += len(material.get("missingFrames", []))
        for frame in material.get("frames", []):
            ref = frame["evidenceRef"]
            path = Path(frame["framePath"])
            if not path.is_absolute():
                # Existing facts store caller-relative paths; no glob/latest fallback.
                path = Path.cwd() / path
            if path.is_symlink() or not path.is_file() or ref in seen:
                raise ValueError(f"missing, symbolic or duplicate frame: {ref}")
            seen.add(ref)
            frames.append({"frameRef": ref, "materialId": material["materialId"], "framePath": str(path.resolve()),
                           "sha256": file_hash(path)})
    if manifest.get("frames") != {"successfulCount": len(frames), "missingCount": missing_count}:
        raise ValueError("frame counts do not match manifest")
    return {"schemaVersion": 2, "manifestSha256": file_hash(manifest_path), "promptSha256": file_hash(prompt),
            "period": manifest.get("period", {}),
            "materialIds": manifest["materialIds"], "frames": frames,
            "uniqueImageCount": len({frame["sha256"] for frame in frames})}


def verify_assembly(analysis_json, inputs):
    """When assembly provenance exists, reject post-assembly edits and lost sources."""
    candidate = Path(analysis_json).resolve()
    receipt_path = candidate.with_suffix(".assembly.json")
    if not receipt_path.exists():
        return None  # Direct current-model JSON is a supported authoring path.
    receipt = read_object(receipt_path)
    if (receipt.get("candidateSha256") != file_hash(candidate)
            or inputs is None
            or any(receipt.get(key) != inputs[key] for key in ("manifestSha256", "promptSha256"))):
        raise ValueError("assembly candidate/input hash mismatch; preserve and re-author a new candidate")
    sources = receipt.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("assembly sources are required")
    for item in sources:
        path = Path(item["path"])
        if (path.is_symlink() or not path.resolve().is_relative_to(candidate.parent)
                or path.suffix != ".json" or not path.is_file() or file_hash(path) != item.get("sha256")):
            raise ValueError("assembly source hash/path mismatch")
    return file_hash(receipt_path)


def check_material_part(analysis_dir, part_path):
    """Early material shape/reference checks, never a substitute for final review."""
    from final_report_lib import FinalReportError, _validate_v2_material_analysis

    root = Path(analysis_dir).resolve()
    path = Path(part_path)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.suffix != ".json":
        raise ValueError("material part must be a JSON file within the analysis directory")
    inputs = inspect_inputs(root)
    part = read_object(path)
    if set(part) - {"schemaVersion", "materialAnalyses"} or part.get("schemaVersion", 5) != 5:
        raise ValueError("material part may contain only schemaVersion 5 and materialAnalyses")
    analyses = part.get("materialAnalyses")
    if not isinstance(analyses, list) or not analyses:
        raise ValueError("materialAnalyses must be a non-empty list")
    unit = read_object(root / "analysis-units/chapter05-material-input.json")
    source = {item["materialId"]: item for item in unit["materials"]}
    frame_hashes = {f["frameRef"]: f["sha256"] for f in inputs["frames"]}
    ids = []
    for index, raw in enumerate(analyses):
        try:
            item = _validate_v2_material_analysis(raw, index)
        except FinalReportError as exc:
            raise ValueError(str(exc)) from exc
        mid = item["materialId"]
        if mid not in source or mid in ids:
            raise ValueError("unknown or duplicate material in part")
        ids.append(mid)
        material = source[mid]
        expected = {f["frameRef"] for f in inputs["frames"] if f["materialId"] == mid}
        actual = [f["frameRef"] for f in item["highlightAnalyses"]]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("material part must cover every successful frame exactly once")
        known = {ref for refs in material.get("evidenceIndex", {}).values() for ref in refs} | expected
        if any(ref not in known for ref in item["evidenceRefs"]):
            raise ValueError("unknown material evidence reference in part")
        missing = material.get("highlight", {}).get("missingFrames", [])
        if bool(missing) != bool(item["frameCoverageNote"]):
            raise ValueError("frameCoverageNote must match missingFrames")
        by_ref = {f["frameRef"]: f for f in item["highlightAnalyses"]}
        for frame in by_ref.values():
            if frame.get("interpretationStatus") == "shared":
                target = by_ref.get(frame["sharedInterpretationRef"])
                if (not target or target is frame or target.get("interpretationStatus") != "supported"
                        or frame_hashes[frame["frameRef"]] != frame_hashes[target["frameRef"]]):
                    raise ValueError("shared interpretation requires same-material same-image direct target")
    return {"status": "material_structure_checked", "materialIds": ids,
            "partSha256": file_hash(path), "contentReviewed": False, "finalValidationRequired": True}


def assemble_analysis(analysis_dir, part_paths, output_path):
    """Concatenate lists and copy scalar fields exactly; never complete prose."""
    root = Path(analysis_dir).resolve()
    output = Path(output_path).resolve()
    if output.parent != root or output.exists():
        raise ValueError("use a new candidate JSON next to analysis-units")
    if output.suffix != ".json" or not part_paths:
        raise ValueError("candidate JSON and explicit part paths are required")
    inputs = inspect_inputs(root)
    candidate = {"schemaVersion": 5}
    merge_lists = {"batchInsights", "materialAnalyses", "nextActions", "warnings"}
    scalar_fields = {"analysisProvenance", "overallConclusion", "portfolioDimensionConclusions", "hotMaterialInsight",
                     "highlightDashboard", "customClassification", "keywordCloudInsight", "keywordCloudReview"}
    sources = []
    for raw_path in part_paths:
        path = Path(raw_path).resolve()
        if path.suffix != ".json" or not path.is_relative_to(root):
            raise ValueError("analysis fragments must be JSON within the analysis directory")
        part = read_object(path)
        if part.pop("schemaVersion", 5) != 5:
            raise ValueError("analysis fragments must use schemaVersion 5")
        for key, value in part.items():
            if key in merge_lists:
                if not isinstance(value, list):
                    raise ValueError(f"{key} must be a list")
                candidate.setdefault(key, []).extend(value)
            elif key in scalar_fields and key not in candidate:
                candidate[key] = value
            else:
                raise ValueError(f"unknown or conflicting analysis field: {key}")
        sources.append({"path": str(path), "sha256": file_hash(path)})
    ids = [item["materialId"] for item in candidate.get("materialAnalyses", [])]
    if len(ids) != len(set(ids)) or set(ids) != set(inputs["materialIds"]):
        raise ValueError("fragments must cover each manifest material exactly once")
    # Preserve author-supplied order and values. The standard validator owns semantics.
    write_json_atomically(output, candidate)
    write_json_atomically(output.with_suffix(".assembly.json"),
                          {"candidateSha256": file_hash(output), "sources": sources,
                           "manifestSha256": inputs["manifestSha256"], "promptSha256": inputs["promptSha256"]})
    return {"analysisJson": str(output), "analysisSha256": file_hash(output)}
