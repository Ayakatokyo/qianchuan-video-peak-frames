"""PresentationReport v1 projection for the Qianchuan report."""

from copy import deepcopy


CATEGORIES = ("portrait", "product", "other")
TABS = ("highlight", "interaction", "audience", "script", "decision")
CHAPTER01_POINTS = ("commercial", "audience", "script", "highlight", "interaction")
CHAPTER_SECTIONS = tuple(f"chapter0{index}" for index in range(1, 6))


def _text(value):
    return str(value).strip() if value is not None else ""


def _frame_ref(material_id, frame):
    explicit = _text(frame.get("frameRef")) if isinstance(frame, dict) else ""
    rank_type = _text(frame.get("rankType")) if isinstance(frame, dict) else ""
    return explicit or (f"highlight:{material_id}:{rank_type}" if rank_type else "")


def _materials(data):
    result = []
    for material in data.get("materials", []):
        if not isinstance(material, dict) or not _text(material.get("materialId")):
            continue
        item = deepcopy(material)
        item["materialId"] = _text(item["materialId"])
        result.append(item)
    return result


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _material_refs(value, material_ids):
    return [ref for ref in (_text(item) for item in _list(value)) if ref in material_ids]


def _availability(has_content, has_evidence=True):
    if not has_content:
        return "unavailable"
    return "available" if has_evidence else "partial"


def _preferences(value, material_ids):
    projected = []
    for preference in _list(value):
        if not isinstance(preference, dict):
            continue
        item = deepcopy(preference)
        item["topMaterials"] = [
            deepcopy(candidate)
            for candidate in _list(item.get("topMaterials"))
            if isinstance(candidate, dict) and _text(candidate.get("materialId")) in material_ids
        ]
        item["sourceMaterialIds"] = [candidate["materialId"] for candidate in item["topMaterials"]]
        projected.append(item)
    return projected


def _dimension(conclusion, topic):
    raw = _mapping(_mapping(conclusion).get("portfolioDimensionConclusions")).get(topic)
    if isinstance(raw, dict):
        return _text(raw.get("conclusion")), [_text(ref) for ref in _list(raw.get("evidenceRefs")) if _text(ref)]
    return _text(raw), []


def _chapter01(data):
    conclusion = _mapping(data.get("conclusion"))
    facts = {
        "commercial": _mapping(data.get("topMaterialRoi")),
        "audience": _mapping(data.get("audience")),
        "script": _mapping(data.get("scripts")),
        "highlight": _mapping(conclusion.get("highlightDashboard")),
        "interaction": _mapping(data.get("interaction")),
    }
    points = []
    evidence_refs = []
    labels = {
        "commercial": ("素材列表", "从素材列表数据来看："),
        "audience": ("人群", "从人群数据来看："),
        "script": ("脚本", "从脚本数据来看："),
        "highlight": ("高光画面", "从高光画面数据来看："),
        "interaction": ("互动时序", "从互动时序数据来看："),
    }
    for topic in CHAPTER01_POINTS:
        conclusion_text, refs = _dimension(conclusion, topic)
        evidence_refs.extend(refs)
        has_facts = bool(facts[topic])
        label, lead_in = labels[topic]
        points.append({
            "id": topic,
            "label": label,
            "leadIn": lead_in,
            "conclusion": conclusion_text,
            "evidenceRefs": refs,
            "source": f"facts.{topic} + ai.portfolioDimensionConclusions.{topic}",
            "availability": _availability(
                has_facts or bool(conclusion_text),
                has_facts and bool(conclusion_text) and bool(refs),
            ),
        })
    return {
        "id": "chapter01", "title": "组合结论", "points": points,
        "overallConclusion": _text(conclusion.get("overallConclusion")),
        "source": "facts + ai.portfolioDimensionConclusions", "evidenceRefs": evidence_refs,
        "availability": _availability(
            any(point["availability"] != "unavailable" for point in points),
            all(point["availability"] == "available" for point in points),
        ),
    }


