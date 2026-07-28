import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from final_report_lib import (  # noqa: E402
    FinalReportError,
    build_final_report_data,
    load_conclusion_analysis,
    normalize_conclusion_analysis,
)
from final_report_lib import generate_final_report  # noqa: E402
import render_final_report  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class FinalReportConclusionTests(unittest.TestCase):
    def test_accepts_valid_conclusion_schema(self):
        payload = {
            "schemaVersion": 1,
            "overallConclusion": "礼赠和滋补卖点都值得保留。",
            "keyInsights": [
                {"title": "礼盒开箱强", "body": "高光帧集中在包装动作。", "materialIds": ["m-1"]}
            ],
            "materialRecommendations": [
                {
                    "materialId": "m-1",
                    "decision": "scale",
                    "reason": "消耗和点击峰值都集中。",
                    "nextAction": "保留前 5 秒开箱动作。",
                }
            ],
            "nextActions": [
                {"title": "新拍礼赠版", "body": "强化送礼对象。", "priority": "high"}
            ],
            "warnings": ["结论来自外部分析。"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostic-ai-analysis.json"
            write_json(path, payload)

            result = load_conclusion_analysis(path)

        self.assertEqual(result["overallConclusion"], "礼赠和滋补卖点都值得保留。")
        self.assertEqual(result["materialRecommendations"][0]["decision"], "scale")

    def test_rejects_invalid_conclusion_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostic-ai-analysis.json"
            write_json(path, {"schemaVersion": 2, "overallConclusion": "bad"})

            with self.assertRaisesRegex(
                FinalReportError, "batchInsights is required for schemaVersion 2"
            ):
                load_conclusion_analysis(path)


def material(material_id, rank=1, cost=100.0):
    return {
        "rank": rank,
        "materialId": material_id,
        "materialName": f"素材 {material_id}",
        "materialType": "短视频",
        "videoType": "素材视频",
        "totalCost": cost,
    }


def click_record(material_id, second, value):
    return {
        "recordType": "interaction_series",
        "interactionMetric": "CLICK_COUNT",
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


class FinalReportDataTests(unittest.TestCase):
    def test_builds_draft_report_without_conclusion_json(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "rankingMetric": "stat_cost_for_overall_roi2",
            "materials": [material("m-1")],
        }
        records_by_material = {
            "m-1": [
                click_record("m-1", 0, 0),
                click_record("m-1", 1, 3),
                click_record("m-1", 2, 0),
            ]
        }
        summaries_by_material = {
            "m-1": {
                "summaryPath": "/run/peak-frames/m-1/summary.json",
                "frames": [
                    {
                        "rankType": "first_peak",
                        "durationSec": 1,
                        "metricValue": 3,
                        "framePath": "/run/peak-frames/m-1/first_peak_0001s_3.jpg",
                    }
                ],
                "warnings": [],
            }
        }

        result = build_final_report_data(
            top_payload,
            records_by_material,
            summaries_by_material,
            conclusion=None,
            batch_verification=None,
        )

        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["overview"]["materialCount"], 1)
        self.assertEqual(result["overview"]["highlightFrameCount"], 1)
        self.assertEqual(result["materials"][0]["extractionStatus"], "succeeded")

    def test_partial_report_keeps_failed_material(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "materials": [material("m-1", 1, 100), material("m-2", 2, 80)],
        }
        records_by_material = {"m-1": [], "m-2": []}
        summaries_by_material = {
            "m-1": {
                "summaryPath": "/run/peak-frames/m-1/summary.json",
                "frames": [
                    {
                        "rankType": "first_peak",
                        "durationSec": 1,
                        "metricValue": 3,
                        "framePath": "/run/peak-frames/m-1/first_peak_0001s_3.jpg",
                    }
                ],
                "warnings": [],
            }
        }
        batch_verification = {
            "valid": False,
            "errors": ["material m-2 is not succeeded"],
            "warnings": [],
        }

        result = build_final_report_data(
            top_payload,
            records_by_material,
            summaries_by_material,
            conclusion={
                "overallConclusion": "m-1 可复用",
                "keyInsights": [],
                "materialRecommendations": [],
                "nextActions": [],
                "warnings": [],
            },
            batch_verification=batch_verification,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            [item["materialId"] for item in result["materials"]], ["m-1", "m-2"]
        )
        self.assertEqual(result["materials"][1]["extractionStatus"], "missing_summary")
        self.assertIn("material m-2 is not succeeded", result["warnings"])


class FinalReportV2ConclusionTests(unittest.TestCase):
    def setUp(self):
        self.top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "materials": [material("m-1", 1, 100)],
        }
        self.records_by_material = {
            "m-1": [
                {
                    "recordType": "crowd_analysis",
                    "materialId": "m-1",
                    "displayMetric": "CLICK_COUNT",
                    "dimensionKey": "age",
                    "dimensionName": "年龄",
                    "dimensionValue": "24-30",
                    "metricValue": 30,
                },
                {
                    "recordType": "script_analysis",
                    "materialId": "m-1",
                    "scriptInfo": json.dumps(
                        {
                            "text": "限时优惠",
                            "data": [
                                {
                                    "level_1_label": {
                                        "value": 1,
                                        "label": "价格优惠",
                                    },
                                    "level_2_labels": [
                                        {"value": 21, "label": "降价/折扣"}
                                    ],
                                }
                            ],
                            "detail": {
                                "21": {
                                    "label": "降价/折扣",
                                    "list": ["今天四九折"],
                                }
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        }
        self.summaries_by_material = {
            "m-1": {
                "summaryPath": "/run/peak-frames/m-1/summary.json",
                "frames": [
                    {
                        "rankType": "first_peak",
                        "durationSec": 1,
                        "metricValue": 3,
                        "framePath": "/run/peak-frames/m-1/first_peak_0001s_3.jpg",
                    }
                ],
                "warnings": [],
            }
        }

    def _conclusion(self):
        return {
            "schemaVersion": 2,
            "overallConclusion": "24-30 岁点击人群值得继续验证。",
            "batchInsights": [
                {
                    "topic": "audience",
                    "title": "点击人群集中",
                    "conclusion": "点击层的 24-30 岁分桶领先。",
                    "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
                }
            ],
            "materialAnalyses": [
                {
                    "materialId": "m-1",
                    "audienceConclusion": "点击层 24-30 岁突出。",
                    "scriptConclusion": "价格优惠表达出现。",
                    "highlightAnalyses": [
                        {
                            "frameRef": "highlight:m-1:first_peak",
                            "visualObservation": "人物在产品近景旁口播。",
                            "commercialInterpretation": "该画面与首个点击峰值同时出现。",
                        }
                    ],
                    "integratedConclusion": "保留产品近景开场。",
                    "decision": "scale",
                    "nextAction": "制作三条开场变体。",
                    "evidenceRefs": [
                        "audience:CLICK_COUNT:age:24-30",
                        "script:m-1:价格优惠:降价/折扣",
                        "highlight:m-1:first_peak",
                    ],
                }
            ],
            "nextActions": [
                {"title": "扩展开场", "body": "测试三条变体。", "priority": "high"}
            ],
            "warnings": [],
        }

    def _build_data(self, conclusion):
        return build_final_report_data(
            self.top_payload,
            self.records_by_material,
            self.summaries_by_material,
            conclusion=normalize_conclusion_analysis(conclusion),
            batch_verification={"valid": True, "errors": [], "warnings": []},
        )

    def test_accepts_v2_conclusion_with_current_batch_evidence(self):
        result = self._build_data(self._conclusion())

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["conclusion"]["schemaVersion"], 2)

    def test_rejects_unknown_v2_evidence_reference(self):
        conclusion = self._conclusion()
        conclusion["batchInsights"][0]["evidenceRefs"] = [
            "audience:SHOW_COUNT:age:24-30"
        ]

        with self.assertRaisesRegex(FinalReportError, "unknown evidence reference"):
            self._build_data(conclusion)

    def test_rejects_v2_conclusion_that_analyzes_execution_process(self):
        conclusion = self._conclusion()
        conclusion["batchInsights"].append(
            {
                "topic": "integrated",
                "title": "修复后的 fresh rerun 证明 verify 收口已闭合",
                "conclusion": (
                    "rank 2 素材因 rpa_request_failed 被剔除，"
                    "后续素材补位进入 Top 6。"
                ),
                "evidenceRefs": ["highlight:m-1:first_peak"],
            }
        )

        with self.assertRaisesRegex(FinalReportError, "execution process"):
            self._build_data(conclusion)


class FinalReportRenderTests(unittest.TestCase):
    def test_template_keeps_current_structure_with_livecheck_visual_style(self):
        template_path = ROOT / "templates" / "final-report-template.html"
        html_text = template_path.read_text(encoding="utf-8")

        self.assertIn('class="hero-copy"', html_text)
        self.assertIn('class="material-tabs side-tabs"', html_text)
        self.assertIn('id="report-data">__REPORT_DATA_JSON__</script>', html_text)
        self.assertIn("--bg: #120b08;", html_text)
        self.assertIn("--paper: #fff8ec;", html_text)
        self.assertIn("--gold: #d79a4b;", html_text)
        self.assertIn(
            "background: linear-gradient(135deg, #160d09, #3b1f15 44%, #f5dfbd 44%, #fff8ec);",
            html_text,
        )
        self.assertIn("border-radius: 28px;", html_text)
        self.assertIn("--shadow: 0 28px 80px rgba(43, 23, 12, .18);", html_text)
        self.assertIn("object-fit: contain;", html_text)
        self.assertIn("grid-template-columns: 260px minmax(0, 1fr);", html_text)

    def test_generates_html_with_required_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = (
                root / "diagnostic-analysis" / "diagnostic-ai-analysis.json"
            )
            output_dir = root / "final-report"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "rankingMetric": "stat_cost_for_overall_roi2",
                    "materials": [material("m-1", 1, 123.45)],
                },
            )
            write_json(records_dir / "m-1.json", [click_record("m-1", 0, 0)])
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_3.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "materialId": "m-1",
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 3,
                            "framePath": frame_path.name,
                        }
                    ],
                    "warnings": [],
                },
            )
            write_json(
                peak_dir / "batch-verification.json",
                {"valid": True, "errors": [], "warnings": []},
            )
            write_json(
                analysis_path,
                {
                    "schemaVersion": 1,
                    "overallConclusion": "礼赠素材可以继续放量。",
                    "keyInsights": [
                        {
                            "title": "开箱强",
                            "body": "高光帧集中。",
                            "materialIds": ["m-1"],
                        }
                    ],
                    "materialRecommendations": [
                        {
                            "materialId": "m-1",
                            "decision": "scale",
                            "reason": "表现稳定",
                            "nextAction": "保留开箱",
                        }
                    ],
                    "nextActions": [
                        {
                            "title": "扩展礼赠脚本",
                            "body": "围绕送礼对象拆分。",
                            "priority": "high",
                        }
                    ],
                    "warnings": [],
                },
            )

            data_path, html_path, data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
                analysis_json=analysis_path,
            )

            html_text = html_path.read_text(encoding="utf-8")
        self.assertEqual(data_path.name, "final-report-data.json")
        self.assertEqual(html_path.name, "final-report.html")
        self.assertEqual(data["status"], "complete")
        self.assertIn("千川 TOP 素材池商业诊断报告", html_text)
        self.assertIn("01｜TOP 素材组合结论", html_text)
        self.assertIn("02｜人群洞察", html_text)
        self.assertIn("03｜脚本洞察", html_text)
        self.assertIn("04｜高光帧洞察", html_text)
        self.assertIn("05｜单素材深度分析", html_text)
        self.assertIn("06｜下一轮策略", html_text)
        self.assertIn("07｜数据质量与方法说明", html_text)
        self.assertIn("礼赠素材可以继续放量。", html_text)
        self.assertIn(
            '"framePath":"../peak-frames/m-1/first_peak_0001s_3.jpg"',
            html_text,
        )
        self.assertIn('<script type="application/json" id="report-data">', html_text)
        self.assertIn('"materials"', html_text)
        self.assertNotIn("__REPORT_DATA_JSON__", html_text)

    def test_embedded_template_data_excludes_execution_process_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            output_dir = root / "final-report"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "materials": [material("m-1", 1, 100)],
                },
            )
            write_json(records_dir / "m-1.json", [click_record("m-1", 1, 3)])
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_3.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 3,
                            "framePath": frame_path.name,
                        }
                    ]
                },
            )
            write_json(
                peak_dir / "batch-verification.json",
                {
                    "valid": False,
                    "selectedMaterials": ["m-1"],
                    "errors": ["verify failed for top_materials_rpa_ready.json"],
                    "warnings": ["batchVerification includes execution process"],
                },
            )

            _data_path, html_path, _data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
            )

            html_text = html_path.read_text(encoding="utf-8")

        self.assertIn('"materialId":"m-1"', html_text)
        self.assertNotIn("batchVerification", html_text)
        self.assertNotIn("selectedMaterials", html_text)
        self.assertNotIn("top_materials_rpa_ready", html_text)
        self.assertNotIn("verify failed", html_text)
        self.assertNotIn("execution process", html_text)

    def test_renders_v2_analysis_with_accessible_material_tabs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = root / "analysis.json"
            output_dir = root / "final-report"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "materials": [material("m-1", 1, 100), material("m-2", 2, 80)],
                },
            )
            write_json(
                records_dir / "m-1.json",
                [
                    {
                        "recordType": "crowd_analysis",
                        "materialId": "m-1",
                        "displayMetric": "CLICK_COUNT",
                        "dimensionKey": "age",
                        "dimensionName": "年龄",
                        "dimensionValue": "24-30",
                        "metricValue": 30,
                    },
                    {
                        "recordType": "script_analysis",
                        "materialId": "m-1",
                        "scriptInfo": json.dumps(
                            {
                                "text": "限时优惠",
                                "data": [
                                    {
                                        "level_1_label": {
                                            "value": 1,
                                            "label": "价格优惠",
                                        },
                                        "level_2_labels": [
                                            {"value": 21, "label": "降价/折扣"}
                                        ],
                                    }
                                ],
                                "detail": {
                                    "21": {
                                        "label": "降价/折扣",
                                        "list": ["今天四九折"],
                                    }
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            write_json(records_dir / "m-2.json", [])
            for material_id in ("m-1", "m-2"):
                material_dir = peak_dir / material_id
                material_dir.mkdir(parents=True)
                frame_path = material_dir / "first_peak_0001s_3.jpg"
                frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
                write_json(
                    material_dir / "summary.json",
                    {
                        "frames": [
                            {
                                "rankType": "first_peak",
                                "durationSec": 1,
                                "metricValue": 3,
                                "framePath": frame_path.name,
                            }
                        ]
                    },
                )
            write_json(
                analysis_path,
                {
                    "schemaVersion": 2,
                    "overallConclusion": "优先测试价格优惠的产品近景开场。",
                    "batchInsights": [
                        {
                            "topic": "audience",
                            "title": "点击人群集中",
                            "conclusion": "点击层的 24-30 岁分桶领先。",
                            "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
                        },
                        {
                            "topic": "script",
                            "title": "优惠脚本可复用",
                            "conclusion": "价格优惠标签出现。",
                            "evidenceRefs": ["script:m-1:价格优惠:降价/折扣"],
                        },
                    ],
                    "materialAnalyses": [
                        {
                            "materialId": "m-1",
                            "audienceConclusion": "点击层 24-30 岁突出。",
                            "scriptConclusion": "价格优惠表达出现。",
                            "highlightAnalyses": [
                                {
                                    "frameRef": "highlight:m-1:first_peak",
                                    "visualObservation": "人物在产品近景旁口播。",
                                    "commercialInterpretation": "画面与首个点击峰值同时出现。",
                                }
                            ],
                            "integratedConclusion": "保留产品近景开场。",
                            "decision": "scale",
                            "nextAction": "制作三条开场变体。",
                            "evidenceRefs": [
                                "audience:CLICK_COUNT:age:24-30",
                                "script:m-1:价格优惠:降价/折扣",
                                "highlight:m-1:first_peak",
                            ],
                        },
                        {
                            "materialId": "m-2",
                            "audienceConclusion": "数据不足以判断。",
                            "scriptConclusion": "数据不足以判断。",
                            "highlightAnalyses": [
                                {
                                    "frameRef": "highlight:m-2:first_peak",
                                    "visualObservation": "产品近景出现。",
                                    "commercialInterpretation": "画面与首个点击峰值同时出现。",
                                }
                            ],
                            "integratedConclusion": "先补充诊断数据。",
                            "decision": "hold",
                            "nextAction": "补充人群和脚本数据后复盘。",
                            "evidenceRefs": ["highlight:m-2:first_peak"],
                        },
                    ],
                    "nextActions": [
                        {
                            "title": "扩展开场",
                            "body": "测试三条变体。",
                            "priority": "high",
                        }
                    ],
                    "warnings": [],
                },
            )

            _data_path, html_path, _data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
                analysis_json=analysis_path,
            )

            html_text = html_path.read_text(encoding="utf-8")
        self.assertNotIn("01｜组合总览", html_text)
        for heading in (
            "01｜TOP 素材组合结论",
            "02｜人群洞察",
            "03｜脚本洞察",
            "04｜高光帧洞察",
            "05｜单素材深度分析",
            "06｜下一轮策略",
            "07｜数据质量与方法说明",
        ):
            self.assertIn(heading, html_text)
        self.assertIn('role="tablist"', html_text)
        self.assertIn('setAttribute("role", "tab")', html_text)
        self.assertIn('setAttribute("role", "tabpanel")', html_text)
        self.assertIn('"materialId":"m-1"', html_text)
        self.assertIn('"materialId":"m-2"', html_text)
        self.assertIn('tab.dataset.materialId = materialId', html_text)
        self.assertIn('setAttribute("aria-selected", String(selected))', html_text)
        self.assertIn("function activateMaterialTab", html_text)
        self.assertIn("ArrowRight", html_text)
        self.assertIn("Home", html_text)

    def test_explains_summary_parts_and_uses_side_material_tabs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = root / "analysis.json"
            output_dir = root / "final-report"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "materials": [material("m-1", 1, 100)],
                },
            )
            write_json(
                records_dir / "m-1.json",
                [
                    {
                        "recordType": "crowd_analysis",
                        "materialId": "m-1",
                        "displayMetric": "CLICK_COUNT",
                        "dimensionKey": "age",
                        "dimensionValue": "24-30",
                        "metricValue": 30,
                    },
                    {
                        "recordType": "script_analysis",
                        "materialId": "m-1",
                        "scriptInfo": json.dumps(
                            {
                                "text": "限时优惠",
                                "data": [
                                    {
                                        "level_1_label": {
                                            "value": 1,
                                            "label": "价格优惠",
                                        },
                                        "level_2_labels": [
                                            {"value": 21, "label": "降价/折扣"}
                                        ],
                                    }
                                ],
                                "detail": {
                                    "21": {
                                        "label": "降价/折扣",
                                        "list": ["今天四九折"],
                                    }
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_3.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 3,
                            "framePath": frame_path.name,
                        }
                    ]
                },
            )
            write_json(
                analysis_path,
                {
                    "schemaVersion": 2,
                    "overallConclusion": "组合表现稳定。",
                    "batchInsights": [
                        {
                            "topic": "audience",
                            "title": "点击人群集中",
                            "conclusion": "点击层 24-30 岁突出。",
                            "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
                        },
                        {
                            "topic": "script",
                            "title": "优惠脚本可复用",
                            "conclusion": "价格优惠标签出现。",
                            "evidenceRefs": ["script:m-1:价格优惠:降价/折扣"],
                        },
                        {
                            "topic": "highlight",
                            "title": "产品近景有效",
                            "conclusion": "产品近景与峰值同时出现。",
                            "evidenceRefs": ["highlight:m-1:first_peak"],
                        },
                        {
                            "topic": "integrated",
                            "title": "组合模板稳定",
                            "conclusion": "人群、脚本和画面可组合复用。",
                            "evidenceRefs": ["highlight:m-1:first_peak"],
                        },
                    ],
                    "materialAnalyses": [
                        {
                            "materialId": "m-1",
                            "audienceConclusion": "点击层 24-30 岁突出。",
                            "scriptConclusion": "价格优惠表达出现。",
                            "highlightAnalyses": [
                                {
                                    "frameRef": "highlight:m-1:first_peak",
                                    "visualObservation": "产品近景出现。",
                                    "commercialInterpretation": "画面与点击峰值同时出现。",
                                }
                            ],
                            "integratedConclusion": "保留产品近景开场。",
                            "decision": "scale",
                            "nextAction": "制作三条开场变体。",
                            "evidenceRefs": [
                                "audience:CLICK_COUNT:age:24-30",
                                "script:m-1:价格优惠:降价/折扣",
                                "highlight:m-1:first_peak",
                            ],
                        }
                    ],
                    "nextActions": [
                        {
                            "title": "扩展开场",
                            "body": "测试三条变体。",
                            "priority": "high",
                        }
                    ],
                    "warnings": [],
                },
            )

            _data_path, html_path, _data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
                analysis_json=analysis_path,
            )

            html_text = html_path.read_text(encoding="utf-8")

        for label in (
            "第一部分：组合总述",
            "第二部分：组合关键洞察",
            "第一部分：人群结论",
            "第二部分：人群证据",
            "第一部分：脚本结论",
            "第二部分：脚本证据",
            "第一部分：高光帧结论",
            "第二部分：高光帧证据",
        ):
            self.assertIn(label, html_text)
        self.assertIn('class="material-tabs side-tabs"', html_text)
        self.assertIn(".side-tabs {", html_text)
        self.assertIn("grid-template-columns: 260px minmax(0, 1fr);", html_text)
        self.assertIn("flex-direction: column;", html_text)
        self.assertIn("object-fit: contain;", html_text)
        self.assertIn(".material-body {", html_text)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", html_text)


class FinalReportCliTests(unittest.TestCase):
    def test_cli_rejects_invalid_analysis_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            output_dir = root / "final-report"
            analysis_path = root / "bad.json"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "materials": [material("m-1")],
                },
            )
            records_dir.mkdir()
            peak_dir.mkdir()
            write_json(analysis_path, {"schemaVersion": 2})

            result = render_final_report.main(
                [
                    "--top-materials-json",
                    str(top_path),
                    "--peak-output-dir",
                    str(peak_dir),
                    "--diagnosis-records-dir",
                    str(records_dir),
                    "--analysis-json",
                    str(analysis_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )

        self.assertEqual(result, 2)
