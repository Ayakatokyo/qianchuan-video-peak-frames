import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qianchuan_presentation import build_presentation_report, validate_presentation_report
from report_renderer import report_data_for_template


def sample_data():
    return {
        "schemaVersion": 1,
        "runId": "run-1",
        "status": "complete",
        "period": {"actualStartDate": "2026-08-01", "actualEndDate": "2026-08-07"},
        "materials": [
            {
                "materialId": "m-1",
                "materialName": "素材一",
                "rank": 1,
                "extractionStatus": "succeeded",
                "highlightFrames": [
                    {"rankType": "peak_top_1", "framePath": "/tmp/peak.jpg", "durationSec": 1.2}
                ],
                "materialAudience": {"metrics": {"SHOW_COUNT": {"available": True, "buckets": []}}},
                "interactionTimeline": {"metrics": [{"metric": "CLICK_COUNT", "points": []}]},
                "scriptAnalysis": {"formula": [{"level1": "利益点", "level2": "高蛋白"}], "evidence": ["script:m-1:1"]},
                "analysis": {"materialId": "m-1", "audienceConclusion": "人群结论", "scriptConclusion": "脚本结论", "decision": "scale", "frameCoverageNote": "基于成功帧分析", "highlightAnalyses": [{"frameRef": "highlight:m-1:peak_top_1", "observation": "画面结论", "evidenceRefs": ["highlight:m-1:peak_top_1"]}], "evidenceRefs": ["material:m-1"]},
            }
        ],
        "conclusion": {
            "schemaVersion": 5,
            "overallConclusion": "总体结论",
            "portfolioDimensionConclusions": {"highlight": "高光共性"},
            "highlightDashboard": {"groups": [
                {"category": "portrait", "frameRefs": ["highlight:m-1:peak_top_1"], "commonConclusion": "人物", "derivativeDirections": [{"title": "方向", "body": "执行", "evidenceRefs": ["highlight:m-1:peak_top_1"]}]},
                {"category": "product", "frameRefs": [], "commonConclusion": "", "derivativeDirections": []},
                {"category": "other", "frameRefs": [], "commonConclusion": "", "derivativeDirections": []},
            ]},
        },
    }


class PresentationTests(unittest.TestCase):
    def test_projects_chapters_with_stable_refs_and_tabs(self):
        report = build_presentation_report(sample_data())
        self.assertEqual(report["presentationSchemaVersion"], 1)
        self.assertEqual(list(report["chapters"]["chapter04"]["groups"]), ["portrait", "product", "other"])
        self.assertEqual(report["chapters"]["chapter04"]["groups"]["portrait"]["frames"][0]["frameRef"], "highlight:m-1:peak_top_1")
        self.assertEqual(report["chapters"]["chapter05"]["materials"][0]["materialId"], "m-1")
        self.assertEqual([tab["id"] for tab in report["chapters"]["chapter05"]["materials"][0]["tabs"]], ["highlight", "interaction", "audience", "script", "decision"])
        self.assertEqual(report["chapters"]["chapter05"]["materials"][0]["analysis"]["decision"], "scale")
        self.assertEqual(report["chapters"]["chapter05"]["materials"][0]["analysis"]["frameCoverageNote"], "基于成功帧分析")
        validate_presentation_report(report)

    def test_missing_frames_and_old_ai_schema_are_unavailable(self):
        data = sample_data()
        data["materials"][0]["highlightFrames"] = []
        data["conclusion"]["schemaVersion"] = 1
        report = build_presentation_report(data)
        self.assertEqual(report["chapters"]["chapter04"]["availability"], "unavailable")
        self.assertEqual(report["chapters"]["chapter05"]["materials"][0]["availability"], "partial")

    def test_validation_rejects_duplicate_material_ids(self):
        report = build_presentation_report(sample_data())
        report["materials"].append(dict(report["materials"][0]))
        with self.assertRaises(ValueError):
            validate_presentation_report(report)

    def test_missing_material_evidence_degrades_workspace(self):
        data = sample_data()
        data["materials"][0]["analysis"]["evidenceRefs"] = []
        report = build_presentation_report(data)
        self.assertEqual(report["chapters"]["chapter05"]["materials"][0]["availability"], "partial")

    def test_renderer_exposes_presentation_with_legacy_fallback_fields(self):
        data = sample_data()
        data["presentation"] = build_presentation_report(data)
        rendered = report_data_for_template(data, None)
        self.assertEqual(rendered["presentation"]["presentationSchemaVersion"], 1)
        self.assertIn("materials", rendered)
        self.assertIn("conclusion", rendered)

    def test_renderer_rewrites_presentation_assets_and_preserves_video(self):
        data = sample_data()
        data["materials"][0]["video"] = {"src": "https://video.example/m-1.mp4", "poster": "https://video.example/poster.jpg", "durationSec": 12}
        data["presentation"] = build_presentation_report(data)
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "peak.jpg"
            source.write_bytes(b"jpeg")
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(source)
            data["presentation"] = build_presentation_report(data)
            rendered = report_data_for_template(data, temp_dir, copy_assets=True)
        frame = rendered["presentation"]["chapters"]["chapter04"]["groups"]["portrait"]["frames"][0]
        material = rendered["presentation"]["chapters"]["chapter05"]["materials"][0]
        self.assertEqual(frame["framePath"], "assets/highlight-frames/m-1/peak.jpg")
        self.assertEqual(material["highlight"]["frames"][0]["framePath"], "assets/highlight-frames/m-1/peak.jpg")
        self.assertEqual(material["video"]["src"], "https://video.example/m-1.mp4")

    def test_template_passes_presentation_video_to_material_workspace(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(encoding="utf-8")
        self.assertIn("video: item.video || {},", template)


if __name__ == "__main__":
    unittest.main()
