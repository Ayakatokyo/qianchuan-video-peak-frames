"""Build isolated chapter inputs from the compatible analysis snapshot."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid


UNIT_DEFINITIONS = (
    ("chapter01-portfolio", "chapter01-portfolio-input.json"),
    ("chapter02-audience", "chapter02-audience-input.json"),
    ("chapter03-script", "chapter03-script-input.json"),
    ("chapter04-highlight", "chapter04-highlight-input.json"),
    ("chapter05-material", "chapter05-material-input.json"),
)

UNIT_CONTRACTS = {
    "chapter01-portfolio": {
        "chapterId": "01",
        "outputFields": ["overallConclusion", "portfolioDimensionConclusions"],
        "allowedEvidencePrefixes": ["commercial:", "audience:", "script:", "highlight:", "interaction:"],
    },
    "chapter02-audience": {
        "chapterId": "02",
        "outputFields": ["batchInsights:audience"],
        "allowedEvidencePrefixes": ["audience:"],
    },
    "chapter03-script": {
        "chapterId": "03",
        "outputFields": ["batchInsights:script"],
        "allowedEvidencePrefixes": ["script:"],
    },
    "chapter04-highlight": {
        "chapterId": "04",
        "outputFields": ["highlightDashboard", "materialAnalyses.highlightAnalyses"],
        "allowedEvidencePrefixes": ["highlight:"],
    },
    "chapter05-material": {
        "chapterId": "05",
        "outputFields": ["materialAnalyses"],
        "allowedEvidencePrefixes": ["audience:", "script:", "highlight:", "interaction:", "commercial:"],
    },
}


class AnalysisUnitError(ValueError):
    """Raised when a compatible snapshot cannot produce chapter inputs."""


def _text(value):
    return str(value).strip() if value is not None else ""


def _identity(material):
    fields = (
        "rank",
        "materialId",
        "materialName",
        "materialType",
        "videoType",
        "totalCost",
    )
    return {field: deepcopy(material[field]) for field in fields if field in material}


def _without_source_material_ids(value):
    if isinstance(value, dict):
        return {
            key: _without_source_material_ids(item)
            for key, item in value.items()
            if key != "sourceMaterialIds"
        }
    if isinstance(value, list):
        return [_without_source_material_ids(item) for item in value]
    return deepcopy(value)


def _audience_unit(analysis):
    audience = analysis.get("audience", {})
    if not isinstance(audience, dict):
        audience = {}
    metrics = _without_source_material_ids(audience.get("metrics", {}))
    if isinstance(metrics, dict):
        for metric, metric_data in metrics.items():
            dimensions = metric_data.get("dimensions", {}) if isinstance(metric_data, dict) else {}
            for dimension_key, dimension in dimensions.items():
                if not isinstance(dimension, dict):
                    continue
                for bucket in dimension.get("buckets", []):
                    bucket_name = _text(bucket.get("bucket")) if isinstance(bucket, dict) else ""
                    if bucket_name:
                        bucket["evidenceRef"] = (
                            f"audience:{metric}:{dimension_key}:{bucket_name}"
                        )
    return {
        "schemaVersion": 1,
        "unitId": "chapter02-audience",
        "chapterId": "02",
        "period": deepcopy(analysis.get("period", {})),
        "metrics": metrics,
        "preferencesByMetric": _without_source_material_ids(
            audience.get("preferencesByMetric", {})
        ),
    }


def _script_unit(analysis):
    scripts = analysis.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}
    materials = []
    for raw in scripts.get("materials", []):
        if not isinstance(raw, dict):
            continue
        material_id = _text(raw.get("materialId"))
        labels = []
        for raw_label in raw.get("labels", []):
            if not isinstance(raw_label, dict):
                continue
            label = deepcopy(raw_label)
            level1 = _text(label.get("level1"))
            level2 = _text(label.get("level2"))
            if material_id and level1 and level2:
                label["evidenceRef"] = (
                    f"script:{material_id}:{level1}:{level2}"
                )
            labels.append(label)
        materials.append(
            {
                "materialId": material_id,
                "text": _text(raw.get("text")),
                "labels": labels,
                "evidence": deepcopy(raw.get("evidence", [])),
            }
        )
    return {
        "schemaVersion": 1,
        "unitId": "chapter03-script",
        "chapterId": "03",
        "period": deepcopy(analysis.get("period", {})),
        "materials": materials,
        "labelSummary": deepcopy(scripts.get("labelSummary", [])),
    }


def _interaction_with_evidence(interaction):
    result = deepcopy(interaction) if isinstance(interaction, dict) else {}
    metrics = result.get("metrics", {})
    if not isinstance(metrics, dict):
        return result
    for metric, metric_data in metrics.items():
        if not isinstance(metric_data, dict):
            continue
        if metric_data.get("available") and metric_data.get("totalMetricValue") is not None:
            metric_data["evidenceRef"] = f"interaction:{metric}:total"
        for bucket in metric_data.get("buckets", []):
            if isinstance(bucket, dict) and _text(bucket.get("label")):
                bucket["evidenceRef"] = (
                    f"interaction:{metric}:{_text(bucket['label'])}"
                )
    return result


def _commercial_evidence_refs(commercial):
    if not isinstance(commercial, dict):
        return []
    return [
        _text(metric.get("evidenceRef"))
        for metric in commercial.get("metrics", [])
        if isinstance(metric, dict) and _text(metric.get("evidenceRef"))
    ]


def _material_audience_preferences(audience):
    result = {}
    preferences = audience.get("preferencesByMetric", {}) if isinstance(audience, dict) else {}
    if not isinstance(preferences, dict):
        return result
    for metric, groups in preferences.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            audience_group = _text(group.get("audienceGroup"))
            for item in group.get("topMaterials", []):
                if not isinstance(item, dict):
                    continue
                material_id = _text(item.get("materialId"))
                if not material_id:
                    continue
                preference = {
                    key: deepcopy(value)
                    for key, value in item.items()
                    if key not in {"materialId", "materialName", "totalCost"}
                }
                preference["metric"] = metric
                preference["audienceGroup"] = audience_group
                preference["evidenceRef"] = (
                    f"audience:{metric}:user_group_label_name:{audience_group}"
                )
                result.setdefault(material_id, []).append(preference)
    return result


def _material_indexes(analysis, script_unit):
    script_refs = {}
    for material in script_unit["materials"]:
        material_id = material["materialId"]
        refs = [
            label["evidenceRef"]
            for label in material["labels"]
            if _text(label.get("evidenceRef"))
        ]
        script_refs[material_id] = list(dict.fromkeys(refs))

    highlight_index = {}
    highlights = analysis.get("highlights", {})
    for material in highlights.get("materials", []) if isinstance(highlights, dict) else []:
        if not isinstance(material, dict):
            continue
        material_id = _text(material.get("materialId"))
        highlight_index[material_id] = {
            "highlightRefs": [
                _text(frame.get("evidenceRef"))
                for frame in material.get("frames", [])
                if isinstance(frame, dict) and _text(frame.get("evidenceRef"))
            ],
            "missingFrames": deepcopy(material.get("missingFrames", [])),
            "frameCoverage": deepcopy(material.get("frameCoverage", {})),
        }

    return script_refs, highlight_index


def build_analysis_units(analysis):
    """Project one compatible fact snapshot into five isolated model inputs."""
    if not isinstance(analysis, dict) or analysis.get("schemaVersion") != 1:
        raise AnalysisUnitError("analysis snapshot must use schemaVersion 1")
    raw_materials = analysis.get("materials")
    if not isinstance(raw_materials, list) or not raw_materials:
        raise AnalysisUnitError("analysis snapshot materials must be a non-empty list")
    materials = [_identity(item) for item in raw_materials if isinstance(item, dict)]
    material_ids = [_text(item.get("materialId")) for item in materials]
    if not all(material_ids) or len(material_ids) != len(set(material_ids)):
        raise AnalysisUnitError("analysis snapshot material IDs must be unique")

    script_unit = _script_unit(analysis)
    audience_unit = _audience_unit(analysis)
    commercial = deepcopy(analysis.get("commercial", {}))
    interaction = _interaction_with_evidence(analysis.get("interaction", {}))
    portfolio_unit = {
        "schemaVersion": 1,
        "unitId": "chapter01-portfolio",
        "chapterId": "01",
        "period": deepcopy(analysis.get("period", {})),
        "materialIds": material_ids,
        "materials": materials,
        "commercial": commercial,
        "interaction": interaction,
        "commercialEvidenceRefs": _commercial_evidence_refs(commercial),
    }
    highlight_unit = {
        "schemaVersion": 1,
        "unitId": "chapter04-highlight",
        "chapterId": "04",
        "period": deepcopy(analysis.get("period", {})),
        "materials": deepcopy(analysis.get("highlights", {}).get("materials", [])),
    }

    audience_preferences = _material_audience_preferences(analysis.get("audience", {}))
    script_refs, highlight_index = _material_indexes(analysis, script_unit)
    material_units = []
    for identity in materials:
        material_id = identity["materialId"]
        highlight = highlight_index.get(material_id, {})
        material_units.append(
            {
                **deepcopy(identity),
                "audiencePreferences": audience_preferences.get(material_id, []),
                "scriptEvidenceRefs": script_refs.get(material_id, []),
                "highlightRefs": highlight.get("highlightRefs", []),
                "missingFrames": highlight.get("missingFrames", []),
                "frameCoverage": highlight.get("frameCoverage", {}),
            }
        )
    material_unit = {
        "schemaVersion": 1,
        "unitId": "chapter05-material",
        "chapterId": "05",
        "period": deepcopy(analysis.get("period", {})),
        "materials": material_units,
    }

    return {
        "chapter01-portfolio": portfolio_unit,
        "chapter02-audience": audience_unit,
        "chapter03-script": script_unit,
        "chapter04-highlight": highlight_unit,
        "chapter05-material": material_unit,
    }


def _compact_json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _frame_counts(analysis):
    successful = 0
    missing = 0
    highlights = analysis.get("highlights", {})
    materials = highlights.get("materials", []) if isinstance(highlights, dict) else []
    for material in materials:
        if not isinstance(material, dict):
            continue
        successful += sum(
            1 for frame in material.get("frames", []) if isinstance(frame, dict)
        )
        missing += sum(
            1 for frame in material.get("missingFrames", []) if isinstance(frame, dict)
        )
    return {"successfulCount": successful, "missingCount": missing}


def publish_analysis_units(output_dir, analysis):
    """Write compact units first and publish the hash manifest last."""
    units = build_analysis_units(analysis)
    serialized_units = []
    for unit_id, filename in UNIT_DEFINITIONS:
        raw = _compact_json_bytes(units[unit_id])
        contract = UNIT_CONTRACTS[unit_id]
        serialized_units.append(
            (
                filename,
                raw,
                {
                    "unitId": unit_id,
                    "chapterId": contract["chapterId"],
                    "path": filename,
                    "outputFields": list(contract["outputFields"]),
                    "allowedEvidencePrefixes": list(
                        contract["allowedEvidencePrefixes"]
                    ),
                    "byteSize": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
            )
        )

    material_ids = [
        _text(material.get("materialId"))
        for material in analysis.get("materials", [])
        if isinstance(material, dict)
    ]
    manifest = {
        "schemaVersion": 1,
        "executionMode": "single_model_call",
        "analysisSchemaVersion": 5,
        "sourceSnapshotSchemaVersion": analysis["schemaVersion"],
        "period": deepcopy(analysis.get("period", {})),
        "materialIds": material_ids,
        "promptPath": "../diagnostic-analysis-prompt.md",
        "compatibilitySnapshotPath": "../diagnostic-analysis-data.json",
        "frames": _frame_counts(analysis),
        "units": [item for _filename, _raw, item in serialized_units],
    }
    manifest_raw = _compact_json_bytes(manifest)

    unit_dir = Path(output_dir) / "analysis-units"
    manifest_path = unit_dir / "analysis-unit-manifest.json"
    staging_paths = []
    manifest_staging = unit_dir / (
        f".analysis-unit-manifest.{uuid.uuid4().hex}.json.tmp"
    )
    try:
        unit_dir.mkdir(parents=True, exist_ok=True)
        for filename, raw, _item in serialized_units:
            staging = unit_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
            staging.write_bytes(raw)
            staging_paths.append((staging, unit_dir / filename))
        manifest_staging.write_bytes(manifest_raw)

        manifest_path.unlink(missing_ok=True)
        for staging, target in staging_paths:
            os.replace(staging, target)
        os.replace(manifest_staging, manifest_path)
    finally:
        for staging, _target in staging_paths:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            manifest_staging.unlink(missing_ok=True)
        except OSError:
            pass
    return manifest_path, manifest
