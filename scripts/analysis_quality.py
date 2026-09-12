"""Evidence-bound review of heuristic findings; never generates analysis prose."""

import hashlib
import json
from pathlib import Path


POLICY_VERSION = "evidence-first-v2"
HIGHLIGHT_DEPTH_POLICY_MARKER = "<!-- highlight-depth-policy: mechanism-depth-v1 -->"


def highlight_depth_findings(conclusion):
    """Check the new Prompt's depth contract; never split frames or write prose.

    Called only for a frozen Prompt carrying the policy marker. Historical
    candidates keep their original contract unless explicitly re-authored.
    """
    results = []

    def check_limitation(container, key, field):
        if key in container and not isinstance(container[key], str):
            results.append(finding("error", "evidence limitation must be a string", field=field))

    def require_count(items, minimum, maximum, field, limitation, refs):
        if not isinstance(items, list):
            results.append(finding("error", "structured insight/group list required", field=field))
            return
        if minimum <= len(items) <= maximum:
            return
        if len(items) < minimum and isinstance(limitation, str) and limitation.strip() and refs:
            results.append(finding(
                "review", f"Review evidence shortage: {field} has {len(items)}; expected {minimum}-{maximum}",
                field=field, evidence_refs=refs, examples=[{"limitation": limitation}],
                required_decision="evidence_limited"))
        else:
            results.append(finding("error", f"{field} requires {minimum}-{maximum} entries; fewer require an explicit evidence limitation",
                                   field=field, evidence_refs=refs, count=len(items)))

    custom = conclusion.get("customClassification", {})
    check_limitation(custom, "classificationLimitation", "customClassification.classificationLimitation")
    groups = custom.get("groups", [])
    refs = [ref for group in groups for ref in group.get("frameRefs", [])]
    require_count(groups, 3, 5, "customClassification.groups", custom.get("classificationLimitation"), refs)
    if groups:
        results.append(finding("review", "Confirm 3-5 dominant visual mechanisms have distinct inclusion boundaries; do not merge distinct mechanisms into a mixed catch-all or split by material ID",
                               field="customClassification.boundaries", evidence_refs=refs,
                               examples=[{"groupName": g.get("groupName"), "description": g.get("description"),
                                          "frameRefs": g.get("frameRefs", [])} for g in groups]))
    for name in ("highlightDashboard", "customClassification"):
        for index, group in enumerate(conclusion.get(name, {}).get("groups", [])):
            check_limitation(group, "contentLimitation", f"{name}.groups[{index}].contentLimitation")
            if not group.get("frameRefs"):
                continue
            for section in ("commonConclusion", "derivativeDirections"):
                items = group.get(section, [])
                field = f"{name}.groups[{index}].{section}"
                require_count(items, 2, 3, field, group.get("contentLimitation"), group["frameRefs"])
                if isinstance(items, list):
                    bodies = ["".join(str(item.get("body", "")).split()).casefold()
                              for item in items if isinstance(item, dict)]
                    if len(bodies) != len(set(bodies)):
                        results.append(finding("error", "duplicate insight bodies do not count as independent analysis",
                                               field=field, evidence_refs=group["frameRefs"]))
    return results


