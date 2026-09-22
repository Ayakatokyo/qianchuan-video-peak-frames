"""Build isolated chapter inputs from normalized diagnostic facts."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid
import re

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
        "outputFields": ["overallConclusion", "portfolioDimensionConclusions", "hotMaterialInsight"],
        "allowedEvidencePrefixes": ["commercial:"],
    },
    "chapter02-audience": {
        "chapterId": "02",
        "outputFields": ["batchInsights:audience"],
        "allowedEvidencePrefixes": ["audience:"],
    },
    "chapter03-script": {
        "chapterId": "03",
        "outputFields": ["batchInsights:script", "keywordCloudReview", "keywordCloudInsight"],
        "allowedEvidencePrefixes": ["script:"],
    },
    "chapter04-highlight": {
        "chapterId": "04",
        "outputFields": ["highlightDashboard", "customClassification", "materialAnalyses.highlightAnalyses"],
        "allowedEvidencePrefixes": ["highlight:"],
    },
    "chapter05-material": {
        "chapterId": "05",
        "outputFields": ["materialAnalyses"],
        "allowedEvidencePrefixes": ["audience:", "script:", "highlight:", "interaction:", "commercial:"],
    },
}


class AnalysisUnitError(ValueError):
    """Raised when normalized diagnostic facts cannot produce chapter inputs."""


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
    }


def build_display_facts(analysis):
    """Project deterministic facts used by report charts and fixed panels."""
    if not isinstance(analysis, dict) or analysis.get("schemaVersion") != 1:
        raise AnalysisUnitError("normalized diagnostic facts must use schemaVersion 1")
    scripts = analysis.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}
    return {
        "schemaVersion": 1,
        "sourceFactsSchemaVersion": analysis["schemaVersion"],
        "period": deepcopy(analysis.get("period", {})),
        "portfolio": deepcopy(analysis.get("portfolio", {})),
        "materials": deepcopy(analysis.get("materials", [])),
        "audience": deepcopy(analysis.get("audience", {})),
        # Full script text and per-material labels belong only to the AI unit;
        # the report display only needs the deterministic aggregate.
        "scripts": {
            "labelSummary": deepcopy(scripts.get("labelSummary", [])),
            "keywordCloud": deepcopy(scripts.get("keywordCloud", {})),
        },
        "interaction": deepcopy(analysis.get("interaction", {})),
        "delivery": deepcopy(analysis.get("delivery", {})),
        "highlights": deepcopy(analysis.get("highlights", {})),
        "commercial": deepcopy(analysis.get("commercial", {})),
    }


def script_label_evidence_refs(materials):
    """Canonical script references accepted by batch/material conclusions."""
    return {f"script:{material['materialId']}:{label['level1']}:{label['level2']}"
            for material in materials for label in material.get('labels', [])}


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
                # Keep the deterministic label chain in the per-material
                # package so the browser can render the formula/color mapping
                # without consulting the batch script unit.
                "formula": [
                    {"level1": _text(label.get("level1")), "level2": _text(label.get("level2"))}
                    for label in labels
                    if _text(label.get("level1")) and _text(label.get("level2"))
                ],
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
        # diagnostic_analysis_lib is the single producer of keywordCloud facts;
        # keep a fallback for callers that construct analysis dictionaries directly.
        "keywordCloud": deepcopy(scripts.get("keywordCloud"))
        if isinstance(scripts.get("keywordCloud"), dict)
        else _keyword_cloud(materials),
    }


_KEYWORD_MIN_FREQUENCY = 2
_KEYWORD_POS_PREFIXES = ("n", "v", "a", "eng")


def _script_keywords(text):
    # Reading frozen facts and checkpointing images never needs segmentation.
    try:
        import jieba.posseg as pseg
    except ImportError as exc:  # pragma: no cover - dependency gate handles this
        raise RuntimeError("jieba is required for deterministic Chinese keyword extraction") from exc
    tokens = []
    previous = None
    for item in pseg.cut(text):
        token = item.word.strip()
        flag = item.flag or ""
        is_content = bool(token and flag.startswith(_KEYWORD_POS_PREFIXES))
        if not is_content:
            previous = None
            continue
        if token.isascii():
            valid = len(token) >= 2 and not token.isdigit() and any(char.isalnum() for char in token)
            normalized = token.lower() if valid else ""
        else:
            normalized = token if re.search(r"[\u4e00-\u9fff]", token) else ""
        if normalized:
            if len(normalized) >= 2:
                tokens.append(normalized)
            if previous and len(previous) == 1 and len(normalized) == 1:
                tokens.append(previous + normalized)
            previous = normalized
    return tokens


def _keyword_cloud(materials):
    stats = {}
    for material in materials:
        material_id = _text(material.get("materialId"))
        for keyword in _script_keywords(_text(material.get("text"))):
            item = stats.setdefault(keyword, {"frequency": 0, "materialIds": set()})
            item["frequency"] += 1
            if material_id:
                item["materialIds"].add(material_id)
    keywords = []
    for keyword, item in sorted(stats.items(), key=lambda pair: (-pair[1]["frequency"], pair[0])):
        if item["frequency"] < _KEYWORD_MIN_FREQUENCY:
            continue
        material_ids = sorted(item["materialIds"])
        keywords.append({
            "keyword": keyword,
            "frequency": item["frequency"],
            "materialIds": material_ids,
            "evidenceRefs": [f"script:{material_id}:text:{keyword}" for material_id in material_ids],
        })
    return {"source": "facts.scripts.materials[*].text", "keywords": keywords}


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
            "frames": deepcopy(material.get("frames", [])),
            "highlightRefs": [
                _text(frame.get("evidenceRef"))
                for frame in material.get("frames", [])
                if isinstance(frame, dict) and _text(frame.get("evidenceRef"))
            ],
            "missingFrames": deepcopy(material.get("missingFrames", [])),
            "frameCoverage": deepcopy(material.get("frameCoverage", {})),
        }

    return script_refs, highlight_index


def _material_interaction(value):
    if not isinstance(value, dict):
        return {"metrics": []}
    metrics = value.get("metrics")
    if isinstance(metrics, list):
        return deepcopy(value)
    if isinstance(metrics, dict):
        return {
            "metrics": [
                {"metric": metric, **deepcopy(metric_data)}
                for metric, metric_data in metrics.items()
                if isinstance(metric_data, dict)
            ]
        }
    return {"metrics": []}


def _material_video(value):
    if not isinstance(value, dict):
        return {}
    # Signed playback URLs are delivery artifacts, not analysis facts.
    return {
        key: deepcopy(value[key])
        for key in ("durationSec", "materialUri")
        if key in value
    }


def build_analysis_units(analysis):
    """Project normalized diagnostic facts into five isolated model inputs."""
    if not isinstance(analysis, dict) or analysis.get("schemaVersion") != 1:
        raise AnalysisUnitError("normalized diagnostic facts must use schemaVersion 1")
    raw_materials = analysis.get("materials")
    if not isinstance(raw_materials, list) or not raw_materials:
        raise AnalysisUnitError("normalized diagnostic facts materials must be a non-empty list")
    source_materials = [item for item in raw_materials if isinstance(item, dict)]
    materials = [_identity(item) for item in source_materials]
    source_by_id = {
        _text(item.get("materialId")): item
        for item in source_materials
        if _text(item.get("materialId"))
    }
    material_ids = [_text(item.get("materialId")) for item in materials]
    if not all(material_ids) or len(material_ids) != len(set(material_ids)):
        raise AnalysisUnitError("normalized diagnostic facts material IDs must be unique")

    script_unit = _script_unit(analysis)
    audience_unit = _audience_unit(analysis)
    portfolio_unit = {
        "schemaVersion": 1,
        "unitId": "chapter01-portfolio",
        "chapterId": "01",
        "period": deepcopy(analysis.get("period", {})),
        "portfolio": deepcopy(analysis.get("portfolio", {})),
    }
    highlight_unit = {
        "schemaVersion": 1,
        "unitId": "chapter04-highlight",
        "chapterId": "04",
        "period": deepcopy(analysis.get("period", {})),
        "materials": deepcopy(analysis.get("highlights", {}).get("materials", [])),
    }

    script_refs, highlight_index = _material_indexes(analysis, script_unit)
    fallback_interaction = analysis.get("interaction", {}) if len(materials) == 1 else {}
    script_by_id = {
        _text(item.get("materialId")): item
        for item in script_unit.get("materials", [])
        if isinstance(item, dict) and _text(item.get("materialId"))
    }
    material_units = []
    for identity in materials:
        material_id = identity["materialId"]
        highlight = highlight_index.get(material_id, {})
        source_material = source_by_id.get(material_id, {})
        material_units.append(
            {
                **deepcopy(identity),
                "materialRanking": deepcopy(source_material.get("materialRanking", {})),
                "video": _material_video(source_material.get("video", {})),
                "audience": deepcopy(source_material.get("audience", {})),
                "script": deepcopy(source_material.get("script") or script_by_id.get(material_id, {})),
                "highlight": {
                    "frames": highlight.get("frames", []),
                    "missingFrames": highlight.get("missingFrames", []),
                    "frameCoverage": highlight.get("frameCoverage", {}),
                },
                "interactionTimeline": _material_interaction(
                    source_material.get("interactionTimeline") or fallback_interaction
                ),
                "evidenceIndex": {
                    **deepcopy(source_material.get("evidenceIndex", {})),
                    "script": script_refs.get(material_id, []),
                    "highlight": highlight.get("highlightRefs", []),
                },
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
    display_facts = build_display_facts(analysis)
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
    display_raw = _compact_json_bytes(display_facts)
    display_entry = {
        "path": "report-display-facts.json",
        "byteSize": len(display_raw),
        "sha256": hashlib.sha256(display_raw).hexdigest(),
    }
    manifest = {
        "schemaVersion": 2,
        "executionMode": "current_model_fragments",
        "analysisSchemaVersion": 5,
        "sourceFactsSchemaVersion": analysis["schemaVersion"],
        "period": deepcopy(analysis.get("period", {})),
        "materialIds": material_ids,
        "promptPath": "../diagnostic-analysis-prompt.md",
        "frames": _frame_counts(analysis),
        "units": [item for _filename, _raw, item in serialized_units],
        "displayFacts": display_entry,
    }
    manifest_raw = _compact_json_bytes(manifest)

    unit_dir = Path(output_dir) / "analysis-units"
    manifest_path = unit_dir / "analysis-unit-manifest.json"
    display_path = Path(output_dir) / "report-display-facts.json"
    staging_paths = []
    manifest_staging = unit_dir / (
        f".analysis-unit-manifest.{uuid.uuid4().hex}.json.tmp"
    )
    display_staging = display_path.with_name(
        f".{display_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        unit_dir.mkdir(parents=True, exist_ok=True)
        for filename, raw, _item in serialized_units:
            staging = unit_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
            staging.write_bytes(raw)
            staging_paths.append((staging, unit_dir / filename))
        manifest_staging.write_bytes(manifest_raw)
        display_staging.write_bytes(display_raw)

        manifest_path.unlink(missing_ok=True)
        for staging, target in staging_paths:
            os.replace(staging, target)
        os.replace(manifest_staging, manifest_path)
        os.replace(display_staging, display_path)
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
        try:
            display_staging.unlink(missing_ok=True)
        except OSError:
            pass
    return manifest_path, manifest
