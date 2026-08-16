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


def complete_presentation_data():
    data = sample_data()
    data.update({
        "topMaterialRoi": {"source": "verifiedAssetFacts", "metrics": [{"key": "netSettlementRoi", "value": "1.20"}]},
        "audience": {
            "metrics": {
                "CLICK_COUNT": {
                    "dimensions": {"age": {"buckets": [{"bucket": "24-30", "metricValue": 12, "sourceMaterialIds": ["m-1"]}]}}
                }
            },
            "preferences": [{"dimension": "age", "bucket": "24-30", "sourceMaterialIds": ["m-1"]}],
        },
        "scripts": {
            "labelSummary": [{
                "level1": "商品卖点", "level2": "适用场景", "occurrenceCount": 1,
                "materialIds": ["m-1"], "evidence": ["早餐场景"],
            }]
        },
    })
    data["conclusion"].update({
        "portfolioDimensionConclusions": {
            topic: {"conclusion": f"{topic} 组合结论", "evidenceRefs": [f"{topic}:m-1"]}
            for topic in ("commercial", "audience", "script", "highlight", "interaction")
        },
        "batchInsights": [{"topic": "audience", "title": "人群洞察", "conclusion": "24-30 岁值得继续验证", "evidenceRefs": ["audience:m-1"]}],
        "nextActions": [{
            "title": "优先测试开场", "body": "将利益点前置到前 3 秒", "action": "改写开场", "validation": "比较 CTR", "priority": "high",
            "evidenceRefs": ["audience:m-1"], "materialIds": ["m-1"],
        }],
    })
    return data


class PresentationTests(unittest.TestCase):
    def test_projects_new_chapter_contracts_with_stable_material_references(self):
        report = build_presentation_report(complete_presentation_data())
        self.assertEqual(report["report"]["sections"], [f"chapter0{index}" for index in range(1, 7)])
        self.assertEqual([tab["id"] for tab in report["chapters"]["chapter01"]["tabs"]], ["commercial", "audience", "script", "highlight", "interaction"])
        bucket = report["chapters"]["chapter02"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"]["buckets"][0]
        self.assertEqual(bucket["sourceMaterialIds"], ["m-1"])
        group = report["chapters"]["chapter03"]["groups"][0]
        self.assertEqual((group["level1"], group["labels"][0]["level2"]), ("商品卖点", "适用场景"))
        action = report["chapters"]["chapter06"]["actions"][0]
        self.assertEqual((action["priority"], action["materialIds"]), ("high", ["m-1"]))

    def test_validation_rejects_invalid_projected_references_and_priorities(self):
        report = build_presentation_report(complete_presentation_data())
        report["chapters"]["chapter06"]["actions"][0]["priority"] = "urgent"
        with self.assertRaisesRegex(ValueError, "priority"):
            validate_presentation_report(report)
        report = build_presentation_report(complete_presentation_data())
        report["chapters"]["chapter02"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"]["buckets"][0]["sourceMaterialIds"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "materialId"):
            validate_presentation_report(report)
        report = build_presentation_report(complete_presentation_data())
        report["chapters"]["chapter03"]["groups"][0]["labels"][0]["materialIds"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "materialId"):
            validate_presentation_report(report)
        report = build_presentation_report(complete_presentation_data())
        report["chapters"]["chapter06"]["actions"][0]["materialIds"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "materialId"):
            validate_presentation_report(report)

    def test_missing_new_chapter_data_stays_renderable(self):
        report = build_presentation_report(sample_data())
        for chapter in ("chapter01", "chapter02", "chapter03", "chapter06"):
            self.assertIn(report["chapters"][chapter]["availability"], ("partial", "unavailable"))
        self.assertEqual(report["chapters"]["chapter02"]["metrics"], {})
        self.assertEqual(report["chapters"]["chapter03"]["groups"], [])
        self.assertEqual(report["chapters"]["chapter06"]["actions"], [])

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
