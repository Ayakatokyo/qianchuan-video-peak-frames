"""PresentationReport v1 projection for the Qianchuan report."""

from copy import deepcopy


CATEGORIES = ("portrait", "product", "other")
TABS = ("highlight", "interaction", "audience", "script", "decision")


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
        groups[category] = {
            "id": f"chapter04-{category}",
            "category": category,
            "frameRefs": refs,
            "frames": cards,
            "commonConclusion": _text(raw.get("commonConclusion")),
            "derivativeDirections": deepcopy(raw.get("derivativeDirections", [])) if isinstance(raw.get("derivativeDirections"), list) else [],
            "source": "ai.highlightDashboard",
            "evidenceRefs": refs,
            "availability": "available" if cards else "unavailable",
        }
    availability = "available" if total_refs and available_refs == total_refs else ("partial" if available_refs else "unavailable")
    return {
        "id": "chapter04",
        "title": "高光画面洞察",
        "availability": availability,
        "categoryOrder": list(CATEGORIES),
        "groups": groups,
        "conclusion": _text((conclusion.get("portfolioDimensionConclusions") or {}).get("highlight")),
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
            "analysis": {key: deepcopy(analysis.get(key)) for key in ("audienceConclusion", "scriptConclusion", "interactionConclusion", "integratedConclusion", "decision", "nextAction", "frameCoverageNote", "evidenceRefs") if key in analysis},
            "source": "facts.materials + ai.materialAnalyses",
            "evidenceRefs": deepcopy(analysis.get("evidenceRefs", [])) if isinstance(analysis.get("evidenceRefs"), list) else [],
            "availability": "available" if (
                material.get("highlightFrames")
                and ai_schema_version in {4, 5}
                and analysis.get("evidenceRefs")
            ) else "partial",
        })
    return {"id": "chapter05", "title": "单素材深度分析", "materials": projected, "defaultMaterialId": projected[0]["materialId"] if projected else None, "availability": "available" if projected else "unavailable"}


def build_presentation_report(data):
    materials = _materials(data if isinstance(data, dict) else {})
    report = {
        "presentationSchemaVersion": 1,
        "report": {"title": "千川素材分析报告", "runId": data.get("runId"), "period": deepcopy(data.get("period", {})), "status": data.get("status"), "sections": ["chapter04", "chapter05"]},
        "materials": [{"materialId": item["materialId"], "materialName": item.get("materialName", ""), "rank": item.get("rank"), "source": "facts.materials"} for item in materials],
        "chapters": {"chapter01": {"availability": "unavailable"}, "chapter02": {"availability": "unavailable"}, "chapter03": {"availability": "unavailable"}, "chapter04": _chapter04(data, materials), "chapter05": _chapter05(materials, (data.get("conclusion") or {}).get("schemaVersion"))},
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
    chapter04 = report.get("chapters", {}).get("chapter04", {})
    if list(chapter04.get("groups", {})) != list(CATEGORIES):
        raise ValueError("chapter04 groups must use fixed category order")
    chapter05 = report.get("chapters", {}).get("chapter05", {})
    material_ids = {item.get("materialId") for item in materials}
    for item in chapter05.get("materials", []):
        if item.get("materialId") not in material_ids:
            raise ValueError("chapter05 materialId must exist in materials")
        if [tab.get("id") for tab in item.get("tabs", [])] != list(TABS):
            raise ValueError("chapter05 tabs must use fixed order")
    return report
