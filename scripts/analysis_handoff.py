"""Read immutable analysis inputs and assemble model-authored JSON fragments."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from analysis_units_lib import UNIT_DEFINITIONS, script_label_evidence_refs
from batch_runtime import write_json_atomically


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def inspect_inputs(analysis_dir, *, loaded_units=None):
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
        if path.is_symlink():
            raise ValueError(f"analysis unit hash/size mismatch: {unit_id}")
        raw = path.read_bytes()
        if len(raw) != entry["byteSize"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"analysis unit hash/size mismatch: {unit_id}")
        units[unit_id] = json.loads(raw)
        if not isinstance(units[unit_id], dict):
            raise ValueError(f"JSON object required: {path}")
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
    if loaded_units is not None:
        loaded_units.update(units)
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


def check_material_part(analysis_dir, part_path, *, _context=None, _part=None):
    """Early material shape/reference checks, never a substitute for final review."""
    from final_report_lib import FinalReportError, _validate_v2_material_analysis

    root = Path(analysis_dir).resolve()
    path = Path(part_path)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.suffix != ".json":
        raise ValueError("material part must be a JSON file within the analysis directory")
    units = {}
    inputs = inspect_inputs(root, loaded_units=units) if _context is None else _context[0]
    if _context is not None:
        units = _context[1]
    part = read_object(path) if _part is None else _part
    if set(part) - {"schemaVersion", "materialAnalyses"} or part.get("schemaVersion", 5) != 5:
        raise ValueError("material part may contain only schemaVersion 5 and materialAnalyses")
    analyses = part.get("materialAnalyses")
    if not isinstance(analyses, list) or not analyses:
        raise ValueError("materialAnalyses must be a non-empty list")
    unit = units['chapter05-material']
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


def check_analysis_part(analysis_dir, part_path, *, _context=None, _part=None):
    """Preflight chapter slots and references; final validation still required."""
    root, path = Path(analysis_dir).resolve(), Path(part_path)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.suffix != '.json':
        raise ValueError('part must be a JSON file within the analysis directory')
    part = read_object(path) if _part is None else _part
    if 'materialAnalyses' in part:
        return check_material_part(root, path, _context=_context, _part=part)
    dashboard = part.get('highlightDashboard')
    if isinstance(dashboard, dict) and 'customClassification' in dashboard:
        raise ValueError('customClassification must be top-level, alongside highlightDashboard')
    allowed = {'schemaVersion', 'analysisProvenance', 'overallConclusion', 'portfolioDimensionConclusions',
               'hotMaterialInsight', 'batchInsights', 'keywordCloudReview', 'keywordCloudInsight',
               'highlightDashboard', 'customClassification'}
    if not part or set(part) - allowed or set(part) <= {'schemaVersion', 'analysisProvenance'}:
        raise ValueError('unrecognized or empty chapter part')
    def contains_null(value):
        if isinstance(value, dict):
            return any(contains_null(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_null(item) for item in value)
        return value is None
    if contains_null(part):
        raise ValueError('unfinished model text in chapter skeleton')
    if part.get('schemaVersion', 5) != 5:
        raise ValueError('chapter parts require schemaVersion 5')
    if 'overallConclusion' in part and (
            not isinstance(part['overallConclusion'], str) or not part['overallConclusion'].strip()):
        raise ValueError('overallConclusion must be nonempty text')
    if 'batchInsights' in part:
        insights = part['batchInsights']
        if not isinstance(insights, list) or not insights or any(
                not isinstance(item, dict) or item.get('topic') not in {'script', 'audience'}
                for item in insights):
            raise ValueError('batchInsights requires nonempty script or audience objects')
    units = {}
    inputs = inspect_inputs(root, loaded_units=units) if _context is None else _context[0]
    if _context is not None:
        units = _context[1]
    if 'keywordCloudInsight' in part:
        from analysis_quality import content_contract_findings
        findings = content_contract_findings(
            {**part, 'analysisProvenance': part.get('analysisProvenance',
                {'author': 'current_model', 'businessTextGeneration': 'direct'})},
            units['chapter03-script'])
        errors = [item['message'] for item in findings if item['level'] == 'error']
        if errors:
            raise ValueError('; '.join(errors))
    from analysis_tasks import evidence_refs
    from final_report_lib import _validate_v2_insight
    for topic, count, unit in [('script', 2, 'chapter03-script'), ('audience', 3, 'chapter02-audience')]:
        items = [x for x in part.get('batchInsights', []) if x.get('topic') == topic]
        if not items:
            continue
        if len(items) != count:
            raise ValueError(f'chapter part requires {count} {topic} insights')
        items = [_validate_v2_insight(item, i) for i, item in enumerate(items)]
        known = (script_label_evidence_refs(units[unit]['materials'])
                 if topic == 'script' else evidence_refs(units[unit]))
        invalid = sorted({ref for item in items for ref in item.get('evidenceRefs', []) if ref not in known})
        if invalid and topic == 'script':
            raise ValueError('batchInsights script evidence reference requires label refs '
                             '(script:<materialId>:<level1>:<level2>); keyword text refs belong to '
                             'keywordCloudInsight.sellingPoints. Invalid: ' + ', '.join(invalid))
        metrics = set()
        for i, item in enumerate(items):
            if not item.get('evidenceRefs') or any(ref not in known for ref in item['evidenceRefs']):
                raise ValueError('unknown or empty chapter evidence reference')
            if topic == 'audience':
                values = {ref.split(':')[1] for ref in item['evidenceRefs']}
                if len(values) != 1:
                    raise ValueError('audience insight must use exactly one metric')
                metrics.update(values)
        if topic == 'audience' and metrics != {'SHOW_COUNT', 'CLICK_COUNT', 'TRANSACTION_AMOUNT'}:
            raise ValueError('audience insights must cover all three metrics')
    expected_frames = {frame['frameRef'] for frame in inputs['frames']}
    for field in ('highlightDashboard', 'customClassification'):
        if field not in part:
            continue
        raw = part[field]
        if not isinstance(raw, dict) or not isinstance(raw.get('groups'), list):
            raise ValueError(f'{field}.groups must be a list')
        refs = []
        for group in raw['groups']:
            if not isinstance(group, dict) or not isinstance(group.get('frameRefs'), list):
                raise ValueError(f'{field}.groups requires frameRefs lists')
            refs.extend(group['frameRefs'])
        if field == 'customClassification':
            unclassified = raw.get('unclassifiedFrameRefs', [])
            if not isinstance(unclassified, list):
                raise ValueError('customClassification.unclassifiedFrameRefs must be a list')
            refs.extend(unclassified)
        if any(not isinstance(ref, str) for ref in refs):
            raise ValueError(f'{field} frameRefs must be strings')
        counts = Counter(refs)
        issues = {'duplicates': sorted(ref for ref, count in counts.items() if count > 1),
                  'missing': sorted(expected_frames - counts.keys()),
                  'unknown': sorted(counts.keys() - expected_frames)}
        if any(issues.values()):
            raise ValueError(f'{field} coverage: ' + json.dumps(issues, ensure_ascii=False))
        if field == 'customClassification':
            from final_report_lib import _validate_custom_classification
            _validate_custom_classification(raw, expected_frames)
            if raw['inputFrameManifestHash'] != 'sha256:' + inputs['manifestSha256']:
                raise ValueError('customClassification inputFrameManifestHash mismatch')
        else:
            from final_report_lib import _validate_highlight_dashboard_group, HIGHLIGHT_DASHBOARD_CATEGORIES
            groups = [_validate_highlight_dashboard_group(g, i) for i, g in enumerate(raw['groups'])]
            categories = [g['category'] for g in groups]
            if categories != [c for c in HIGHLIGHT_DASHBOARD_CATEGORIES if c in categories]:
                raise ValueError('highlightDashboard requires unique categories in portrait, product, other order')
    return {'status': 'chapter_structure_checked', 'partSha256': file_hash(path),
            'contentReviewed': False, 'finalValidationRequired': True}


def check_analysis_parts(analysis_dir, part_paths):
    """Validate independent fragments against one snapshot; report every failed part."""
    from final_report_lib import FinalReportError
    units = {}
    inputs = inspect_inputs(analysis_dir, loaded_units=units)
    results, errors = [], []
    for path in part_paths:
        try:
            part = read_object(path)
            # Independent fields/materials fail together, using one verified snapshot.
            chunks = []
            failures = []
            remaining = dict(part)
            materials = remaining.pop('materialAnalyses', None)
            if isinstance(materials, list) and materials:
                ids = [item.get('materialId') for item in materials if isinstance(item, dict)]
                if any(ids.count(mid) > 1 for mid in ids):
                    failures.append('duplicate material in part')
                chunks.extend({'schemaVersion': part.get('schemaVersion', 5),
                               'materialAnalyses': [item]} for item in materials)
            elif materials is not None:
                chunks.append({'materialAnalyses': materials})
            for key in ('highlightDashboard', 'customClassification', 'keywordCloudInsight'):
                if key in remaining:
                    chunk = {key: remaining.pop(key), 'schemaVersion': part.get('schemaVersion', 5)}
                    if key == 'keywordCloudInsight':
                        chunk['keywordCloudReview'] = part.get('keywordCloudReview', [])
                        if 'analysisProvenance' in part:
                            chunk['analysisProvenance'] = part['analysisProvenance']
                    chunks.append(chunk)
            if set(remaining) - {'schemaVersion', 'analysisProvenance', 'keywordCloudReview'} or not chunks:
                chunks.append(remaining)
            for chunk in chunks:
                try:
                    check_analysis_part(analysis_dir, path, _context=(inputs, units), _part=chunk)
                except (ValueError, OSError, FinalReportError) as exc:
                    failures.append(str(exc))
            if failures:
                raise ValueError('; '.join(failures))
        except (ValueError, OSError, FinalReportError) as exc:
            errors.append({'part': str(path), 'error': str(exc)})
        else:
            results.append({'part': str(path), 'partSha256': file_hash(path),
                            'status': 'part_structure_checked'})
    return {'status': 'invalid' if errors else 'parts_structure_checked', 'parts': results,
            'errors': errors, 'contentReviewed': False, 'finalValidationRequired': True}


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