def _chapter02(data, material_ids):
    audience = _mapping(data.get("audience"))
    insights = [
        deepcopy(insight)
        for insight in _list(_mapping(data.get("conclusion")).get("batchInsights"))
        if isinstance(insight, dict) and _text(insight.get("topic")) == "audience"
    ]
    metrics = deepcopy(_mapping(audience.get("metrics")))
    preferences = _preferences(audience.get("preferences"), material_ids)
    preferences_by_metric = {
        _text(metric): _preferences(items, material_ids)
        for metric, items in _mapping(audience.get("preferencesByMetric")).items()
        if _text(metric)
    }
    has_refs = False
    for metric_key, metric in metrics.items():
        for dimension_key, dimension in _mapping(metric).get("dimensions", {}).items():
            for bucket in _list(_mapping(dimension).get("buckets")):
                if isinstance(bucket, dict):
                    bucket["sourceMaterialIds"] = _material_refs(bucket.get("sourceMaterialIds"), material_ids)
                    bucket["evidenceRefs"] = [
                        f"audience:{metric_key}:{dimension_key}:{_text(bucket.get('bucket'))}"
                    ] if _text(bucket.get("bucket")) else []
                    has_refs = has_refs or bool(bucket["sourceMaterialIds"])
    for preference in preferences + [item for items in preferences_by_metric.values() for item in items]:
        has_refs = has_refs or bool(preference["sourceMaterialIds"])
    has_content = bool(metrics or preferences or preferences_by_metric)
    return {
        "id": "chapter02", "title": "人群洞察", "insights": insights,
        "metrics": metrics, "preferences": preferences,
        "preferencesByMetric": preferences_by_metric,
        "source": "facts.audience + ai.batchInsights[audience]", "evidenceRefs": [],
        "availability": _availability(has_content, has_refs),
    }


def _chapter03(data, material_ids):
    summary = _list(_mapping(data.get("scripts")).get("labelSummary"))
    groups = []
    by_level1 = {}
    for raw in summary:
        if not isinstance(raw, dict) or not _text(raw.get("level1")) or not _text(raw.get("level2")):
            continue
        level1 = _text(raw.get("level1"))
        group = by_level1.setdefault(level1, {
            "level1": level1,
            "labels": [],
            "source": "facts.scripts.labelSummary",
        })
        refs = [
            f"script:{material_id}:{level1}:{_text(raw.get('level2'))}"
            for material_id in _material_refs(raw.get("materialIds"), material_ids)
        ]
        group["labels"].append({
            "level2": _text(raw.get("level2")), "occurrenceCount": raw.get("occurrenceCount", 0),
            "materialIds": _material_refs(raw.get("materialIds"), material_ids),
            "evidenceRefs": refs,
        })
    for group in by_level1.values():
        groups.append(group)
    evidence_refs = [item for group in groups for label in group["labels"] for item in label["evidenceRefs"]]
    has_refs = any(label["materialIds"] for group in groups for label in group["labels"])
    insights = []
    for raw in _list(_mapping(data.get("conclusion")).get("batchInsights")):
        if not isinstance(raw, dict) or _text(raw.get("topic")) != "script":
            continue
        # Script insight titles are AI-generated and already carry their dimension prefix.
        # Keep them intact so the renderer does not repeat the prefix via summary.
        insight = deepcopy(raw)
        insight["source"] = "ai.batchInsights[script]"
        insights.append(insight)
    return {
        "id": "chapter03", "title": "脚本洞察", "groups": groups, "insights": insights,
        "source": "facts.scripts + ai.batchInsights[script]", "evidenceRefs": evidence_refs,
        "availability": _availability(bool(groups), bool(evidence_refs) and has_refs),
    }


def _frame_index(materials):
    index = {}
    for material in materials:
        material_id = material["materialId"]
        for frame in material.get("highlightFrames", []):
            if not isinstance(frame, dict):
                continue
            ref = _frame_ref(material_id, frame)
            if ref:
                index[ref] = {
                    "frameRef": ref,
                    "materialId": material_id,
                    "rankType": frame.get("rankType"),
                    "durationSec": frame.get("durationSec"),
                    "metricValue": frame.get("metricValue"),
                    "framePath": frame.get("framePath", ""),
                    "source": "facts.materials.highlightFrames",
                    "availability": "available",
                }
    return index


