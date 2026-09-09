"""PresentationReport v1 projection for the Qianchuan report."""

from copy import deepcopy

from portfolio_projection import project_portfolio_summary


CATEGORIES = ("portrait", "product", "other")
TABS = ("highlight", "interaction", "audience", "script", "decision")
CHAPTER01_POINTS = ("commercial",)
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
        candidates = _list(item.get("topMaterials"))
        item["topMaterials"] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or _text(candidate.get("materialId")) not in material_ids:
                continue
            item["topMaterials"].append({
                key: deepcopy(candidate[key])
                for key in ("rank", "materialId", "materialName", "audiencePercentage", "totalCost")
                if key in candidate
            })
        item["sourceMaterialIds"] = [candidate["materialId"] for candidate in item["topMaterials"]]
        projected.append(item)
    return projected


def _metrics(value):
    projected = {}
    for metric, raw_metric in _mapping(value).items():
        if not isinstance(raw_metric, dict):
            continue
        dimensions = {}
        for dimension_key, raw_dimension in _mapping(raw_metric.get("dimensions")).items():
            if not isinstance(raw_dimension, dict):
                continue
            dimension = {"buckets": []}
            for bucket in _list(raw_dimension.get("buckets")):
                if not isinstance(bucket, dict):
                    continue
                dimension["buckets"].append({
                    key: deepcopy(bucket[key])
                    for key in ("bucket", "metricValue", "metricPercentage", "sourceMaterialIds", "evidenceRefs")
                    if key in bucket
                })
            dimensions[dimension_key] = dimension
        projected[metric] = {"dimensions": dimensions}
    return projected


def _dimension(conclusion, topic):
    raw = _mapping(_mapping(conclusion).get("portfolioDimensionConclusions")).get(topic)
    if isinstance(raw, dict):
        return _text(raw.get("conclusion")), [_text(ref) for ref in _list(raw.get("evidenceRefs")) if _text(ref)]
    return _text(raw), []


def _hot_material_panel(portfolio):
    rule = _mapping(_mapping(portfolio).get("hotMaterialRule"))
    qualifying = []
    for raw in _list(rule.get("qualifyingMaterials")):
        if not isinstance(raw, dict) or not _text(raw.get("materialId")):
            continue
        qualifying.append({
            key: deepcopy(raw[key])
            for key in ("materialId", "materialName", "totalCost", "metrics", "evidenceRef")
            if key in raw
        })
    panel = {
        "thresholdCost": _text(rule.get("thresholdCost")),
        "comparison": _text(rule.get("comparison")),
        "qualifyingCount": rule.get("qualifyingCount", 0),
        "materials": qualifying,
        "source": "facts.portfolio.hotMaterialRule",
        "availability": "available" if rule else "unavailable",
    }
    return panel


def _ai_conclusion(conclusion, hot_material_rule):
    overall = _text(_mapping(conclusion).get("overallConclusion"))
    insight = _mapping(_mapping(conclusion).get("hotMaterialInsight"))
    hot_text = _text(insight.get("conclusion")) if hot_material_rule else ""
    paragraphs = [paragraph for paragraph in (overall, hot_text) if paragraph]
    evidence_refs = []
    for reference in _list(insight.get("evidenceRefs")):
        reference = _text(reference)
        if reference and reference not in evidence_refs:
            evidence_refs.append(reference)
    source = "ai.overallConclusion"
    if hot_material_rule and hot_text:
        source += " + ai.hotMaterialInsight"
    return {
        "paragraphs": paragraphs,
        "evidenceRefs": evidence_refs,
        "source": source,
        "availability": "available" if paragraphs else "unavailable",
    }


