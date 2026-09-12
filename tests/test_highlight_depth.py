"""Chapter 04 depth gates and offline rendering; synthetic fixtures only."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analysis_quality as quality
import final_report_lib as report
from qianchuan_presentation import build_presentation_report


def candidate(group_count=3, point_count=2):
    def group(index):
        ref = f"highlight:test-{index}:zero_second"
        return {
            "groupId": f"fixture-{index}", "groupName": f"测试机制{index}",
            "description": f"合成测试边界{index}，不代表业务分析", "frameRefs": [ref],
            **{section: [
                {"title": f"测试{n}", "body": f"synthetic {index}/{section}/{n}", "evidenceRefs": [ref]}
                for n in range(point_count)
            ] for section in ("commonConclusion", "derivativeDirections")},
        }
    groups = [group(i) for i in range(group_count)]
    return {
        "schemaVersion": 5,
        "highlightDashboard": {"groups": [{**copy.deepcopy(groups[0]), "category": "portrait"}]},
        "customClassification": {
            "ruleId": "test", "ruleName": "测试", "ruleDescription": "合成测试",
            "ruleVersion": "v1", "inputFrameManifestHash": "sha256:fixture",
            "groups": groups, "unclassifiedFrameRefs": [],
        },
    }


def errors(value):
    return [f for f in quality.highlight_depth_findings(value) if f["level"] == "error"]


@pytest.mark.parametrize("groups", [3, 4, 5])
@pytest.mark.parametrize("points", [2, 3])
def test_normal_counts_still_require_boundary_review(groups, points):
    value = candidate(groups, points)
    assert errors(value) == []
    assert any(f["field"] == "customClassification.boundaries" and f["level"] == "review"
               for f in quality.highlight_depth_findings(value))


def test_single_mixed_bucket_and_single_points_are_rejected():
    fields = {f["field"] for f in errors(candidate(1, 1))}
    assert fields == {"customClassification.groups", *[
        f"{mode}.groups[0].{section}" for mode in ("highlightDashboard", "customClassification")
        for section in ("commonConclusion", "derivativeDirections")
    ]}


@pytest.mark.parametrize("mode", ["highlightDashboard", "customClassification"])
@pytest.mark.parametrize("section", ["commonConclusion", "derivativeDirections"])
def test_shortage_and_duplicates_fail_in_both_modes(mode, section):
    value = candidate()
    items = value[mode]["groups"][0][section]
    items.pop()
    assert errors(value)
    items.append({**items[0], "body": " \n" + items[0]["body"].upper() + " "})
    assert any("duplicate" in f["message"] for f in errors(value))


def test_empty_standard_group_is_not_forced_to_invent_insights():
    value = candidate()
    value["highlightDashboard"]["groups"].append({"category": "other", "frameRefs": []})
    assert errors(value) == []


def test_no_supported_direction_retains_limitation_through_normalization():
    value = candidate()
    for mode in ("highlightDashboard", "customClassification"):
        group = value[mode]["groups"][0]
        group["derivativeDirections"] = []
        group["contentLimitation"] = "测试输入无法支持可辨识的下一轮画面变量"
    chapter = projected(value)
    for group in (chapter["groups"]["portrait"], chapter["batchClassification"]["groups"][0]):
        assert group["derivativeDirections"] == []
        assert group["contentLimitation"]
    findings = quality.highlight_depth_findings(value)
    assert not errors(value)
    assert len([f for f in findings if f.get("requiredDecision") == "evidence_limited"]) == 2


@pytest.mark.parametrize("groups,points", [(6, 2), (3, 4)])
def test_upper_bound_cannot_be_waived(groups, points):
    value = candidate(groups, points)
    value["customClassification"]["classificationLimitation"] = "测试限制"
    for mode in ("highlightDashboard", "customClassification"):
        for group in value[mode]["groups"]:
            group["contentLimitation"] = "测试限制"
    assert errors(value)


def test_shortage_requires_bound_evidence_limited_receipt(tmp_path):
    value = candidate(1, 1)
    value["customClassification"]["classificationLimitation"] = "测试输入只有一种可辨机制"
    for mode in ("highlightDashboard", "customClassification"):
        value[mode]["groups"][0]["contentLimitation"] = "测试图片缺少支持第二种独立判断的信息"
    findings = quality.highlight_depth_findings(value)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value))
    result = quality.review_result(findings, path, [])
    assert result["analysisStatus"] == "review_required"
    assert result["structurally_valid"]
    decisions = [{"id": f["id"], "decision": f.get("requiredDecision", "supported"),
                  "reason": "测试复核：逐帧核对后的具体信息缺口", "evidenceRefs": f["evidenceRefs"]}
                 for f in findings]
    receipt = {k: result[k] for k in ("policyVersion", "analysisSha256", "inputSha256")}
    receipt["findings"] = decisions
    receipt_path = tmp_path / "quality-review.json"
    receipt_path.write_text(json.dumps(receipt))
    accepted = quality.review_result(findings, path, [], receipt_path)
    assert accepted["analysisStatus"] == "valid"
    assert accepted["contentReviewStatus"] == "reviewed_with_limits"
    assert len(accepted["contentReviewLimitations"]) == 5
    decisions[0]["decision"] = "supported"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="requires evidence_limited"):
        quality.review_result(findings, path, [], receipt_path)


@pytest.mark.parametrize("limitation", [None, {}, [], 123])
def test_limitations_must_be_text(limitation):
    value = candidate()
    value["customClassification"]["classificationLimitation"] = limitation
    value["highlightDashboard"]["groups"][0]["contentLimitation"] = limitation
    assert len(errors(value)) == 2


def test_frozen_prompt_enables_production_gate_without_changing_old_policy(tmp_path):
    value = candidate(1, 1)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value))
    # Isolate the depth gate; the unchanged structural/content gates have their own suites.
    with patch.object(report, "_validate_standard_ai_quality", return_value=[]), \
            patch.object(report, "content_contract_findings", return_value=[]):
        old = report._review_analysis_quality(value, {"materials": []}, path)
        assert old["analysisStatus"] == "valid"
        (tmp_path / "diagnostic-analysis-prompt.md").write_text(quality.HIGHLIGHT_DEPTH_POLICY_MARKER)
        new = report._review_analysis_quality(value, {"materials": []}, path)
        assert new["analysisStatus"] == "invalid"
        assert len([f for f in new["findings"] if f["level"] == "error"]) == 5


def projected(value):
    custom = report._validate_custom_classification(value["customClassification"])
    standard = [report._validate_highlight_dashboard_group(g, i)
                for i, g in enumerate(value["highlightDashboard"]["groups"])]
    materials = [{"materialId": f"test-{i}", "extractionStatus": "succeeded",
                  "highlightFrames": [{"rankType": "zero_second", "framePath": f"/tmp/test-{i}.jpg"}]}
                 for i in range(len(custom["groups"]))]
    return build_presentation_report({"materials": materials, "conclusion": {
        "schemaVersion": 5, "customClassification": custom, "highlightDashboard": {"groups": standard},
    }})["chapters"]["chapter04"]


@pytest.mark.parametrize("points", [2, 3])
def test_normalization_projection_and_dom_preserve_all_points_and_limits(points):
    value = candidate(point_count=points)
    value["customClassification"]["classificationLimitation"] = "分类测试边界"
    value["highlightDashboard"]["groups"][0]["contentLimitation"] = "标准测试边界"
    value["customClassification"]["groups"][0]["contentLimitation"] = "本批次测试边界"
    chapter = projected(value)
    assert chapter["batchClassification"]["classificationLimitation"] == "分类测试边界"
    for source, output in [(value["customClassification"]["groups"], chapter["batchClassification"]["groups"]),
                           (value["highlightDashboard"]["groups"], [chapter["groups"]["portrait"]])]:
        for original, actual in zip(source, output):
            for field in ("commonConclusion", "derivativeDirections", "contentLimitation"):
                assert actual.get(field) == original.get(field)
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for offline DOM rendering")
    template = (ROOT / "templates/final-report-template.html").read_text()
    functions = "function renderHighlightInsightColumns" + template.split("function renderHighlightInsightColumns", 1)[1].split("function activateMaterialTab", 1)[0]
    # Minimal DOM: real chapter renderers run; frame paging/chart internals are outside this test.
    code = r'''
function element(tag, cls="", value="") {return {tag,cls,value,children:[],dataset:{},classList:{add(){}},
 append(...v){this.children.push(...v)},replaceChildren(){this.children=[]},setAttribute(k,v){this[k]=v},
 addEventListener(){},querySelectorAll(){return []}}}
const root=element("main");
const document={getElementById:()=>root,querySelector:()=>null,createElement:tag=>element(tag)};
const text=v=>v==null?"":String(v),safeId=text,formatNumber=String,elementWithMaterialLinks=element;
const reportData={},presentation=true,highlightCategoryLabels={portrait:"人物",product:"产品",other:"其他"};
const chapter04Groups=()=>Object.values(chapter.groups).filter(g=>g.frameRefs.length);
const chapter04Conclusion=()=>null,highFrameFactsByReference=()=>new Map(),presentationChapter=()=>chapter;
const createHighlightAssetsCard=()=>element("aside"),renderHighlightFramePage=()=>{},renderColumnChart=()=>element("figure");
''' + "\nconst chapter=" + json.dumps(chapter) + ";\n" + functions + "\nrenderHighlightDashboard(); process.stdout.write(JSON.stringify(root));"
    dom = json.loads(subprocess.run([node, "-e", code], check=True, capture_output=True, text=True).stdout)
    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    nodes = list(walk(dom))
    assert len([n for n in nodes if n.get("cls") == "highlight-batch-category-panel"]) == 3
    assert len([n for n in nodes if n.get("cls") == "highlight-insight"]) == 4 * points * 2
    values = [n.get("value") for n in nodes]
    for group in value["customClassification"]["groups"] + value["highlightDashboard"]["groups"]:
        for section in ("commonConclusion", "derivativeDirections"):
            for item in group[section]:
                assert item["body"] in values
    for limit in ("分类测试边界", "标准测试边界", "本批次测试边界"):
        assert any(limit in str(v) for v in values)


def test_prompt_example_follows_depth_and_frame_coverage_contract():
    prompt = (ROOT / "prompts/diagnostic-analysis-prompt-v1.md").read_text()
    value = json.loads(prompt.split("```json\n", 1)[1].split("```", 1)[0])
    assert errors(value) == []
    refs = [ref for group in value["highlightDashboard"]["groups"] for ref in group["frameRefs"]]
    report._validate_custom_classification(value["customClassification"], refs)