def content_contract_findings(conclusion, script_input):
    """Validate references deterministically; leave evidence sufficiency to review."""
    results = []
    cloud = script_input.get("keywordCloud", {})
    keywords = {item["keyword"]: set(item.get("evidenceRefs", []))
                for item in cloud.get("keywords", [])}
    refs = sorted(set().union(*keywords.values())) if keywords else []
    provenance = conclusion.get("analysisProvenance", {})
    if provenance != {"author": "current_model", "businessTextGeneration": "direct"}:
        results.append(finding("error", "analysisProvenance requires current_model/direct; regenerate script-authored prose",
                               field="analysisProvenance"))
    insight = conclusion.get("keywordCloudInsight")
    if not isinstance(insight, dict):
        return results + [finding("error", "keywordCloudInsight is required")]
    points = insight.get("sellingPoints")
    status = insight.get("analysisStatus")
    if status not in {"supported", "insufficient_evidence"} or not isinstance(points, list):
        return results + [finding("error", "keywordCloudInsight requires analysisStatus and sellingPoints list")]
    if status == "insufficient_evidence":
        if not str(insight.get("limitation", "")).strip():
            results.append(finding("error", "keywordCloudInsight requires a specific limitation"))
        elif refs:
            results.append(finding("review", "Review selling-point evidence sufficiency against scripts",
                                   field="keywordCloudInsight", evidence_refs=refs))
    elif len(points) < 3 or len({p["dimension"].strip() for p in points
                               if isinstance(p, dict) and isinstance(p.get("dimension"), str) and p["dimension"].strip()}) < 2:
        results.append(finding("error", "supported sellingPoints requires at least 3 points across 2 dimensions"))
    excluded = {item.get("keyword") for item in conclusion.get("keywordCloudReview", [])
                if item.get("decision") == "exclude"}
    for index, point in enumerate(points):
        field = f"keywordCloudInsight.sellingPoints[{index}]"
        if not isinstance(point, dict) or not all(isinstance(point.get(k), str) and point[k].strip()
                                                 for k in ("title", "description", "dimension")):
            results.append(finding("error", "selling point requires title, description and dimension", field=field))
            continue
        words, evidence = point.get("keywordRefs", []), point.get("evidenceRefs", [])
        if (not isinstance(words, list) or not isinstance(evidence, list) or not words or not evidence
                or not all(isinstance(w, str) and w in keywords and w not in excluded for w in words)
                or not all(isinstance(r, str) and r.startswith("script:") and r in refs for r in evidence)
                or not all(keywords[w].intersection(evidence) for w in words if w in keywords)):
            results.append(finding("error", "selling point requires real keyword-local script evidence", field=field))
    if points:
        results.append(finding("review", "Confirm distinct selling dimensions, business relevance and claim support",
                               field="keywordCloudInsight.sellingPoints", evidence_refs=refs))
    for name in ("highlightDashboard", "customClassification"):
        for index, group in enumerate(conclusion.get(name, {}).get("groups", [])):
            group_refs = group.get("frameRefs", [])
            if group_refs:
                results.append(finding("review", "Confirm independent visual mechanisms and actionable test variables; retain genuine same-image observations",
                                       field=f"{name}.groups[{index}]", evidence_refs=group_refs,
                                       examples=[{"commonConclusion": group.get("commonConclusion"),
                                                  "derivativeDirections": group.get("derivativeDirections", [])}]))
    return results


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def finding(level, message, *, field=None, evidence_refs=(), examples=(), count=None, required_decision=None):
    item = {"level": level, "field": field or message.split(" ", 1)[0],
            "message": message, "evidenceRefs": sorted(set(evidence_refs)),
            "examples": list(examples)[:3]}
    if count is not None:
        item["count"] = count
    if required_decision is not None:
        item["requiredDecision"] = required_decision
    item["id"] = digest(item)[:20]
    return item


def review_result(findings, analysis_json, material_cards, quality_review=None, input_context=None):
    """A current-model review resolves heuristics, never structural errors.

    Bind the receipt to actual bytes and report inputs, including JPEG hashes.
    Changing wording, facts or images invalidates previous dispositions.
    """
    from final_report_lib import _local_frame_hashes

    binding = {
        "policyVersion": POLICY_VERSION,
        "analysisSha256": hashlib.sha256(Path(analysis_json).read_bytes()).hexdigest(),
        "inputSha256": digest({"materials": material_cards,
                               "frames": _local_frame_hashes(material_cards),
                               "context": input_context}),
    }
    reviews = [item for item in findings if item["level"] == "review"]
    resolved = set()
    decisions = []
    if quality_review is not None:
        receipt = json.loads(Path(quality_review).read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in binding.items()):
            raise ValueError("quality review does not match candidate, inputs or policy")
        entries = receipt.get("findings", [])
        if not isinstance(entries, list):
            raise ValueError("quality review findings must be a list")
        known = {item["id"]: item for item in reviews}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id") not in known or entry["id"] in resolved:
                raise ValueError("quality review has unknown or duplicate finding")
            refs = entry.get("evidenceRefs", [])
            if (entry.get("decision") not in {"supported", "evidence_limited"}
                    or not isinstance(entry.get("reason"), str) or not entry["reason"].strip()
                    or not isinstance(refs, list) or not refs
                    or not all(isinstance(ref, str) for ref in refs)
                    or set(refs) != set(known[entry["id"]]["evidenceRefs"])):
                raise ValueError("quality review requires a reason and finding-local evidenceRefs")
            required = known[entry["id"]].get("requiredDecision")
            if required and entry["decision"] != required:
                raise ValueError(f"quality review requires {required} for this evidence shortage")
            resolved.add(entry["id"])
            decisions.append(entry)
    pending = [item["id"] for item in reviews if item["id"] not in resolved]
    errors = any(item["level"] == "error" for item in findings)
    limitations = [entry for entry in decisions if entry["decision"] == "evidence_limited"]
    return {**binding, "analysisStatus": "invalid" if errors else "review_required" if pending else "valid",
            "structurally_valid": not errors,
            "content_reviewed": not errors and not pending,
            "contentReviewStatus": "pending" if errors or pending else "reviewed_with_limits" if limitations else "reviewed",
            "reviewDecisions": decisions, "contentReviewLimitations": limitations,
            "findings": findings, "pendingReviewIds": pending}
