"""Content gate and offline projection regression; never synthesizes business prose.

Optional real-run QA: set QIANCHUAN_CONTENT_QA_INPUT to diagnostic-analysis and
QIANCHUAN_CONTENT_QA_OUTPUT to a new directory outside runs. Input stays read-only.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analysis_quality as quality
import final_report_lib as report
from analysis_handoff import inspect_inputs, verify_assembly
from qianchuan_presentation import build_presentation_report
from report_renderer import report_data_for_template, render_html_report

FIXTURE = ROOT / "tests/fixtures/content-regression-20260909.json"


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class ContentContractTests(unittest.TestCase):
    def setUp(self):
        self.candidate = read(FIXTURE)
        self.facts = {"keywordCloud": {"keywords": [
            {"keyword": "福利", "evidenceRefs": ["script:7669725885080436779:text:福利"]},
            {"keyword": "加送", "evidenceRefs": ["script:7669725885080436779:text:加送"]},
            {"keyword": "配料表", "evidenceRefs": ["script:7663436424943517723:text:配料表"]},
            {"keyword": "溯源", "evidenceRefs": ["script:7663436424943517723:text:溯源"]},
            {"keyword": "早餐", "evidenceRefs": ["script:7679012713931423770:text:早餐"]},
            {"keyword": "搭配", "evidenceRefs": ["script:7679012713931423770:text:搭配"]}
        ]}}

    def findings(self):
        return quality.content_contract_findings(self.candidate, self.facts)

    def test_empty_selling_points_is_hard_error_and_explicit_shortage_is_review(self):
        insight = self.candidate["keywordCloudInsight"]
        insight["sellingPoints"] = []
        self.assertTrue(any(f["level"] == "error" for f in self.findings()))
        insight["analysisStatus"] = "insufficient_evidence"
        insight["limitation"] = "输入只能支持福利一类，其他维度尚无原句。"
        findings = self.findings()
        self.assertFalse(any(f["level"] == "error" for f in findings))
        self.assertTrue(any(f["field"] == "keywordCloudInsight" and f["level"] == "review" for f in findings))
        insight["limitation"] = ""
        self.assertTrue(any(f["level"] == "error" for f in self.findings()))

    def test_no_keyword_evidence_allows_explicit_empty_not_silent_empty(self):
        self.facts["keywordCloud"]["keywords"] = []
        self.candidate["keywordCloudInsight"] = {"analysisStatus": "insufficient_evidence", "limitation": "没有可用关键词及脚本原句。", "sellingPoints": []}
        self.assertFalse(any(f["level"] == "error" for f in self.findings()))

    def test_unknown_excluded_and_cross_keyword_evidence_are_errors(self):
        original = copy.deepcopy(self.candidate)
        for words, refs in [(["编造"], ["script:7669725885080436779:text:福利"]),
                            (["福利"], ["script:unknown:text:福利"]),
                            (["福利"], ["script:7679012713931423770:text:早餐"])]:
            with self.subTest(words=words, refs=refs):
                self.candidate = copy.deepcopy(original)
                self.candidate["keywordCloudInsight"]["sellingPoints"][0].update(keywordRefs=words, evidenceRefs=refs)
                self.assertTrue(any(f["level"] == "error" for f in self.findings()))
        self.candidate = original
        self.candidate["keywordCloudReview"].append({"keyword": "福利", "decision": "exclude"})
        self.assertTrue(any(f["level"] == "error" for f in self.findings()))

    def test_dimensions_and_source_are_required(self):
        for point in self.candidate["keywordCloudInsight"]["sellingPoints"]:
            point["dimension"] = "同一维度"
        self.assertTrue(any(f["level"] == "error" for f in self.findings()))
        self.candidate["analysisProvenance"]["businessTextGeneration"] = "script"
        self.assertTrue(any(f["field"] == "analysisProvenance" and f["level"] == "error" for f in self.findings()))

    def test_single_generic_direction_and_rewording_cannot_escape_review(self):
        group = self.candidate["customClassification"]["groups"][0]
        group["derivativeDirections"][0]["body"] = "保持主体、替换一个变量、比较 ROI。"
        first = next(f for f in self.findings() if f["field"] == "customClassification.groups[0]")
        group["derivativeDirections"][0]["body"] = "维持主角，调整一项元素并观察回报。"
        second = next(f for f in self.findings() if f["field"] == "customClassification.groups[0]")
        self.assertEqual((first["level"], second["level"]), ("review", "review"))
        self.assertNotEqual(first["id"], second["id"])
        group["derivativeDirections"] = []
        self.assertTrue(any(f["field"] == "customClassification.groups[0]" for f in self.findings()))

    def test_receipt_cannot_waive_error_and_retains_limited_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, receipt = Path(directory) / "candidate.json", Path(directory) / "review.json"
            write(candidate, self.candidate)
            item = quality.finding("review", "Review", evidence_refs=["script:m:text:x"])
            result = quality.review_result([item], candidate, [])
            decision = {"id": item["id"], "decision": "evidence_limited", "reason": "字幕无法辨认，不能支持更细变量。", "evidenceRefs": item["evidenceRefs"]}
            write(receipt, {**{k: result[k] for k in ("policyVersion", "analysisSha256", "inputSha256")}, "findings": [decision]})
            result = quality.review_result([item], candidate, [], receipt)
            self.assertEqual(result["contentReviewStatus"], "reviewed_with_limits")
            self.assertEqual(result["contentReviewLimitations"], [decision])
            invalid = quality.review_result([item, quality.finding("error", "Missing point")], candidate, [], receipt)
            self.assertEqual(invalid["analysisStatus"], "invalid")
            self.assertFalse(invalid["structurally_valid"])
            self.assertFalse(invalid["content_reviewed"])
            data = {"materials": [], "conclusion": {}, "analysisQuality": result}
            data["presentation"] = build_presentation_report(data)
            embedded = report_data_for_template(data, None)
            self.assertEqual(embedded["analysisQuality"], result)
            self.assertEqual(embedded["presentation"]["analysisQuality"], result)

    def test_production_review_invokes_content_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            candidate = {**self.candidate, "schemaVersion": 5}
            candidate["keywordCloudInsight"]["sellingPoints"] = []
            write(path, candidate)
            with patch.object(report, "_validate_standard_ai_quality", return_value=[]):
                result = report._review_analysis_quality(candidate, {"materials": [], "scripts": self.facts}, path)
            self.assertEqual(result["analysisStatus"], "invalid")

    def test_assembly_detects_candidate_or_fragment_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, part = root / "candidate.json", root / "part.json"
            write(candidate, self.candidate)
            write(part, self.candidate)
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            inputs = {"manifestSha256": "manifest", "promptSha256": "prompt"}
            write(root / "candidate.assembly.json", {**inputs, "candidateSha256": sha(candidate), "sources": [{"path": str(part), "sha256": sha(part)}]})
            self.assertTrue(verify_assembly(candidate, inputs))
            part.write_text("{}")
            with self.assertRaisesRegex(ValueError, "source"):
                verify_assembly(candidate, inputs)
            candidate.write_text("{}")
            with self.assertRaisesRegex(ValueError, "candidate/input"):
                verify_assembly(candidate, inputs)

    def test_offline_empty_panel_renders_alert_status_and_limitation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for offline DOM check")
        template = (ROOT / "templates/final-report-template.html").read_text()
        function = "function renderKeywordCloudPanel" + template.split("function renderKeywordCloudPanel", 1)[1].split("function renderFrames", 1)[0]
        code = r'''
const panel = {keywords:[{keyword:"燕窝",frequency:1}],sellingPoints:[],analysisStatus:"insufficient_evidence",availability:"available",warning:"关键词事实存在，但 AI 未提炼卖点",limitation:"只有品类词"};
function element(tag, cls, value) {return {tag, cls, value, children:[],style:{},dataset:{},append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},setAttribute(k,v){this[k]=v}}}
const root=element("main"); const document={getElementById:()=>root};
const chapter03KeywordCloud=()=>panel; const stableKeywordOrder=v=>v; const formatNumber=String;
''' + function + '\nrenderKeywordCloudPanel(); process.stdout.write(JSON.stringify(root));'
        result = json.loads(subprocess.run([node, "-e", code], check=True, text=True, capture_output=True).stdout)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertIn('"role": "alert"', serialized)
        self.assertIn("关键词事实存在，但 AI 未提炼卖点", serialized)
        self.assertIn("insufficient_evidence", serialized)
        self.assertIn("只有品类词", serialized)

    @unittest.skipUnless(os.environ.get("QIANCHUAN_CONTENT_QA_INPUT"), "Explicit real-run input required")
    def test_real_facts_to_presentation_and_html(self):
        source = Path(os.environ["QIANCHUAN_CONTENT_QA_INPUT"]).resolve()
        binding = inspect_inputs(source)
        self.assertEqual(binding["manifestSha256"], self.candidate["customClassification"]["inputFrameManifestHash"])
        facts = read(source / "analysis-units/chapter03-script-input.json")
        findings = quality.content_contract_findings(self.candidate, facts)
        self.assertFalse(any(f["level"] == "error" for f in findings), findings)
        frame_refs = {item["frameRef"] for item in binding["frames"]}
        report._validate_custom_classification(self.candidate["customClassification"], frame_refs)
        highlights = read(source / "analysis-units/chapter04-highlight-input.json")
        materials = []
        for raw in highlights["materials"]:
            frames = [{**frame, "rankType": frame["evidenceRef"].rsplit(":", 1)[1]} for frame in raw["frames"]]
            materials.append({"materialId": raw["materialId"], "materialName": raw["materialId"], "extractionStatus": "succeeded", "highlightFrames": frames})
        data = {"schemaVersion": 1, "status": "acceptance_sample", "period": binding["period"], "materials": materials,
                "scripts": facts, "conclusion": {"schemaVersion": 5, **self.candidate}}
        presentation = build_presentation_report(data)
        panel = presentation["chapters"]["chapter03"]["keywordCloudPanel"]
        self.assertEqual(panel["sellingPoints"], self.candidate["keywordCloudInsight"]["sellingPoints"])
        groups = presentation["chapters"]["chapter04"]["batchClassification"]["groups"]
        self.assertEqual(sum(g["frameCount"] for g in groups), 60)
        self.assertEqual(len(groups), 7)
        for original, projected in zip(self.candidate["customClassification"]["groups"], groups):
            self.assertEqual(original["derivativeDirections"], projected["derivativeDirections"])
        data["presentation"] = presentation
        html = render_html_report(data, embed_assets=True)
        for point in panel["sellingPoints"]:
            self.assertIn(point["description"], html)
            for ref in point["evidenceRefs"]:
                self.assertIn(ref, html)
        for group in groups:
            for direction in group["derivativeDirections"]:
                self.assertIn(direction["body"], html)
                for ref in direction["evidenceRefs"]:
                    self.assertIn(ref, html)
        if os.environ.get("QIANCHUAN_CONTENT_QA_OUTPUT"):
            output = Path(os.environ["QIANCHUAN_CONTENT_QA_OUTPUT"]).resolve()
            self.assertNotIn(ROOT / "runs", output.parents)
            self.assertNotIn(source, output.parents)
            output.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(FIXTURE, output / "model-authored-chapters03-04.json")
            shutil.copyfile(ROOT / "prompts/diagnostic-analysis-prompt-v1.md", output / "prompt-snapshot.md")
            write(output / "presentation.json", presentation)
            write(output / "input-binding.json", binding)
            write(output / "content-findings.json", findings)
            (output / "acceptance-sample.html").write_text(html)
            write(output / "trace.json", {"scope": "Chapter03/04 acceptance only; not a finalized five-chapter report",
                "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()},
                "inputManifestSha256": binding["manifestSha256"], "sourcePromptSha256": binding["promptSha256"],
                "sellingPoints": len(panel["sellingPoints"]), "mechanismGroups": len(groups), "coveredFrames": 60})
        self.assertEqual(inspect_inputs(source), binding)


if __name__ == "__main__":
    unittest.main()