def _chapter04(data, materials):
    conclusion = data.get("conclusion") if isinstance(data.get("conclusion"), dict) else {}
    dashboard = conclusion.get("highlightDashboard") if isinstance(conclusion.get("highlightDashboard"), dict) else {}
    raw_groups = dashboard.get("groups") if isinstance(dashboard.get("groups"), list) else []
    by_category = {group.get("category"): group for group in raw_groups if isinstance(group, dict)}
    frames = _frame_index(materials)
    conclusion_text, conclusion_refs = _dimension(conclusion, "highlight")
    groups = {}
    total_refs = 0
    available_refs = 0
    for category in CATEGORIES:
        raw = by_category.get(category, {})
        refs = [_text(ref) for ref in raw.get("frameRefs", []) if _text(ref)]
        cards = []
        for ref in refs:
            total_refs += 1
            card = deepcopy(frames.get(ref, {"frameRef": ref, "availability": "unavailable", "source": "facts.materials.highlightFrames"}))
            if card.get("availability") == "available":
                available_refs += 1
            cards.append(card)
        group_availability = (
            "unavailable" if not cards
            else "available" if available_refs == len(cards)
            else "partial"
        )
        groups[category] = {
            "id": f"chapter04-{category}",
            "category": category,
            "frameRefs": refs,
            "frames": cards,
            "commonConclusion": _text(raw.get("commonConclusion")),
            "derivativeDirections": deepcopy(raw.get("derivativeDirections", [])) if isinstance(raw.get("derivativeDirections"), list) else [],
            "source": "facts.materials.highlightFrames + ai.highlightDashboard",
            "evidenceRefs": refs,
            "availability": group_availability,
        }
    availability = "available" if total_refs and available_refs == total_refs else ("partial" if available_refs else "unavailable")
    return {
        "id": "chapter04",
        "title": "高光画面洞察",
        "availability": availability,
        "categoryOrder": list(CATEGORIES),
        "groups": groups,
        "conclusion": {
            "text": conclusion_text,
            "evidenceRefs": conclusion_refs,
            "source": "ai.portfolioDimensionConclusions.highlight",
            "availability": _availability(bool(conclusion_text), bool(conclusion_refs)),
        },
        "source": "facts.materials.highlightFrames + ai.highlightDashboard",
        "evidenceRefs": [ref for group in groups.values() for ref in group["frameRefs"]],
    }


def _chapter05(materials, ai_schema_version):
    projected = []
    for material in materials:
        analysis = material.get("analysis") if isinstance(material.get("analysis"), dict) else {}
        frames = []
        for frame in material.get("highlightFrames", []):
            if isinstance(frame, dict):
                item = deepcopy(frame)
                item["frameRef"] = _frame_ref(material["materialId"], frame)
                item["source"] = "facts.materials.highlightFrames"
                frames.append(item)
        tabs = [
            {"id": tab, "label": label, "source": source, "availability": availability}
            for tab, label, source, availability in (
                ("highlight", "高光帧观察", "facts.materials.highlightFrames", "available" if frames else "unavailable"),
                ("interaction", "互动时序分析", "facts.interactionTimeline", "available" if material.get("interactionTimeline") else "unavailable"),
                ("audience", "人群分析", "facts.materialAudience", "available" if material.get("materialAudience") else "unavailable"),
                ("script", "脚本分析", "facts.scriptAnalysis", "available" if material.get("scriptAnalysis") else "unavailable"),
                ("decision", "综合判断与下一步", "ai.materialAnalyses", "available" if analysis.get("decision") or analysis.get("integratedConclusion") or analysis.get("nextAction") else "unavailable"),
            )
        ]
        projected.append({
            "id": f"chapter05-material-{material['materialId']}",
            "materialId": material["materialId"],
            "materialName": material.get("materialName", ""),
            "tabs": tabs,
            "audience": deepcopy(material.get("materialAudience", {})),
            "script": deepcopy(material.get("scriptAnalysis", {})),
            "highlight": {"frames": frames, "analyses": deepcopy(analysis.get("highlightAnalyses", []))},
            "interaction": deepcopy(material.get("interactionTimeline", {})),
            "video": deepcopy(material.get("video", {})),
            "analysis": {
                **{
                    key: deepcopy(analysis.get(key))
                    for key in ("audienceConclusion", "scriptConclusion", "interactionConclusion", "integratedConclusion", "decision", "nextAction", "frameCoverageNote", "evidenceRefs")
                    if key in analysis
                },
                "source": "ai.materialAnalyses",
            },
            "source": "facts.materials + ai.materialAnalyses",
            "evidenceRefs": deepcopy(analysis.get("evidenceRefs", [])) if isinstance(analysis.get("evidenceRefs"), list) else [],
            "availability": "available" if (
                material.get("highlightFrames")
                and ai_schema_version in {4, 5}
                and analysis.get("evidenceRefs")
            ) else "partial",
        })
    return {
        "id": "chapter05",
        "title": "单素材深度分析",
        "materials": projected,
        "defaultMaterialId": projected[0]["materialId"] if projected else None,
        "source": "facts.materials + ai.materialAnalyses",
        "availability": "available" if projected else "unavailable",
    }


