import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
import shutil
import io
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import analysis_handoff as handoff
import analysis_quality as quality
import standard_workflow as workflow
import final_report_lib as report
import run_standard_analysis as cli
import test_analysis_units as unit_fixtures
import test_standard_workflow as workflow_fixtures
import test_final_report as report_fixtures
from analysis_units_lib import publish_analysis_units


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class HandoffTests(unittest.TestCase):
    def inputs(self, root):
        facts = unit_fixtures.example_analysis()
        image = root / "frame.jpg"
        image.write_bytes(b"same JPEG")
        facts["highlights"]["materials"][0]["frames"][0]["framePath"] = str(image)
        publish_analysis_units(root / "diagnostic-analysis", facts)
        (root / "diagnostic-analysis" / "diagnostic-analysis-prompt.md").write_text("prompt")
        return root / "diagnostic-analysis"

    def test_assembly_preserves_prose_and_records_exact_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.inputs(Path(directory))
            first, second = root / "parts/a.json", root / "parts/b.json"
            content = "短而充分的结论。"
            write(first, {"overallConclusion": content})
            write(second, {"materialAnalyses": [{"materialId": "m-1", "scriptConclusion": "脚本缺失。"}]})
            output = root / "candidate.json"
            handoff.assemble_analysis(root, [first, second], output)
            self.assertEqual(handoff.read_object(output)["overallConclusion"], content)
            receipt = handoff.read_object(root / "candidate.assembly.json")
            self.assertEqual(receipt["sources"][0]["sha256"], handoff.file_hash(first))
            with self.assertRaisesRegex(ValueError, "new candidate"):
                handoff.assemble_analysis(root, [first, second], output)

    def test_assembly_rejects_duplicate_fields_materials_and_tampered_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.inputs(Path(directory))
            first, second = root / "a.json", root / "b.json"
            write(first, {"materialAnalyses": [{"materialId": "m-1"}]})
            write(second, {"materialAnalyses": [{"materialId": "m-1"}]})
            with self.assertRaisesRegex(ValueError, "exactly once"):
                handoff.assemble_analysis(root, [first, second], root / "candidate.json")
            write(first, {"overallConclusion": "A"})
            write(second, {"overallConclusion": "B"})
            with self.assertRaisesRegex(ValueError, "conflicting"):
                handoff.assemble_analysis(root, [first, second], root / "candidate.json")
            (root / "analysis-units/chapter01-portfolio-input.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                handoff.assemble_analysis(root, [first], root / "candidate.json")
            self.assertFalse((root / "candidate.json").exists())

    def landed_run(self, root):
        helper = workflow_fixtures.StandardWorkflowTests()
        helper.write_complete_ready_data(root)
        ready_path = root / "peak-frames/top_materials_rpa_ready.json"
        ready = handoff.read_object(ready_path)
        manifest_path = root / "peak-frames/batch-manifest.json"
        manifest = handoff.read_object(manifest_path)
        for item in manifest["selectedMaterials"]:
            video = root / "peak-frames" / f"{item['materialId']}.mp4"
            video.write_bytes(b"retained video")
            item["videoPath"] = str(video)
            item["videoSha256"] = handoff.file_hash(video)
        write(manifest_path, manifest)
        ready.update(actualStartDate="2026-07-19", actualEndDate="2026-07-25",
                     filters={"advertiserId": "1770096415226951"},
                     portfolioFacts={"summaryMetrics": {"cost": "20"}, "materials": []})
        for rank, m in enumerate(ready["materials"], 1):
            m.update(rank=rank, materialName=m["materialId"], totalCost=10)
            mid = m["materialId"]
            write(root / "diagnosis-records" / f"{mid}.json", [])
            frame = root / "peak-frames" / mid / "zero.jpg"
            frame.parent.mkdir(parents=True, exist_ok=True)
            frame.write_bytes(b"JPEG")
            write(frame.parent / "summary.json", {"frames": [{"rankType": "zero_second",
                "durationSec": 0, "metricValue": 1, "framePath": str(frame)}], "warnings": []})
        write(ready_path, ready)
        write(root / "top_materials.json", ready)
        return helper.request()

    def test_local_prepare_never_collects_and_preserves_existing_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.landed_run(root)
            source_hash = handoff.file_hash(root / "top_materials.json")
            with patch.object(workflow.export_top_materials, "main", side_effect=AssertionError("network")), \
                 patch.object(workflow.extract_peak_frames, "main", side_effect=AssertionError("RPA/FFmpeg")):
                summary = workflow.prepare_local_run(request, root)
                facts = root / "diagnostic-analysis/analysis-units/chapter01-portfolio-input.json"
                original = facts.read_bytes()
                again = workflow.prepare_local_run(request, root)
            self.assertEqual(summary["status"], "analysis_pending")
            self.assertEqual(again["status"], "analysis_pending")
            self.assertEqual(facts.read_bytes(), original)
            self.assertEqual(handoff.file_hash(root / "top_materials.json"), source_hash)

    def test_local_prepare_rejects_wrong_period_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.landed_run(root)
            bad = workflow.WorkflowRequest("2026-07-18", "2026-07-25", 2, "1770096415226951", "shop-1")
            with self.assertRaisesRegex(workflow.StandardWorkflowError, "period"):
                workflow.prepare_local_run(bad, root)
            self.assertFalse((root / "diagnostic-analysis").exists())
            self.assertFalse((root / "run-summary.json").exists())


class EvidenceReviewTests(unittest.TestCase):
    def test_cli_pending_review_returns_three_and_persists_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = workflow_fixtures.StandardWorkflowTests()
            write(root / "run-summary.json", {"status": "analysis_pending", "request": helper.request_payload()})
            candidate = root / "candidate.json"
            write(candidate, {"schemaVersion": 5})
            result = workflow.validate_analysis(root, candidate, validate=lambda **kw: {
                "analysisStatus": "review_required", "findings": [{"level": "review", "id": "r1"}]})
            self.assertEqual(handoff.read_object(root / "candidate.validation.json")["analysisStatus"], "review_required")
            with patch.object(cli, "ensure_prepare_dependencies"), patch.object(workflow, "validate_analysis", return_value=result), redirect_stdout(io.StringIO()):
                code = cli.main(["validate-analysis", "--output-dir", str(root), "--analysis-json", str(candidate)])
            self.assertEqual(code, 3)

    def test_finalize_preserves_retry_state_on_hard_error_and_pending_review(self):
        for result in [workflow.StandardWorkflowError("analysis", "unknown evidence"), {"analysisStatus": "review_required"}, {"analysisStatus": "invalid"}]:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                helper = workflow_fixtures.StandardWorkflowTests()
                write(root / "run-summary.json", {"status": "analysis_pending", "request": helper.request_payload()})
                candidate = root / "candidate.json"
                write(candidate, {})
                effect = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(workflow, "validate_analysis", **effect):
                    with self.assertRaises(workflow.StandardWorkflowError):
                        workflow.finalize_standard_run(root, candidate)
                self.assertEqual(handoff.read_object(root / "run-summary.json")["status"], "analysis_pending")
                self.assertFalse((root / "final-report").exists())

    def test_same_material_same_image_can_share_but_not_cycles_or_different_images(self):
        helper = report_fixtures.FinalReportV4HighlightDashboardTests()
        helper.setUp()
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "same.jpg"
            image.write_bytes(b"identical JPEG")
            frames = helper.summaries_by_material["m-1"]["frames"]
            frames[0]["framePath"] = str(image)
            other = copy.deepcopy(frames[0])
            other["rankType"] = "peak_top_2"
            frames.append(other)
            payload = helper._conclusion()
            highlights = payload["materialAnalyses"][0]["highlightAnalyses"]
            shared = {"frameRef": "highlight:m-1:peak_top_2", "visualObservation": "人物举起礼盒。",
                      "interpretationStatus": "shared", "sharedInterpretationRef": highlights[0]["frameRef"],
                      "commercialInterpretation": ""}
            highlights.append(shared)
            payload["highlightDashboard"]["groups"][0]["frameRefs"].append(shared["frameRef"])
            data = helper._build_data(payload)
            self.assertEqual(data["materials"][0]["analysis"]["highlightAnalyses"][1]["interpretationStatus"], "shared")
            for invalid in ["highlight:foreign:first_peak", shared["frameRef"]]:
                shared["sharedInterpretationRef"] = invalid
                with self.assertRaisesRegex(report.FinalReportError, "sharedInterpretationRef"):
                    helper._build_data(payload)
            shared["sharedInterpretationRef"] = highlights[0]["frameRef"]
            other_image = Path(directory) / "different.jpg"
            other_image.write_bytes(b"different image")
            frames[1]["framePath"] = str(other_image)
            with self.assertRaisesRegex(report.FinalReportError, "identical local JPEG"):
                helper._build_data(payload)

    def test_frontend_resolves_unselected_shared_target_and_displays_limitation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for offline frontend behavior check")
        template = (ROOT / "templates/final-report-template.html").read_text()
        function = template.split("function renderHighlightFormula", 1)[1].split("const highlightCategoryLabels", 1)[0]
        script = r'''
const text = value => value == null ? "" : String(value);
const formatNumber = text;
function element(tag, cls, value) {return {tag, value, children: [], append(...items) {this.children.push(...items)}}}
const elementWithMaterialLinks = element;
const renderTextWithMaterialLinks = text;
const document = {createElement: element};
''' + "function renderHighlightFormula" + function + r'''
const original = {frameRef: "original", commercialInterpretation: "保留礼盒识别线索。"};
const shared = {frameRef: "copy", interpretationStatus: "shared", sharedInterpretationRef: "original", visualObservation: "同图。"};
const limited = {frameRef: "limited", interpretationStatus: "insufficient_evidence", limitation: "字幕不可辨。", visualObservation: "瓶身遮挡字幕。"};
process.stdout.write(JSON.stringify(renderHighlightFormula([shared, limited], new Map(), [original, shared, limited])));
'''
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        self.assertIn("保留礼盒识别线索。", result.stdout)
        self.assertIn("证据不足：字幕不可辨。", result.stdout)

    def test_review_required_and_bound_to_candidate_input_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, receipt = root / "candidate.json", root / "review.json"
            write(candidate, {"text": "相同画面"})
            item = quality.finding("review", "Similar frame text", field="frames",
                                   evidence_refs=["highlight:m:zero_second"])
            result = quality.review_result([item], candidate, [])
            self.assertEqual(result["analysisStatus"], "review_required")
            review = {k: result[k] for k in ("policyVersion", "analysisSha256", "inputSha256")}
            review["findings"] = [{"id": item["id"], "decision": "supported",
                                   "reason": "同一画面及同一素材事实支持此表述。", "evidenceRefs": item["evidenceRefs"]}]
            write(receipt, review)
            self.assertEqual(quality.review_result([item], candidate, [], receipt)["analysisStatus"], "valid")
            review["findings"][0]["evidenceRefs"] = ["invented"]
            write(receipt, review)
            with self.assertRaisesRegex(ValueError, "evidenceRefs"):
                quality.review_result([item], candidate, [], receipt)
            review["findings"][0]["evidenceRefs"] = item["evidenceRefs"]
            write(receipt, review)
            with self.assertRaisesRegex(ValueError, "does not match"):
                quality.review_result([item], candidate, [{"materialId": "changed"}], receipt)
            write(candidate, {"text": "changed"})
            with self.assertRaisesRegex(ValueError, "does not match"):
                quality.review_result([item], candidate, [], receipt)

    def test_insufficient_evidence_preserved_and_contradictory_claim_rejected(self):
        raw = {"frameRef": "highlight:m:zero_second", "visualObservation": "瓶身遮挡字幕。",
               "interpretationStatus": "insufficient_evidence", "limitation": "字幕不可辨，且无分段时码。",
               "commercialInterpretation": ""}
        normalized = report._validate_v2_highlight_analysis(raw, 0, 0)
        self.assertEqual(normalized["limitation"], raw["limitation"])
        raw["commercialInterpretation"] = "该画面提升了成交。"
        with self.assertRaisesRegex(report.FinalReportError, "no commercial claim"):
            report._validate_v2_highlight_analysis(raw, 0, 0)

    def test_sparse_classification_keeps_group_ownership_hard(self):
        helper = report_fixtures.FinalReportConclusionTests()
        payload = helper._custom_classification()
        payload["groups"] = payload["groups"][:1]
        group = payload["groups"][0]
        group["commonConclusion"] = group["commonConclusion"][:1]
        group["commonConclusion"][0]["body"] = "人物举起礼盒。"
        group["derivativeDirections"] = []
        report._validate_custom_classification(payload, set(group["frameRefs"]))
        group["commonConclusion"][0]["evidenceRefs"] = ["highlight:foreign:zero_second"]
        with self.assertRaisesRegex(report.FinalReportError, "its group"):
            report._validate_custom_classification(payload, set(group["frameRefs"]))

    def test_short_supported_analysis_is_advice_not_error(self):
        payload = report_fixtures.FinalReportQualityGateTests._keyword_cloud_quality_conclusion("品质形成记忆。")
        payload["overallConclusion"] = "尚无投放对照，不能据此判断因果。"
        findings = report._validate_standard_ai_quality(payload, [])
        self.assertTrue(any(f["field"] == "overallConclusion" for f in findings))
        self.assertTrue(all(f["level"] == "advice" for f in findings))

    def test_invalid_evidence_cannot_be_waived_by_review(self):
        helper = report_fixtures.FinalReportV4HighlightDashboardTests()
        helper.setUp()
        payload = helper._conclusion()
        payload["materialAnalyses"][0]["evidenceRefs"] = ["script:unknown:invented"]
        with self.assertRaisesRegex(report.FinalReportError, "unknown evidence"):
            helper._build_data(payload)