def _chapter01(data):
    conclusion = _mapping(data.get("conclusion"))
    # Chapter 01 is intentionally sourced only from the full portfolio facts.
    # RPA-selected ROI facts remain available to later chapters but are not a
    # fallback source for the portfolio chapter.
    facts = {"commercial": project_portfolio_summary(data.get("portfolio"))}
    points = []
    evidence_refs = []
    labels = {
        "commercial": ("素材榜单数据", ""),
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
            "source": f"facts.portfolio + ai.portfolioDimensionConclusions.{topic}",
            "availability": _availability(
                has_facts or bool(conclusion_text),
                has_facts and bool(conclusion_text) and bool(refs),
            ),
        })
    return {
        "id": "chapter01", "title": "素材榜单数据洞察", "points": points,
        "portfolio": facts["commercial"],
        "hotMaterialPanel": _hot_material_panel(facts["commercial"]),
        "aiConclusion": _ai_conclusion(
            conclusion,
            _mapping(facts["commercial"].get("hotMaterialRule")),
        ),
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
    metrics = _metrics(audience.get("metrics"))
    preferences_by_metric = {
        _text(metric): _preferences(items, material_ids)
        for metric, items in _mapping(audience.get("preferencesByMetric")).items()
        if _text(metric)
    }
    if not preferences_by_metric and _list(audience.get("preferences")):
        preferences_by_metric["SHOW_COUNT"] = _preferences(
            audience.get("preferences"), material_ids
        )
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
    for preference in [item for items in preferences_by_metric.values() for item in items]:
        has_refs = has_refs or bool(preference["sourceMaterialIds"])
    has_content = bool(metrics or preferences_by_metric)
    return {
        "id": "chapter02", "title": "人群洞察", "insights": insights,
        "metrics": metrics,
        "preferencesByMetric": preferences_by_metric,
        "source": "facts.audience + ai.batchInsights[audience]", "evidenceRefs": [],
        "availability": _availability(has_content, has_refs),
    }


def _chapter03(data, material_ids):
    scripts = _mapping(data.get("scripts"))
    summary = _list(scripts.get("labelSummary"))
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
    raw_cloud = _mapping(scripts.get("keywordCloud"))
    review = {
        _text(item.get("keyword")): _text(item.get("decision"))
        for item in _list(_mapping(data.get("conclusion")).get("keywordCloudReview"))
        if isinstance(item, dict) and _text(item.get("keyword"))
    }
    keywords = []
    cloud_refs = []
    for raw in _list(raw_cloud.get("keywords")):
        if not isinstance(raw, dict) or not _text(raw.get("keyword")):
            continue
        if review.get(_text(raw["keyword"])) == "exclude":
            continue
        refs = [ref for ref in _list(raw.get("evidenceRefs")) if _text(ref)]
        item = {
            "keyword": _text(raw["keyword"]),
            "frequency": raw.get("frequency", 0),
            "materialIds": _material_refs(raw.get("materialIds"), material_ids),
            "evidenceRefs": refs,
        }
        keywords.append(item)
        cloud_refs.extend(refs)
    ai_cloud = _mapping(_mapping(data.get("conclusion")).get("keywordCloudInsight"))
    selling_points = []
    valid_keywords = {item["keyword"] for item in keywords}
    valid_refs = set(cloud_refs) | set(evidence_refs)
    for raw in _list(ai_cloud.get("sellingPoints")):
        if not isinstance(raw, dict):
            continue
        keyword_refs = [ref for ref in _list(raw.get("keywordRefs")) if ref in valid_keywords]
        evidence = [ref for ref in _list(raw.get("evidenceRefs")) if ref in valid_refs]
        if _text(raw.get("title")) and _text(raw.get("description")) and keyword_refs and evidence:
            selling_points.append({"dimension": _text(raw.get("dimension")), "title": _text(raw["title"]), "description": _text(raw["description"]), "keywordRefs": keyword_refs, "evidenceRefs": evidence})
    keyword_panel = {
        "oneSentenceConclusion": _text(ai_cloud.get("oneSentenceConclusion")),
        "summary": _text(ai_cloud.get("summary")),
        "keywords": keywords,
        "sellingPoints": selling_points,
        "analysisStatus": ai_cloud.get("analysisStatus") or "missing",
        "limitation": _text(ai_cloud.get("limitation")),
        "warning": ("关键词事实存在，但 AI 未提炼卖点" if keywords else "关键词证据不足，暂无可展示卖点") if not selling_points else "",
        "source": "facts.scripts.keywordCloud + ai.keywordCloudInsight",
        "evidenceRefs": list(dict.fromkeys(cloud_refs + [ref for card in selling_points for ref in card["evidenceRefs"]])),
        "availability": _availability(bool(keywords), bool(keywords)),
    }
    return {
        "id": "chapter03", "title": "脚本洞察", "groups": groups, "insights": insights,
        "keywordCloudPanel": keyword_panel,
        "source": "facts.scripts + ai.batchInsights[script]", "evidenceRefs": evidence_refs,
        "availability": _availability(bool(groups), bool(evidence_refs) and has_refs),
    }


def _chapter04(data, materials):
    conclusion = data.get("conclusion") if isinstance(data.get("conclusion"), dict) else {}
    dashboard = conclusion.get("highlightDashboard") if isinstance(conclusion.get("highlightDashboard"), dict) else {}
    raw_groups = dashboard.get("groups") if isinstance(dashboard.get("groups"), list) else []
    by_category = {group.get("category"): group for group in raw_groups if isinstance(group, dict)}
    conclusion_text, conclusion_refs = _dimension(conclusion, "highlight")
    groups = {}
    total_refs = 0
    available_refs = 0
    for category in CATEGORIES:
        raw = by_category.get(category, {})
        refs = [_text(ref) for ref in raw.get("frameRefs", []) if _text(ref)]
        group_available = 0
        for ref in refs:
            total_refs += 1
            # Frame details are projected once under Chapter 05 material facts.
            # Chapter 04 only owns the AI classification refs and must not carry
            # a second copy of frame metadata/path data.
            if any(
                _frame_ref(material.get("materialId", ""), frame) == ref
                for material in materials
                for frame in material.get("highlightFrames", [])
                if isinstance(frame, dict) and _text(frame.get("framePath"))
            ):
                available_refs += 1
                group_available += 1
        group_availability = (
            "unavailable" if not refs
            else "available" if group_available == len(refs)
            else "partial"
        )
        groups[category] = {
            "id": f"chapter04-{category}",
            "category": category,
            "frameRefs": refs,
            "commonConclusion": (
                deepcopy(raw.get("commonConclusion"))
                if isinstance(raw.get("commonConclusion"), list)
                else _text(raw.get("commonConclusion"))
            ),
            "derivativeDirections": deepcopy(raw.get("derivativeDirections", [])) if isinstance(raw.get("derivativeDirections"), list) else [],
            "source": "facts.materials.highlightFrames + ai.highlightDashboard",
            "evidenceRefs": refs,
            "availability": group_availability,
        }
    availability = "available" if total_refs and available_refs == total_refs else ("partial" if available_refs else "unavailable")
    custom = conclusion.get("customClassification")
    batch_classification = None
    if isinstance(custom, dict):
        successful_frame_refs = {
            _frame_ref(material.get("materialId", ""), frame)
            for material in materials
            if material.get("extractionStatus") == "succeeded"
            for frame in material.get("highlightFrames", [])
            if isinstance(frame, dict) and _text(frame.get("framePath"))
        }
        total_frame_count = len(successful_frame_refs)
        projected_groups = []
        covered_frame_refs = set()
        for raw in custom.get("groups", []):
            if not isinstance(raw, dict):
                continue
            refs = [
                _text(ref)
                for ref in _list(raw.get("frameRefs"))
                if _text(ref) in successful_frame_refs
            ]
            covered_frame_refs.update(refs)
            frame_count = len(refs)
            projected_groups.append({
                "groupId": _text(raw.get("groupId")),
                "groupName": _text(raw.get("groupName")),
                "description": _text(raw.get("description")),
                "frameRefs": refs,
                "frameCount": frame_count,
                "percentage": round(frame_count / total_frame_count * 100, 2) if total_frame_count else 0,
                "commonConclusion": deepcopy(raw.get("commonConclusion", [])) if isinstance(raw.get("commonConclusion"), list) else [],
                "derivativeDirections": deepcopy(raw.get("derivativeDirections", [])) if isinstance(raw.get("derivativeDirections"), list) else [],
                "evidenceRefs": refs,
                "availability": "available" if frame_count else "unavailable",
            })
        unclassified_refs = [
            _text(ref)
            for ref in _list(custom.get("unclassifiedFrameRefs"))
            if _text(ref) in successful_frame_refs
        ]
        covered_frame_refs.update(unclassified_refs)
        batch_availability = _text(custom.get("availability")) or "available"
        if (
            not total_frame_count
            or len(covered_frame_refs) != total_frame_count
            or unclassified_refs
            or any(not group["frameRefs"] for group in projected_groups)
        ):
            batch_availability = "partial" if total_frame_count else "unavailable"
        distribution = [
            {
                "groupId": group["groupId"],
                "groupName": group["groupName"],
                "frameCount": group["frameCount"],
                "percentage": group["percentage"],
            }
            for group in projected_groups
            if group["frameCount"] > 0
        ]
        if unclassified_refs:
            distribution.append({
                "groupId": "unclassified",
                "groupName": "未归类",
                "frameCount": len(unclassified_refs),
                "percentage": round(len(unclassified_refs) / total_frame_count * 100, 2) if total_frame_count else 0,
            })
        batch_classification = {
            "ruleId": _text(custom.get("ruleId")),
            "ruleName": _text(custom.get("ruleName")),
            "ruleDescription": _text(custom.get("ruleDescription")),
            "ruleVersion": _text(custom.get("ruleVersion")),
            "inputFrameManifestHash": _text(custom.get("inputFrameManifestHash")),
            "inputFrameCount": total_frame_count,
            "groupCount": len(projected_groups),
            "groups": projected_groups,
            "distribution": distribution,
            "unclassifiedFrameRefs": unclassified_refs,
            "coverage": round(len(covered_frame_refs) / total_frame_count, 4) if total_frame_count else 0,
            "availability": batch_availability,
            "source": "ai.customClassification + facts.materials.highlightFrames",
        }
    result = {
        "id": "chapter04",
        "title": "高光画面洞察",
        "availability": availability,
        "categoryOrder": list(CATEGORIES),
        "classificationModes": ["standard"] + (["batch"] if batch_classification is not None else []),
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
    if batch_classification is not None:
        result["batchClassification"] = batch_classification
    return result


def _chapter05(materials, ai_schema_version, canonical_facts=None):
    canonical_by_id = {
        _text(item.get("materialId")): item
        for item in _list(_mapping(canonical_facts).get("materials"))
        if isinstance(item, dict) and _text(item.get("materialId"))
    }
    projected = []
    for material in materials:
        analysis = material.get("analysis") if isinstance(material.get("analysis"), dict) else {}
        canonical = canonical_by_id.get(material["materialId"], {})
        canonical_highlight = canonical.get("highlight") if isinstance(canonical.get("highlight"), dict) else {}
        canonical_frames = canonical_highlight.get("frames") if isinstance(canonical_highlight.get("frames"), list) else []
        source_frames = canonical_frames or material.get("highlightFrames", [])
        source_audience = canonical.get("audience") or material.get("materialAudience", {})
        source_script = canonical.get("script") or material.get("scriptAnalysis", {})
        if isinstance(source_script, dict):
            source_script = deepcopy(source_script)
            # Older Chapter05 packages carried labels/evidence but no formula.
            # Preserve the established formula chain as a compatibility
            # fallback so transcript/formula colors remain linked.
            if not isinstance(source_script.get("formula"), list) or not source_script.get("formula"):
                legacy_script = material.get("scriptAnalysis", {})
                legacy_formula = legacy_script.get("formula") if isinstance(legacy_script, dict) else None
                if isinstance(legacy_formula, list) and legacy_formula:
                    source_script["formula"] = deepcopy(legacy_formula)
                else:
                    source_script["formula"] = [
                        {
                            "level1": _text(label.get("level1")),
                            "level2": _text(label.get("level2")),
                        }
                        for label in _list(source_script.get("labels"))
                        if isinstance(label, dict)
                        and _text(label.get("level1"))
                        and _text(label.get("level2"))
                    ]
        source_interaction = canonical.get("interactionTimeline") or material.get("interactionTimeline", {})
        frames = []
        for frame in source_frames:
            if isinstance(frame, dict):
                item = deepcopy(frame)
                item["frameRef"] = _frame_ref(material["materialId"], frame)
                item["source"] = "facts.materials.highlightFrames"
                frames.append(item)
        tabs = [
            {"id": tab, "label": label, "source": source, "availability": availability}
            for tab, label, source, availability in (
                ("highlight", "高光帧观察", "facts.chapter05.highlight", "available" if frames else "unavailable"),
                ("interaction", "互动时序分析", "facts.chapter05.interactionTimeline", "available" if source_interaction else "unavailable"),
                ("audience", "人群分析", "facts.chapter05.audience", "available" if source_audience else "unavailable"),
                ("script", "脚本分析", "facts.chapter05.script", "available" if source_script else "unavailable"),
                ("decision", "综合判断与下一步", "ai.materialAnalyses", "available" if analysis.get("decision") or analysis.get("integratedConclusion") or analysis.get("nextAction") else "unavailable"),
            )
        ]
        projected.append({
            "id": f"chapter05-material-{material['materialId']}",
            "materialId": material["materialId"],
            "materialName": material.get("materialName", ""),
            "materialRanking": deepcopy(material.get("materialRanking", {})),
            "tabs": tabs,
            "audience": deepcopy(source_audience),
            "script": deepcopy(source_script),
            "highlight": {"frames": frames, "analyses": deepcopy(analysis.get("highlightAnalyses", []))},
            "interaction": deepcopy(source_interaction),
            "video": deepcopy(material.get("video", {})),
            "analysis": {
                **{
                    key: deepcopy(analysis.get(key))
                    for key in ("audienceConclusion", "scriptConclusion", "interactionConclusion", "integratedConclusion", "decision", "nextAction", "frameCoverageNote", "evidenceRefs")
                    if key in analysis
                },
                "source": "ai.materialAnalyses",
            },
            "source": "facts.chapter05 + ai.materialAnalyses",
            "availability": "available" if (
                frames
                and ai_schema_version in {4, 5}
                and analysis.get("evidenceRefs")
            ) else "partial",
        })
    return {
        "id": "chapter05",
        "title": "单素材深度分析",
        "materials": projected,
        "defaultMaterialId": projected[0]["materialId"] if projected else None,
        "source": "facts.chapter05 + ai.materialAnalyses",
        "availability": "available" if projected else "unavailable",
    }


def build_presentation_report(data):
    data = data if isinstance(data, dict) else {}
    display_facts = data.get("displayFacts")
    if isinstance(display_facts, dict):
        fact_data = {
            **data,
            **{
                key: deepcopy(value)
                for key, value in display_facts.items()
                if key not in {"schemaVersion", "sourceFactsSchemaVersion", "materials"}
            },
        }
    else:
        fact_data = data
    materials = _materials(data)
    material_ids = {item["materialId"] for item in materials}
    report = {
        "presentationSchemaVersion": 1,
        "analysisQuality": deepcopy(data.get("analysisQuality", {})),
        "report": {"title": "千川素材分析报告", "runId": data.get("runId"), "period": deepcopy(data.get("period", {})), "status": data.get("status"), "sections": list(CHAPTER_SECTIONS)},
        "materials": [{"materialId": item["materialId"], "materialName": item.get("materialName", ""), "rank": item.get("rank"), "source": "facts.materials"} for item in materials],
        "chapters": {
            "chapter01": _chapter01(fact_data), "chapter02": _chapter02(fact_data, material_ids),
            "chapter03": _chapter03(fact_data, material_ids), "chapter04": _chapter04(fact_data, materials),
            "chapter05": _chapter05(
                materials,
                (data.get("conclusion") or {}).get("schemaVersion"),
                data.get("chapter05Facts"),
            ),
        },
        "interactions": {"source": "report-display-facts.interaction", "availability": "available" if fact_data.get("interaction") else "unavailable"},
        "extensions": {"qianchuan": {"aiSchemaVersion": (data.get("conclusion") or {}).get("schemaVersion"), "displayFactSource": "report-display-facts.json"}},
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
    classification_modes = chapter04.get("classificationModes")
    if classification_modes is not None:
        expected_modes = ["standard"] + (["batch"] if "batchClassification" in chapter04 else [])
        if classification_modes != expected_modes:
            raise ValueError("chapter04 classificationModes must use fixed order")
    frame_refs = []
    for category in CATEGORIES:
        group = chapter04.get("groups", {}).get(category, {})
        frame_refs.extend(group.get("frameRefs", []))
    if len(frame_refs) != len(set(frame_refs)):
        raise ValueError("frameRef must belong to only one category")
    batch = chapter04.get("batchClassification")
    if isinstance(batch, dict):
        batch_refs = []
        for group in _list(batch.get("groups")):
            if not isinstance(group, dict):
                raise ValueError("batch classification groups must be objects")
            refs = _list(group.get("frameRefs"))
            if group.get("frameCount") != len(refs):
                raise ValueError("batch classification frameCount must match frameRefs")
            batch_refs.extend(refs)
        batch_refs.extend(_list(batch.get("unclassifiedFrameRefs")))
        if len(batch_refs) != len(set(batch_refs)):
            raise ValueError("batch classification frameRef must belong to only one group")
        distribution = batch.get("distribution")
        if not isinstance(distribution, list):
            raise ValueError("batch classification distribution must be a list")
        expected_distribution = [
            {
                "groupId": group.get("groupId"),
                "groupName": group.get("groupName"),
                "frameCount": group.get("frameCount"),
                "percentage": group.get("percentage"),
            }
            for group in _list(batch.get("groups"))
            if group.get("frameCount", 0) > 0
        ]
        unclassified = _list(batch.get("unclassifiedFrameRefs"))
        if unclassified:
            total = batch.get("inputFrameCount", 0)
            expected_distribution.append({
                "groupId": "unclassified",
                "groupName": "未归类",
                "frameCount": len(unclassified),
                "percentage": round(len(unclassified) / total * 100, 2) if total else 0,
            })
        distribution_ids = [item.get("groupId") for item in distribution if isinstance(item, dict)]
        if len(distribution_ids) != len(distribution) or len(distribution_ids) != len(set(distribution_ids)):
            raise ValueError("batch classification distribution groupId must be unique")
        if distribution != expected_distribution:
            raise ValueError("batch classification distribution does not match groups")
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