def build_presentation_report(data):
    data = data if isinstance(data, dict) else {}
    materials = _materials(data)
    material_ids = {item["materialId"] for item in materials}
    report = {
        "presentationSchemaVersion": 1,
        "report": {"title": "千川素材分析报告", "runId": data.get("runId"), "period": deepcopy(data.get("period", {})), "status": data.get("status"), "sections": list(CHAPTER_SECTIONS)},
        "materials": [{"materialId": item["materialId"], "materialName": item.get("materialName", ""), "rank": item.get("rank"), "source": "facts.materials"} for item in materials],
        "chapters": {
            "chapter01": _chapter01(data), "chapter02": _chapter02(data, material_ids),
            "chapter03": _chapter03(data, material_ids), "chapter04": _chapter04(data, materials),
            "chapter05": _chapter05(materials, (data.get("conclusion") or {}).get("schemaVersion")),
        },
        "interactions": {"source": "facts.interaction", "availability": "available" if data.get("interaction") else "unavailable"},
        "extensions": {"qianchuan": {"aiSchemaVersion": (data.get("conclusion") or {}).get("schemaVersion")}},
    }
    validate_presentation_report(report)
    return report


def validate_presentation_report(report):
    if not isinstance(report, dict) or report.get("presentationSchemaVersion") != 1:
        raise ValueError("presentationSchemaVersion must be 1")
    materials = report.get("materials")
    if not isinstance(materials, list):
        raise ValueError("materials must be a list")
    ids = [item.get("materialId") for item in materials if isinstance(item, dict)]
    if len(ids) != len(set(ids)) or any(not _text(item) for item in ids):
        raise ValueError("materials must have unique materialId")
    if report.get("report", {}).get("sections") != list(CHAPTER_SECTIONS):
        raise ValueError("report sections must use fixed chapter order")
    chapters = report.get("chapters", {})
    chapter01 = chapters.get("chapter01", {})
    if [point.get("id") for point in chapter01.get("points", [])] != list(CHAPTER01_POINTS):
        raise ValueError("chapter01 points must use fixed order")
    chapter04 = chapters.get("chapter04", {})
    if list(chapter04.get("groups", {})) != list(CATEGORIES):
        raise ValueError("chapter04 groups must use fixed category order")
    frame_refs = []
    for category in CATEGORIES:
        group = chapter04.get("groups", {}).get(category, {})
        frame_refs.extend(group.get("frameRefs", []))
    if len(frame_refs) != len(set(frame_refs)):
        raise ValueError("frameRef must belong to only one category")
    chapter05 = chapters.get("chapter05", {})
    material_ids = {item.get("materialId") for item in materials}
    for item in chapter05.get("materials", []):
        if item.get("materialId") not in material_ids:
            raise ValueError("chapter05 materialId must exist in materials")
        if [tab.get("id") for tab in item.get("tabs", [])] != list(TABS):
            raise ValueError("chapter05 tabs must use fixed order")
    _validate_material_references(chapters.get("chapter02", {}), material_ids)
    _validate_material_references(chapters.get("chapter03", {}), material_ids)
    return report


def _validate_material_references(value, material_ids):
    if isinstance(value, list):
        for item in value:
            _validate_material_references(item, material_ids)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "materialId" and _text(item) not in material_ids:
                raise ValueError("projected materialId must exist in materials")
            if key in {"materialIds", "sourceMaterialIds"}:
                for material_id in _list(item):
                    if _text(material_id) not in material_ids:
                        raise ValueError("projected materialId must exist in materials")
            _validate_material_references(item, material_ids)
