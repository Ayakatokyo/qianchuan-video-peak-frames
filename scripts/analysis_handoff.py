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
            frames.append({"frameRef": ref, "framePath": str(path.resolve()),
                           "sha256": file_hash(path)})
    if manifest.get("frames") != {"successfulCount": len(frames), "missingCount": missing_count}:
        raise ValueError("frame counts do not match manifest")
    return {"manifestSha256": file_hash(manifest_path), "promptSha256": file_hash(prompt),
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
