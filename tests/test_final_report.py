import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from final_report_lib import (  # noqa: E402
    FinalReportError,
    build_final_report_data,
    load_conclusion_analysis,
    normalize_conclusion_analysis,
    _validate_v2_analysis_quality,
)
from final_report_lib import generate_final_report  # noqa: E402
import render_final_report  # noqa: E402
import report_renderer  # noqa: E402
from report_renderer import create_report_archive, report_data_for_template  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_diagnosis_csv(path, records):
    headers = [
        "recordType",
        "materialId",
        "interactionMetric",
        "durationSec",
        "metricValue",
        "displayMetric",
        "dimensionKey",
        "dimensionName",
        "dimensionValue",
        "scriptInfo",
        "materialUri",
        "videoUrl",
        "thumbnailUrl",
        "videoDuration",
    ]
    rows = ["\x01".join(headers)]
    for record in records:
        values = []
        for header in headers:
            value = record.get(header, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            values.append(str(value))
        rows.append("\x02" + "\x01".join(values))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class FinalReportArchiveTests(unittest.TestCase):
    def test_creates_portable_archive_with_local_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_dir = root / "final-report"
            image_path = (
                report_dir / "assets" / "highlight-frames" / "m-1" / "peak.jpg"
            )
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"jpeg")
            (report_dir / "final-report.html").write_text(
                '<img src="assets/highlight-frames/m-1/peak.jpg">',
                encoding="utf-8",
            )
            (report_dir / "final-report-data.json").write_text(
                '{"video":{"src":"https://video.example/m-1.mp4"}}',
                encoding="utf-8",
            )

            archive_path = create_report_archive(report_dir)

            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    [
                        "final-report/assets/highlight-frames/m-1/peak.jpg",
                        "final-report/final-report-data.json",
                        "final-report/final-report.html",
                    ],
                )
                self.assertEqual(
                    archive.read("final-report/final-report-data.json"),
                    b'{"video":{"src":"https://video.example/m-1.mp4"}}',
                )

    def test_rejects_archive_when_referenced_local_image_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "final-report"
            report_dir.mkdir()
            (report_dir / "final-report.html").write_text(
                '<img src="assets/highlight-frames/m-1/missing.jpg">',
                encoding="utf-8",
            )
            (report_dir / "final-report-data.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(FinalReportError, "referenced report image"):
                create_report_archive(report_dir)


class FinalReportConclusionTests(unittest.TestCase):
    def test_next_actions_preserve_explicit_action_and_validation_with_body_fallback(self):
        conclusion = normalize_conclusion_analysis(
            {
                "schemaVersion": 1,
                "overallConclusion": "",
                "keyInsights": [],
                "materialRecommendations": [],
                "nextActions": [
                    {
                        "title": "显式策略",
                        "body": "历史正文",
                        "action": "将利益点前置到开场。",
                        "validation": "比较 CTR 与净成交 ROI。",
                        "priority": "high",
                    },
                    {"title": "历史策略", "body": "保留原始正文。", "priority": "low"},
                ],
                "warnings": [],
            }
        )

        self.assertEqual(conclusion["nextActions"][0]["action"], "将利益点前置到开场。")
        self.assertEqual(conclusion["nextActions"][0]["validation"], "比较 CTR 与净成交 ROI。")
        self.assertEqual(conclusion["nextActions"][1]["action"], "保留原始正文。")
        self.assertEqual(conclusion["nextActions"][1]["validation"], "")

    def test_v2_conclusion_requires_frame_coverage_note_for_missing_frames(self):
        top_payload = {"materials": [material("m-1")]}
        summaries = {
            "m-1": {
                "frames": [{"rankType": "zero_second", "framePath": "cover.jpg"}],
                "missingFrames": [{"rankType": "peak_top_1"}],
            }
        }
        raw_conclusion = {
            "schemaVersion": 2,
            "overallConclusion": "结论。",
            "batchInsights": [],
            "materialAnalyses": [
                {
                    "materialId": "m-1",
                    "audienceConclusion": "人群结论。",
                    "scriptConclusion": "脚本结论。",
                    "highlightAnalyses": [],
                    "integratedConclusion": "综合结论。",
                    "decision": "scale",
                    "nextAction": "继续验证。",
                    "evidenceRefs": [],
                }
            ],
            "nextActions": [],
        }
        conclusion = normalize_conclusion_analysis(raw_conclusion)

        with self.assertRaisesRegex(FinalReportError, "frameCoverageNote"):
            build_final_report_data(
                top_payload,
                {"m-1": []},
                summaries,
                conclusion=conclusion,
            )


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


class FinalReportFrameCoverageRenderingTests(unittest.TestCase):
    def test_top_dimension_conclusions_render_as_tabs(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('>汇总结论<', template)
        self.assertIn('"top-dimension-tabs"', template)
        self.assertIn('"top-dimension-panel"', template)
        self.assertIn("function activateTopDimensionTab", template)
        self.assertIn('tab.dataset.dimensionKey = key;', template)
        self.assertIn('String(index === 0)', template)
        self.assertIn('["interaction", "互动时序"]', template)
        self.assertIn("conclusion.schemaVersion !== 5", template)
        self.assertIn('root.hidden = true;', template)
        self.assertIn('fallback.hidden = false;', template)
        self.assertNotIn('root.append(element("article", "top-roi-card")', template)
        self.assertIn(".unified-report .top-dimension-tabs", css)
        self.assertIn(".unified-report .highlight-dashboard-tabs", css)
        self.assertIn("border-bottom-color: #9b6a37;", css)
        self.assertIn(".unified-report .top-overall-heading", css)
        self.assertIn("font-size: 16px;\n  font-weight: 800;", css)

    def test_template_renders_strategy_action_and_validation_without_priority_label(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('"rec-card strategy-card"', template)
        self.assertIn('"strategy-action"', template)
        self.assertIn('"strategy-validation-label", "验证口径"', template)
        self.assertIn('"strategy-validation"', template)
        self.assertNotIn("action.priority", template)
        self.assertIn("#next-actions .strategy-card .strategy-validation", css)
        self.assertIn("overflow-wrap: anywhere", css)

    def test_template_renders_frame_coverage_note_inside_material_panel(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("function renderFrameCoverageNote", template)
        self.assertIn("renderFrameCoverageNote(analysis.frameCoverageNote)", template)
        self.assertIn("高光帧说明", template)


class TopMaterialRoiReportTests(unittest.TestCase):
    def test_report_data_exposes_only_verified_top_material_roi_facts(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [material("m-1"), material("m-2", rank=2)],
        }
        roi_facts = {
            "schemaVersion": 1,
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialIds": ["m-1", "m-2"],
            "totals": {"cost": "100", "netSettlementAmount": "110", "overallPaymentAmount": "210"},
            "metricKeys": {
                "cost": "stat_cost_for_overall_roi2",
                "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
                "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
            },
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)", "value": "1.1", "displayValue": "1.10", "available": True},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)", "value": "2.1", "displayValue": "2.10", "available": True},
            ],
        }

        result = build_final_report_data(
            top_payload,
            {"m-1": [], "m-2": []},
            {"m-1": {}, "m-2": {}},
            top_material_roi=roi_facts,
        )

        self.assertEqual(result["topMaterialRoi"], {
            "source": "verifiedAssetFacts",
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialCount": 2,
            "materialIds": ["m-1", "m-2"],
            "totals": {"cost": "100", "netSettlementAmount": "110", "overallPaymentAmount": "210"},
            "metricKeys": {
                "cost": "stat_cost_for_overall_roi2",
                "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
                "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
            },
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)", "value": "1.1", "displayValue": "1.10", "available": True},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)", "value": "2.1", "displayValue": "2.10", "available": True},
            ],
        })
        self.assertNotIn("999", json.dumps(result, ensure_ascii=False))

    def test_report_rejects_roi_facts_with_unconfirmed_mapping_or_formula(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [material("m-1")],
        }
        tampered = {
            "schemaVersion": 1,
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialIds": ["m-1"],
            "totals": {"cost": "10", "netSettlementAmount": "20", "overallPaymentAmount": "30"},
            "metricKeys": {"cost": "bad", "netSettlementAmount": "bad", "overallPaymentAmount": "bad"},
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "bad", "value": "2", "available": True},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "bad", "value": "3", "available": True},
            ],
        }

        with self.assertRaisesRegex(FinalReportError, "metric keys"):
            build_final_report_data(
                top_payload, {"m-1": []}, {"m-1": {}}, top_material_roi=tampered
            )

        tampered["metricKeys"] = {
            "cost": "stat_cost_for_overall_roi2",
            "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
            "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
        }
        with self.assertRaisesRegex(FinalReportError, "formula"):
            build_final_report_data(
                top_payload, {"m-1": []}, {"m-1": {}}, top_material_roi=tampered
            )

    def test_missing_or_zero_cost_roi_facts_render_explicitly_unavailable(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [material("m-1")],
        }
        result = build_final_report_data(top_payload, {"m-1": []}, {"m-1": {}})

        self.assertEqual(result["topMaterialRoi"]["source"], "unavailable")
        self.assertEqual(
            [(metric["label"], metric["available"], metric["value"])
            for metric in result["topMaterialRoi"]["metrics"]],
            [("净成交 ROI", False, None), ("整体支付 ROI", False, None)],
        )

        zero_cost_facts = {
            "schemaVersion": 1,
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialIds": ["m-1"],
            "totals": {"cost": "0", "netSettlementAmount": "1", "overallPaymentAmount": "2"},
            "metricKeys": {
                "cost": "stat_cost_for_overall_roi2",
                "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
                "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
            },
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)", "value": None, "available": False},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)", "value": None, "available": False},
            ],
        }
        result = build_final_report_data(
            top_payload, {"m-1": []}, {"m-1": {}}, top_material_roi=zero_cost_facts
        )
        self.assertEqual(
            [(metric["available"], metric["value"])
            for metric in result["topMaterialRoi"]["metrics"]],
            [(False, None), (False, None)],
        )

    def test_report_rejects_roi_facts_from_candidate_pool(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [material("m-1")],
        }
        candidate_pool_facts = {
            "schemaVersion": 1,
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialIds": ["m-1", "candidate-m-2"],
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "value": "9", "available": True},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "value": "9", "available": True},
            ],
        }

        with self.assertRaisesRegex(FinalReportError, "verified report materials"):
            build_final_report_data(
                top_payload, {"m-1": []}, {"m-1": {}},
                top_material_roi=candidate_pool_facts,
            )

    def test_generate_report_reads_roi_facts_from_peak_frames_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            peak_dir = root / "peak-frames"
            write_json(top_path, {
                "actualStartDate": "2026-06-01",
                "actualEndDate": "2026-06-02",
                "materials": [material("m-1")],
            })
            write_json(peak_dir / "top-material-roi-facts-v1.json", {
                "schemaVersion": 1,
                "actualStartDate": "2026-06-01",
                "actualEndDate": "2026-06-02",
                "materialIds": ["m-1"],
                "totals": {"cost": "4", "netSettlementAmount": "5", "overallPaymentAmount": "10"},
                "metricKeys": {
                    "cost": "stat_cost_for_overall_roi2",
                    "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
                    "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
                },
                "metrics": [
                    {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)", "value": "1.25", "available": True},
                    {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)", "value": "2.5", "available": True},
                ],
            })

            _data_path, _html_path, data = generate_final_report(
                top_path, peak_dir, root / "diagnosis-records", root / "final-report"
            )

        self.assertEqual(data["topMaterialRoi"]["source"], "verifiedAssetFacts")
        self.assertEqual(data["topMaterialRoi"]["metrics"][0]["value"], "1.25")

    def test_template_keeps_roi_data_but_does_not_render_top_roi_cards(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('id="top-material-roi"', template)
        self.assertNotIn("function renderTopMaterialRoi", template)
        self.assertNotIn('const card = element("div", "top-roi-card");', template)

    def test_report_data_formats_available_roi_to_two_decimal_places(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [material("m-1")],
        }
        roi_facts = {
            "schemaVersion": 1,
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialIds": ["m-1"],
            "totals": {
                "cost": "100",
                "netSettlementAmount": "120",
                "overallPaymentAmount": "234.5",
            },
            "metricKeys": {
                "cost": "stat_cost_for_overall_roi2",
                "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
                "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
            },
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)", "value": "1.2", "available": True},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)", "value": "2.345", "available": True},
            ],
        }

        result = build_final_report_data(
            top_payload, {"m-1": []}, {"m-1": {}}, top_material_roi=roi_facts
        )

        self.assertEqual(
            [metric["value"] for metric in result["topMaterialRoi"]["metrics"]],
            ["1.2", "2.345"],
        )
        self.assertEqual(
            [metric["displayValue"] for metric in result["topMaterialRoi"]["metrics"]],
            ["1.20", "2.35"],
        )

    def test_template_has_v3_dimension_conclusion_container(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="top-dimension-conclusions"', template)
        self.assertIn("function renderTopDimensionConclusions", template)


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


def interaction_record(material_id, metric, second, value):
    return {
        "recordType": "interaction_series",
        "interactionMetric": metric,
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


def script_record(material_id):
    return {
        "recordType": "script_analysis",
        "materialId": material_id,
        "materialUri": f"uri-{material_id}",
        "scriptInfo": json.dumps(
            {
                "text": "早餐可以不碰碳水，但鲜炖燕窝一定要坚持吃。",
                "formula": [
                    {
                        "level_1_label": {"value": 1, "label": "商品卖点"},
                        "level_2_label": {"value": 11, "label": "适用场景"},
                    }
                ],
                "data": [
                    {
                        "level_1_label": {"value": 1, "label": "商品卖点"},
                        "level_2_labels": [
                            {"value": 11, "label": "适用场景"},
                            {"value": 12, "label": "使用感受"},
                        ],
                    }
                ],
                "detail": {
                    "11": {
                        "label": "适用场景",
                        "list": ["早餐可以不碰碳水，但鲜炖燕窝一定要坚持吃。"],
                    },
                    "12": {
                        "label": "使用感受",
                        "list": ["浓稠捞丝，吃起来口感 q 弹。"],
                    },
                },
            },
            ensure_ascii=False,
        ),
    }


def material_info_record(material_id):
    return {
        "recordType": "material_info",
        "materialId": material_id,
        "videoUrl": f"https://video.example/{material_id}.mp4?token=signed",
        "thumbnailUrl": f"https://image.example/{material_id}.jpg?token=signed",
        "videoDuration": "46.022",
    }


class FinalReportDataTests(unittest.TestCase):
    def test_copied_asset_report_keeps_highlight_images_out_of_html(self):
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
                    "actualEndDate": "2026-06-02",
                    "materials": [material("m-1")],
                },
            )
            write_json(
                records_dir / "m-1.json",
                [material_info_record("m-1"), click_record("m-1", 1, 9)],
            )
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_9.jpg"
            frame_path.write_bytes(b"\xff\xd8" + (b"large-jpeg" * 1000) + b"\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 9,
                            "framePath": frame_path.name,
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
                copy_assets=True,
            )

            html_text = html_path.read_text(encoding="utf-8")
            copied_frame = (
                output_dir
                / "assets"
                / "highlight-frames"
                / "m-1"
                / "first_peak_0001s_9.jpg"
            )
            copied_frame_exists = copied_frame.is_file()
            copied_frame_payload = copied_frame.read_bytes() if copied_frame_exists else b""
            original_frame_payload = frame_path.read_bytes()

        self.assertTrue(copied_frame_exists)
        self.assertEqual(copied_frame_payload, original_frame_payload)
        self.assertIn("assets/highlight-frames/m-1/first_peak_0001s_9.jpg", html_text)
        self.assertNotIn("data:image/", html_text)
        self.assertNotIn("../peak-frames/", html_text)
        self.assertIn("document.createElement(\"video\")", html_text)
        self.assertIn('video.preload = "none"', html_text)
        self.assertIn("video.src = src", html_text)
        self.assertNotIn("data:video/", html_text)

    def test_embedded_report_inlines_highlight_images_and_keeps_external_video_preview(self):
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
                    "actualEndDate": "2026-06-02",
                    "materials": [material("m-1")],
                },
            )
            write_json(
                records_dir / "m-1.json",
                [material_info_record("m-1"), click_record("m-1", 1, 9)],
            )
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_9.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 9,
                            "framePath": frame_path.name,
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
                embed_assets=True,
            )

            html_text = html_path.read_text(encoding="utf-8")

        self.assertIn("data:image/jpeg;base64,", html_text)
        self.assertNotIn("../peak-frames/", html_text)
        self.assertIn("document.createElement(\"video\")", html_text)
        self.assertIn('video.preload = "none"', html_text)
        self.assertIn("video.src = src", html_text)
        self.assertNotIn("<video", html_text)
        self.assertNotIn("data:video/", html_text)

    def test_material_cards_include_interaction_timeline_and_script_analysis(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "materials": [material("m-1")],
        }
        records_by_material = {
            "m-1": [
                interaction_record("m-1", "CLICK_COUNT", 2, 5),
                interaction_record("m-1", "CLICK_COUNT", 1, 9),
                interaction_record("m-1", "CLICK_COUNT", 3, 9),
                interaction_record("m-1", "LIKE_COUNT", 1, 2),
                interaction_record("m-1", "LOSE_COUNT", 1, 7),
                interaction_record("m-1", "COMMENT_COUNT", 1, 1),
                interaction_record("m-1", "SHARE_COUNT", 1, 3),
                interaction_record("m-1", "NEW_FOLLOWER_COUNT", 1, 4),
                {
                    "recordType": "crowd_analysis",
                    "materialId": "m-1",
                    "displayMetric": "CLICK_COUNT",
                    "dimensionKey": "age",
                    "dimensionName": "年龄",
                    "dimensionValue": "24-30",
                    "metricValue": 12,
                },
                script_record("m-1"),
            ]
        }
        summaries_by_material = {
            "m-1": {
                "summaryPath": "/run/peak-frames/m-1/summary.json",
                "frames": [],
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

        card = result["materials"][0]
        timeline = card["interactionTimeline"]
        self.assertEqual(
            [metric["metric"] for metric in timeline["metrics"]],
            [
                "CLICK_COUNT",
                "LOSE_COUNT",
                "LIKE_COUNT",
                "COMMENT_COUNT",
                "SHARE_COUNT",
                "NEW_FOLLOWER_COUNT",
            ],
        )
        click_metric = timeline["metrics"][0]
        self.assertEqual(click_metric["label"], "整体点击次数")
        self.assertEqual(
            click_metric["points"],
            [
                {"second": 1.0, "value": 9.0},
                {"second": 2.0, "value": 5.0},
                {"second": 3.0, "value": 9.0},
            ],
        )
        self.assertEqual(click_metric["peak"], {"second": 1.0, "value": 9.0})
        self.assertEqual(
            card["materialAudience"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"]["buckets"],
            [{"bucket": "24-30", "metricValue": 12.0, "metricPercentage": 100.0, "sourceMaterialIds": ["m-1"]}],
        )
        script = card["scriptAnalysis"]
        self.assertEqual(script["materialUri"], "uri-m-1")
        self.assertEqual(script["text"], "早餐可以不碰碳水，但鲜炖燕窝一定要坚持吃。")
        self.assertIn({"level1": "商品卖点", "level2": "适用场景"}, script["labels"])
        self.assertIn({"level1": "商品卖点", "level2": "适用场景"}, script["formula"])
        self.assertEqual(script["evidence"][0]["level2"], "适用场景")

    def test_material_cards_include_video_metadata_from_material_info(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "materials": [material("m-1")],
        }
        records_by_material = {"m-1": [material_info_record("m-1")]}

        result = build_final_report_data(
            top_payload,
            records_by_material,
            summaries_by_material={},
            conclusion=None,
            batch_verification=None,
        )

        self.assertEqual(
            result["materials"][0]["video"],
            {
                "src": "https://video.example/m-1.mp4?token=signed",
                "poster": "https://image.example/m-1.jpg?token=signed",
                "durationSec": 46.022,
            },
        )

    def test_generate_report_accepts_retained_diagnosis_csv_dir_for_visual_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            csv_dir = root / "diagnosis-csv"
            peak_dir = root / "peak-frames"
            output_dir = root / "final-report"
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-02",
                    "materials": [material("m-1")],
                },
            )
            write_diagnosis_csv(
                csv_dir / "m-1.csv",
                [
                    click_record("m-1", 0, 10),
                    click_record("m-1", 1, 20),
                    script_record("m-1"),
                    material_info_record("m-1"),
                    {
                        "recordType": "crowd_analysis",
                        "materialId": "m-1",
                        "displayMetric": "SHOW_COUNT",
                        "dimensionKey": "age",
                        "dimensionName": "年龄",
                        "dimensionValue": "24-30",
                        "metricValue": 100,
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

            _data_path, _html_path, data = generate_final_report(
                top_path,
                peak_dir,
                csv_dir,
                output_dir,
            )

            material_card = data["materials"][0]
            self.assertEqual(
                material_card["interactionTimeline"]["metrics"][0]["points"][1],
                {"second": 1.0, "value": 20.0},
            )
            self.assertEqual(
                material_card["scriptAnalysis"]["formula"][0]["level2"], "适用场景"
            )
            self.assertEqual(
                material_card["video"]["src"],
                "https://video.example/m-1.mp4?token=signed",
            )
            self.assertEqual(
                data["audience"]["metrics"]["SHOW_COUNT"]["dimensions"]["age"]["buckets"][0]["bucket"],
                "24-30",
            )

    def test_complete_report_requires_retained_diagnosis_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = root / "diagnostic-analysis.json"
            output_dir = root / "final-report"
            records_dir.mkdir()
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-02",
                    "materials": [material("m-1")],
                },
            )
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
                analysis_path,
                {
                    "schemaVersion": 2,
                    "overallConclusion": "产品近景可继续验证。",
                    "batchInsights": [
                        {
                            "topic": "audience",
                            "title": "人群数据不足",
                            "conclusion": "当前素材缺少人群分桶数据，需补充后再判断放量空间。",
                            "evidenceRefs": ["highlight:m-1:first_peak"],
                        }
                    ],
                    "materialAnalyses": [
                        {
                            "materialId": "m-1",
                            "audienceConclusion": "人群数据不足以判断。",
                            "scriptConclusion": "脚本数据不足以判断。",
                            "highlightAnalyses": [
                                {
                                    "frameRef": "highlight:m-1:first_peak",
                                    "visualObservation": "产品近景出现。",
                                    "commercialInterpretation": "低理解成本画面可保留。",
                                }
                            ],
                            "integratedConclusion": "继续保留产品近景。",
                            "decision": "hold",
                            "nextAction": "补充人群和脚本数据后再判断。",
                            "evidenceRefs": ["highlight:m-1:first_peak"],
                        }
                    ],
                    "nextActions": [
                        {
                            "title": "补齐诊断数据",
                            "body": "重新读取 RPA 诊断记录。",
                            "priority": "high",
                        }
                    ],
                },
            )

            _data_path, _html_path, data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
                analysis_json=analysis_path,
            )

            self.assertEqual(data["status"], "partial")
            self.assertIn(
                "missing diagnosis records for material m-1",
                data["warnings"],
            )

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

    def test_includes_pipeline_summary_from_manifest_and_verification(self):
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
        batch_manifest = {
            "targetTopN": 2,
            "candidateCount": 6,
            "submittedMaterials": [
                {
                    "materialId": "m-1",
                    "rpaStatus": "selected",
                    "postprocessStatus": "succeeded",
                    "diagnosisRecordsPath": "/run/diagnosis-records/m-1.json",
                },
                {
                    "materialId": "m-2",
                    "rpaStatus": "selected",
                    "postprocessStatus": "failed",
                    "diagnosisRecordsPath": "/run/diagnosis-records/m-2.json",
                },
                {"materialId": "m-3", "rpaStatus": "rejected", "postprocessStatus": "pending"},
                {"materialId": "m-4", "rpaStatus": "cancelled", "postprocessStatus": "not_started"},
            ],
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
            batch_manifest=batch_manifest,
        )

        self.assertEqual(
            result["pipelineSummary"],
            {
                "runId": None,
                "targetTopN": 2,
                "candidateCount": 6,
                "submittedCount": 3,
                "selectedCount": 2,
                "rejectedCount": 1,
                "cancelledCount": 1,
                "postprocessSucceededCount": 1,
                "postprocessFailedCount": 1,
                "rpaRequestCount": 3,
                "rpaAcceptedCount": 0,
                "rpaSelectedCount": 2,
                "recordsPersistedCount": 0,
                "recordsPersistedUnknownCount": 4,
                "reportReadyCount": 0,
                "verificationValid": False,
                "verificationErrors": ["material m-2 is not succeeded"],
                "verificationWarnings": [],
            },
        )


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
            "overallConclusion": "24-30 岁点击次数值得继续验证。",
            "batchInsights": [
                {
                    "topic": "audience",
                    "title": "点击次数集中",
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

    def test_rejects_v2_conclusion_without_next_actions(self):
        conclusion = self._conclusion()
        conclusion.pop("nextActions")

        with self.assertRaisesRegex(FinalReportError, "nextActions is required"):
            self._build_data(conclusion)

    def test_rejects_v2_highlight_batch_insight(self):
        conclusion = self._conclusion()
        conclusion["batchInsights"].append(
            {
                "topic": "highlight",
                "title": "产品近景有效",
                "conclusion": "产品近景与峰值同时出现。",
                "evidenceRefs": ["highlight:m-1:first_peak"],
            }
        )

        with self.assertRaisesRegex(FinalReportError, "topic is unsupported"):
            normalize_conclusion_analysis(conclusion)

    def test_rejects_v2_integrated_batch_insight(self):
        conclusion = self._conclusion()
        conclusion["batchInsights"].append(
            {
                "topic": "integrated",
                "title": "组合模板稳定",
                "conclusion": "组合洞察已经由 overallConclusion 承载。",
                "evidenceRefs": ["highlight:m-1:first_peak"],
            }
        )

        with self.assertRaisesRegex(FinalReportError, "topic is unsupported"):
            normalize_conclusion_analysis(conclusion)

    def test_v2_conclusion_drops_page_unused_warnings(self):
        conclusion = self._conclusion()
        conclusion["warnings"] = ["素材 m-1 缺少补充帧，仅作谨慎判断。"]

        result = self._build_data(conclusion)

        self.assertNotIn("warnings", result["conclusion"])
        self.assertNotIn("素材 m-1 缺少补充帧，仅作谨慎判断。", result["warnings"])

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
                "topic": "audience",
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


class FinalReportV3ConclusionTests(unittest.TestCase):
    def setUp(self):
        FinalReportV2ConclusionTests.setUp(self)

    def _roi_facts(self):
        return {
            "schemaVersion": 1,
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-30",
            "materialIds": ["m-1"],
            "totals": {
                "cost": "100",
                "netSettlementAmount": "120",
                "overallPaymentAmount": "150",
            },
            "metricKeys": {
                "cost": "stat_cost_for_overall_roi2",
                "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
                "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
            },
            "metrics": [
                {"key": "netSettlementRoi", "label": "净成交 ROI", "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)", "value": "1.2", "available": True},
                {"key": "overallPaymentRoi", "label": "整体支付 ROI", "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)", "value": "1.5", "available": True},
            ],
        }

    def _conclusion(self):
        conclusion = FinalReportV2ConclusionTests._conclusion(self)
        conclusion["schemaVersion"] = 3
        conclusion["portfolioDimensionConclusions"] = {
            "commercial": {
                "conclusion": "净成交 ROI 与整体支付 ROI 均可作为下一轮素材组合验证的商业基线，应在保持整体消耗可控的前提下比较不同创意表达。",
                "evidenceRefs": ["commercial:netSettlementRoi", "commercial:overallPaymentRoi"],
            },
            "audience": {
                "conclusion": "组合层面可优先围绕已有点击人群信号继续验证创意表达差异，而不是直接复述单个分桶的表现。",
                "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
            },
            "script": {
                "conclusion": "价格优惠脚本可作为组合测试中的稳定对照条件，并通过调整利益点呈现顺序识别真正驱动表现的表达机制。",
                "evidenceRefs": ["script:m-1:价格优惠:降价/折扣"],
            },
            "highlight": {
                "conclusion": "最终入选素材的首个峰值画面包含人物与产品近景，可作为验证产品信息前置效果的画面假设。",
                "evidenceRefs": ["highlight:m-1:first_peak"],
            },
        }
        return conclusion

    def _build_data(self, conclusion):
        return build_final_report_data(
            self.top_payload,
            self.records_by_material,
            self.summaries_by_material,
            conclusion=normalize_conclusion_analysis(conclusion),
            batch_verification={"valid": True, "errors": [], "warnings": []},
            top_material_roi=self._roi_facts(),
        )

    def test_accepts_v3_dimension_conclusions_without_overall_conclusion(self):
        result = self._build_data(self._conclusion())

        self.assertEqual(result["conclusion"]["schemaVersion"], 3)
        self.assertEqual(result["conclusion"]["overallConclusion"], "")
        self.assertEqual(
            list(result["conclusion"]["portfolioDimensionConclusions"]),
            ["commercial", "audience", "script", "highlight"],
        )

    def test_template_payload_preserves_v3_dimension_conclusions(self):
        result = self._build_data(self._conclusion())

        template_data = report_data_for_template(result, None)

        self.assertEqual(template_data["conclusion"]["schemaVersion"], 3)
        self.assertIn(
            "portfolioDimensionConclusions", template_data["conclusion"]
        )
        self.assertEqual(
            list(template_data["conclusion"]["portfolioDimensionConclusions"]),
            ["commercial", "audience", "script", "highlight"],
        )

    def test_rejects_v3_missing_dimension_or_exposed_data_source(self):
        conclusion = self._conclusion()
        conclusion["portfolioDimensionConclusions"].pop("highlight")
        with self.assertRaisesRegex(FinalReportError, "highlight"):
            normalize_conclusion_analysis(conclusion)

        conclusion = self._conclusion()
        conclusion["portfolioDimensionConclusions"]["commercial"]["conclusion"] = (
            "资产表显示净成交 ROI 可继续验证。"
        )
        with self.assertRaisesRegex(FinalReportError, "资产表"):
            self._build_data(conclusion)

    def test_rejects_v3_dimension_text_copied_from_detailed_insight(self):
        conclusion = self._conclusion()
        conclusion["portfolioDimensionConclusions"]["audience"]["conclusion"] = (
            conclusion["batchInsights"][0]["conclusion"]
        )

        with self.assertRaisesRegex(FinalReportError, "duplicates"):
            self._build_data(conclusion)


class FinalReportV4HighlightDashboardTests(unittest.TestCase):
    def setUp(self):
        FinalReportV3ConclusionTests.setUp(self)

    def _roi_facts(self):
        return FinalReportV3ConclusionTests._roi_facts(self)

    def _conclusion(self):
        conclusion = FinalReportV3ConclusionTests._conclusion(self)
        conclusion["schemaVersion"] = 4
        conclusion["highlightDashboard"] = {
            "groups": [
                {
                    "category": "portrait",
                    "frameRefs": ["highlight:m-1:first_peak"],
                    "commonConclusion": "人物口播与产品同框，是本组已查看高光画面中的共同可见信号。",
                    "derivativeDirections": [
                        {
                            "title": "验证人物开场景别",
                            "body": "保持人物与产品同框，分别测试近景和中景，观察点击表现是否出现稳定差异。",
                            "evidenceRefs": ["highlight:m-1:first_peak"],
                        }
                    ],
                }
            ]
        }
        return conclusion

    def _build_data(self, conclusion):
        return build_final_report_data(
            self.top_payload,
            self.records_by_material,
            self.summaries_by_material,
            conclusion=normalize_conclusion_analysis(conclusion),
            batch_verification={"valid": True, "errors": [], "warnings": []},
            top_material_roi=self._roi_facts(),
        )

    def test_accepts_v4_dashboard_that_covers_each_successful_frame_once(self):
        result = self._build_data(self._conclusion())

        self.assertEqual(result["conclusion"]["schemaVersion"], 4)
        self.assertEqual(
            result["conclusion"]["highlightDashboard"]["groups"][0]["category"],
            "portrait",
        )

    def test_accepts_v5_with_interaction_dimension_and_visible_overall_conclusion(self):
        conclusion = self._conclusion()
        conclusion["schemaVersion"] = 5
        conclusion["overallConclusion"] = "五个维度共同指向先验证开场吸引力，再观察成交承接。"
        conclusion["portfolioDimensionConclusions"]["interaction"] = {
            "conclusion": "点击与流失的相对时段分布可用于判断开场和中段的节奏风险，并应与素材改写后的净成交表现共同验证。",
            "evidenceRefs": ["interaction:CLICK_COUNT:0-10%"],
        }
        conclusion["batchInsights"] = [
            {
                "topic": "audience",
                "title": "人群洞察",
                "conclusion": "女性人群表现值得继续验证。",
                "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
            },
            {
                "topic": "script",
                "title": "脚本洞察",
                "conclusion": "价格机制值得继续验证。",
                "evidenceRefs": ["script:m-1:价格优惠:降价/折扣"],
            },
        ]
        self.records_by_material["m-1"].append(
            {
                "recordType": "interaction_series",
                "interactionMetric": "CLICK_COUNT",
                "durationSec": 1,
                "metricValue": 3,
            }
        )
        normalized = normalize_conclusion_analysis(conclusion)
        self.assertEqual(normalized["schemaVersion"], 5)
        self.assertEqual(
            list(normalized["portfolioDimensionConclusions"]),
            ["commercial", "audience", "script", "highlight", "interaction"],
        )
        self.assertEqual(normalized["overallConclusion"], conclusion["overallConclusion"])

    def test_v5_batch_insights_are_distributed_to_audience_and_script_sections(self):
        conclusion = self._conclusion()
        conclusion["schemaVersion"] = 5
        conclusion["overallConclusion"] = "五个维度共同指向开场优化和成交承接验证。"
        conclusion["portfolioDimensionConclusions"]["interaction"] = {
            "conclusion": "点击与流失的相对时段分布可用于判断开场和中段的节奏风险，并应与素材改写后的净成交表现共同验证。",
            "evidenceRefs": ["interaction:CLICK_COUNT:0-10%"],
        }
        conclusion["batchInsights"] = [
            {
                "topic": "audience",
                "title": "人群洞察",
                "conclusion": "女性人群表现值得继续验证。",
                "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
            },
            {
                "topic": "script",
                "title": "脚本洞察",
                "conclusion": "价格机制值得继续验证。",
                "evidenceRefs": ["script:m-1:价格优惠:降价/折扣"],
            },
        ]
        self.records_by_material["m-1"].append(
            {
                "recordType": "interaction_series",
                "interactionMetric": "CLICK_COUNT",
                "durationSec": 1,
                "metricValue": 3,
            }
        )
        result = self._build_data(conclusion)

        self.assertEqual(len(result["batchInsights"]["audience"]), 1)
        self.assertEqual(len(result["batchInsights"]["script"]), 1)

    def test_rejects_v4_dashboard_with_duplicate_or_missing_frame_reference(self):
        duplicate = self._conclusion()
        duplicate["highlightDashboard"]["groups"].append(
            {
                "category": "other",
                "frameRefs": ["highlight:m-1:first_peak"],
                "commonConclusion": "同一高光帧不得同时归入其他分类，否则会破坏分类汇总的唯一性。",
                "derivativeDirections": [
                    {
                        "title": "保持唯一归类",
                        "body": "每个成功高光帧只能归入一个分类，避免重复画面同时支撑多个分类结论。",
                        "evidenceRefs": ["highlight:m-1:first_peak"],
                    }
                ],
            }
        )
        with self.assertRaisesRegex(FinalReportError, "exactly once"):
            self._build_data(duplicate)

        missing = self._conclusion()
        missing["highlightDashboard"]["groups"][0]["frameRefs"] = []
        with self.assertRaisesRegex(FinalReportError, "must not be empty"):
            normalize_conclusion_analysis(missing)

    def test_rejects_v4_dashboard_with_unknown_or_cross_group_reference(self):
        unknown = self._conclusion()
        unknown["highlightDashboard"]["groups"][0]["frameRefs"] = [
            "highlight:m-1:peak_top_5"
        ]
        unknown["highlightDashboard"]["groups"][0]["derivativeDirections"][0][
            "evidenceRefs"
        ] = ["highlight:m-1:peak_top_5"]
        with self.assertRaisesRegex(FinalReportError, "unknown evidence reference"):
            self._build_data(unknown)

        outside_group = self._conclusion()
        outside_group["highlightDashboard"]["groups"][0]["derivativeDirections"][0][
            "evidenceRefs"
        ] = ["audience:CLICK_COUNT:age:24-30"]
        with self.assertRaisesRegex(FinalReportError, "must reference its group"):
            self._build_data(outside_group)

    def test_rejects_v4_dashboard_without_ordered_substantive_directions(self):
        no_direction = self._conclusion()
        no_direction["highlightDashboard"]["groups"][0]["derivativeDirections"] = []
        with self.assertRaisesRegex(FinalReportError, "must not be empty"):
            normalize_conclusion_analysis(no_direction)

        short_common_conclusion = self._conclusion()
        short_common_conclusion["highlightDashboard"]["groups"][0][
            "commonConclusion"
        ] = "太短"
        with self.assertRaisesRegex(FinalReportError, "commonConclusion must contain"):
            normalize_conclusion_analysis(short_common_conclusion)

        short_direction = self._conclusion()
        short_direction["highlightDashboard"]["groups"][0][
            "derivativeDirections"
        ][0]["body"] = "太短"
        with self.assertRaisesRegex(FinalReportError, "body must contain"):
            normalize_conclusion_analysis(short_direction)

        unordered = self._conclusion()
        unordered["highlightDashboard"]["groups"] = [
            {
                "category": "product",
                "frameRefs": ["highlight:placeholder:peak_top_1"],
                "commonConclusion": "产品包装位于画面中央，是已查看帧中可见的产品主视觉信号。",
                "derivativeDirections": [
                    {
                        "title": "验证产品景别",
                        "body": "保持产品包装为主视觉，分别测试近景和中景，观察点击信号是否出现稳定差异。",
                        "evidenceRefs": ["highlight:placeholder:peak_top_1"],
                    }
                ],
            },
            self._conclusion()["highlightDashboard"]["groups"][0],
        ]
        with self.assertRaisesRegex(FinalReportError, "fixed category order"):
            normalize_conclusion_analysis(unordered)


class FinalReportQualityGateTests(unittest.TestCase):
    def test_standard_quality_gate_requires_v4_highlight_dashboard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = root / "diagnostic-ai-analysis.json"
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
                        "dimensionName": "年龄",
                        "dimensionValue": "24-30",
                        "metricValue": 30,
                    },
                    script_record("m-1"),
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
                    ],
                    "warnings": [],
                },
            )
            write_json(
                analysis_path,
                {
                    "schemaVersion": 2,
                    "overallConclusion": "当前素材值得继续测试。",
                    "batchInsights": [
                        {
                            "topic": "audience",
                            "title": "人群表现",
                            "conclusion": "24-30 岁点击更高。",
                            "evidenceRefs": [],
                        },
                        {
                            "topic": "script",
                            "title": "脚本表现",
                            "conclusion": "优惠表达可用。",
                            "evidenceRefs": [],
                        },
                    ],
                    "materialAnalyses": [
                        {
                            "materialId": "m-1",
                            "audienceConclusion": "24-30 岁点击更高。",
                            "scriptConclusion": "优惠表达可用。",
                            "highlightAnalyses": [
                                {
                                    "frameRef": "highlight:m-1:first_peak",
                                    "visualObservation": "产品近景出现。",
                                    "commercialInterpretation": "画面与点击峰值同时出现。",
                                }
                            ],
                            "integratedConclusion": "保留产品近景。",
                            "decision": "scale",
                            "nextAction": "继续测试。",
                            "evidenceRefs": [],
                        }
                    ],
                    "nextActions": [
                        {
                            "title": "扩展开场",
                            "body": "测试变体。",
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
            self.assertEqual(data["status"], "complete")
            self.assertTrue(data_path.is_file())
            self.assertTrue(html_path.is_file())

            with self.assertRaises(FinalReportError) as error:
                generate_final_report(
                    top_path,
                    peak_dir,
                    records_dir,
                    output_dir,
                    analysis_json=analysis_path,
                    require_ai_quality=True,
                )

        message = str(error.exception)
        self.assertIn("AI quality gate", message)
        self.assertIn("schemaVersion 4", message)

    def test_standard_quality_gate_rejects_repeated_material_conclusions(self):
        repeated_audience = "24-30 岁用户对产品利益点的关注更集中，下一轮应保留利益点前置并继续验证不同开场的点击差异。" * 3
        repeated_script = "脚本以日常滋补场景承接产品利益点，表达路径可保留，同时需要测试更直接的利益点呈现方式。" * 3
        repeated_integrated = "素材应继续保留产品近景与滋补场景的组合，并通过不同开场节奏验证其对点击与理解成本的影响。" * 3
        repeated_action = "保留当前产品近景和滋补场景，分别测试利益点前置、人物口播与产品特写的三种开场变体。" * 3
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_path = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            analysis_path = root / "diagnostic-ai-analysis.json"
            output_dir = root / "final-report"
            material_ids = ("m-1", "m-2")
            write_json(
                top_path,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-30",
                    "materials": [
                        material(material_id, index + 1, 100 - index * 10)
                        for index, material_id in enumerate(material_ids)
                    ],
                },
            )
            for material_id in material_ids:
                write_json(
                    records_dir / f"{material_id}.json",
                    [
                        {
                            "recordType": "crowd_analysis",
                            "materialId": material_id,
                            "displayMetric": "CLICK_COUNT",
                            "dimensionKey": "age",
                            "dimensionName": "年龄",
                            "dimensionValue": "24-30",
                            "metricValue": 30,
                        },
                        script_record(material_id),
                    ],
                )
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
                        ],
                        "warnings": [],
                    },
                )
            write_json(
                analysis_path,
                {
                    "schemaVersion": 2,
                    "overallConclusion": "两条素材均在 24-30 岁点击分桶和日常滋补脚本中呈现可继续验证的信号。组合判断不是直接放量，而是保留产品利益点与场景承接，分别测试开场节奏、人物口播和产品特写，观察点击、理解成本与后续互动是否同步改善。素材之间存在相似表现时，仍需区分真实创意机制与描述重复，避免把同一结论复制到每一条素材。" * 2,
                    "batchInsights": [
                        {
                            "topic": "audience",
                            "title": "核心年龄分桶有点击信号",
                            "conclusion": "24-30 岁分桶在两条素材中均有点击记录，说明日常滋补场景对该人群有继续验证的价值。下一轮应保持人群口径一致，比较利益点前置和场景前置的开场，确认点击信号是否来自创意表达而非单次样本波动。" * 2,
                            "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"],
                        },
                        {
                            "topic": "script",
                            "title": "滋补场景脚本可作为对照",
                            "conclusion": "两条素材都包含日常滋补相关脚本标签，可作为后续创意迭代的共同对照条件。应在保留场景信息的同时更换利益点位置和口播方式，识别是场景代入还是利益点表达在推动点击。" * 2,
                            "evidenceRefs": ["script:m-1:商品卖点:适用场景"],
                        },
                    ],
                    "materialAnalyses": [
                        {
                            "materialId": material_id,
                            "audienceConclusion": repeated_audience,
                            "scriptConclusion": repeated_script,
                            "highlightAnalyses": [
                                {
                                    "frameRef": f"highlight:{material_id}:first_peak",
                                    "visualObservation": f"素材 {material_id} 的首个峰值帧中，人物将燕窝产品置于画面中央，产品包装、勺取动作与口播人物同时可见，画面信息完整且焦点明确。",
                                    "commercialInterpretation": "产品、使用动作和人物口播在同一画面中出现，可能降低用户理解产品利益点的成本。该画面与点击峰值同时出现，只能作为下一轮验证产品特写与人物口播组合的假设，不能直接视为因果结论。" * 2,
                                }
                            ],
                            "integratedConclusion": repeated_integrated,
                            "decision": "scale",
                            "nextAction": repeated_action,
                            "evidenceRefs": [
                                "audience:CLICK_COUNT:age:24-30",
                                f"script:{material_id}:商品卖点:适用场景",
                                f"highlight:{material_id}:first_peak",
                            ],
                        }
                        for material_id in material_ids
                    ],
                    "nextActions": [
                        {
                            "title": "测试利益点前置",
                            "body": "将产品利益点放在前两秒，并与现有场景开场比较点击表现和理解成本。",
                            "priority": "high",
                        },
                        {
                            "title": "测试产品特写时长",
                            "body": "保持滋补场景不变，分别测试短产品特写和长产品特写对首段互动的影响。",
                            "priority": "high",
                        },
                        {
                            "title": "测试人物口播版本",
                            "body": "保留产品与使用动作，替换人物口播的利益点顺序并观察人群点击变化。",
                            "priority": "medium",
                        },
                        {
                            "title": "复盘人群分桶差异",
                            "body": "按年龄分桶比较各开场变体的点击信号，确认创意机制是否具有可复用性。",
                            "priority": "medium",
                        },
                    ],
                    "warnings": [],
                },
            )

            _data_path, _html_path, data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
                analysis_json=analysis_path,
            )
            with self.assertRaises(FinalReportError) as error:
                _validate_v2_analysis_quality(data["conclusion"], data["materials"])

        message = str(error.exception)
        self.assertIn("AI quality gate", message)
        self.assertIn("repeated", message)
        for field_name in (
            "audienceConclusion",
            "scriptConclusion",
            "integratedConclusion",
            "nextAction",
        ):
            self.assertIn(field_name, message)


class FinalReportRenderTests(unittest.TestCase):
    def test_skill_documents_unified_report_delivery_contract(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("assets/report/", skill)
        self.assertIn("内联", skill)
        self.assertIn("不得手工修改", skill)
        self.assertIn("1440x1000", skill)

    def test_unified_report_style_defines_text_card_and_control_contract(self):
        css = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("--unified-text-card: #f7ead8;", css)
        self.assertIn(".top-dimension-card", css)
        self.assertIn(".highlight-summary", css)
        self.assertIn(".tab-list button", css)
        self.assertIn("background: var(--unified-text-card);", css)
        self.assertIn("border-radius: 8px;", css)

    def test_render_html_inlines_vendored_unified_assets(self):
        rendered = report_renderer.render_html_report({"status": "complete"})

        self.assertIn('data-unified-report-version="2026-08-09"', rendered)
        self.assertIn(".unified-report .report-shell", rendered)
        self.assertIn("data-unified-report-ready", rendered)
        self.assertNotIn('rel="stylesheet"', rendered)
        self.assertNotIn('script src=', rendered)
        self.assertNotIn("__REPORT_STYLE_CSS__", rendered)
        self.assertNotIn("__REPORT_SCRIPT_JS__", rendered)

    def test_render_html_rejects_missing_unified_asset(self):
        with patch.object(
            report_renderer,
            "REPORT_STYLE_PATH",
            ROOT / "assets" / "report" / "missing.css",
        ):
            with self.assertRaisesRegex(report_renderer.ReportRenderingError, "report style"):
                report_renderer.render_html_report({"status": "complete"})

    def test_render_html_rejects_template_missing_unified_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "template.html"
            template_path.write_text(
                "<style>__REPORT_STYLE_CSS__</style>"
                '<script id="report-data">__REPORT_DATA_JSON__</script>',
                encoding="utf-8",
            )
            with patch.object(report_renderer, "REPORT_TEMPLATE_PATH", template_path):
                with self.assertRaisesRegex(
                    report_renderer.ReportRenderingError,
                    "__REPORT_SCRIPT_JS__",
                ):
                    report_renderer.render_html_report({"status": "complete"})

    def test_template_emits_unified_desktop_report_structure(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<body class="unified-report unified-qianchuan"', template)
        self.assertIn('<header class="hero report-cover">', template)
        self.assertNotIn('class="eyebrow"', template)
        self.assertIn("千川 TOP 素材池商业诊断报告", template)
        self.assertIn('class="report-shell"', template)
        self.assertIn('class="report-nav" aria-label="报告目录"', template)
        self.assertEqual(template.count('class="section-header"'), 6)
        for section_id in (
            "top-material-combination",
            "audience-insights",
            "script-insights",
            "highlight-dashboard-section",
            "material-analysis",
            "next-actions-section",
        ):
            self.assertIn(f'id="{section_id}"', template)

    def test_unified_report_removes_duplicate_dividers(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('root.append(divider);', template)
        self.assertIn(".unified-report .section-header {", css_text)
        self.assertIn("border-bottom: 0;", css_text)
        self.assertIn(".unified-report .insight-layout {", css_text)
        self.assertIn("border-top: 0;", css_text)
        self.assertIn(".unified-report .top-roi-grid + .top-dimension-conclusions {", css_text)
        self.assertIn("border-top: 1px solid var(--unified-line);", css_text)
        self.assertIn(".unified-report .top-roi-grid + .top-dimension-conclusions .top-dimension-card:nth-child(n + 3)", css_text)
        self.assertIn("border-top: 1px solid var(--unified-line);", css_text)
        self.assertIn("border-bottom: 0;", css_text)
        self.assertIn(".unified-report .top-roi-grid + .top-dimension-conclusions .top-dimension-card:nth-child(2n)::before", css_text)
        self.assertIn("top: 18px;", css_text)
        self.assertIn("bottom: 18px;", css_text)
        self.assertNotIn(".top-dimension-card:nth-child(2n) {\n  padding-left: var(--unified-content-gap);\n  border-left: 1px solid var(--unified-line);", css_text)

    def test_batch_data_dashboards_use_collapsible_raw_fact_shells(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('function createCollapsibleDataCard(title = "", toggleLabel = "数据")', template)
        self.assertIn('element("div", "raw-fact-card dashboard-data-card")', template)
        self.assertIn('const { card, body } = createCollapsibleDataCard("", "人群汇总数据");', template)
        self.assertIn('const { card, body } = createCollapsibleDataCard("", "脚本汇总数据");', template)
        self.assertIn('getScriptDashboardBody()', template)
        self.assertIn('resetCollapsibleBody(body, "", view);', template)
        self.assertNotIn('createCollapsibleDataCard("脚本汇总数据")', template)
        self.assertNotIn('getScriptDashboardBody("脚本汇总数据")', template)
        self.assertNotIn('resetCollapsibleBody(body, "脚本汇总数据", view);', template)
        self.assertIn(".unified-report .dashboard-data-card {", css_text)
        self.assertIn(".unified-report .dashboard-data-card > .collapsible-body {", css_text)
        self.assertIn(".unified-report .dashboard-data-card > .collapsible-body[hidden] {", css_text)
        self.assertIn("display: none;", css_text)
        self.assertIn(".unified-report .dashboard-data-card .metric-switch button {", css_text)
        self.assertIn("min-height: 36px !important;", css_text)
        self.assertIn(".unified-report .dashboard-data-card .collapsible-body-title {", css_text)
        self.assertIn("font-size: 18px;", css_text)
        self.assertIn(".unified-report #audience-dashboard .collapsible-body {", css_text)
        self.assertIn("grid-template-columns: minmax(0, 1fr) max-content;", css_text)
        self.assertIn(".unified-report #audience-dashboard .metric-switch {", css_text)
        self.assertIn("justify-self: end;", css_text)

    def test_dense_timeline_metric_buttons_and_highlight_grid_height_are_stable(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("for (let index = pageRefs.length; index < HIGHLIGHT_FRAMES_PER_PAGE; index += 1)", template)
        self.assertIn("const HIGHLIGHT_FRAMES_PER_PAGE = 12;", template)
        self.assertIn('element("div", "highlight-frame-card highlight-frame-placeholder")', template)
        self.assertIn(".unified-report .material-body .raw-fact-card .metric-switch {", css_text)
        self.assertIn("flex-wrap: nowrap;", css_text)
        self.assertIn(".unified-report .material-body .raw-fact-card .metric-switch button {", css_text)
        self.assertIn("font-size: 10px;", css_text)
        self.assertIn("padding: 3px 5px;", css_text)
        self.assertIn(".unified-report .highlight-frame-grid {", css_text)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr));", css_text)
        self.assertIn("grid-auto-rows: minmax(0, auto);", css_text)
        self.assertIn("min-height: 0;", css_text)
        self.assertIn(".unified-report .highlight-frame-preview {", css_text)
        self.assertIn("aspect-ratio: 9 / 16;", css_text)
        self.assertIn(".unified-report .highlight-frame-placeholder {", css_text)
        self.assertIn("visibility: hidden;", css_text)
        self.assertNotIn('element("div", "highlight-category-head")', template)

    def test_highlight_dashboard_uses_approved_category_and_card_labels(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('portrait: "人像画面"', template)
        self.assertIn('product: "产品画面"', template)
        self.assertIn('other: "其他画面"', template)
        self.assertIn('`点击 Top${peakRank} 波峰画面`', template)
        self.assertIn(
            '`${formatNumber(frame.durationSec)}s · 点击 ${formatNumber(frame.metricValue)}`',
            template,
        )
        self.assertNotIn('`秒点 ${formatNumber(frame.durationSec)}s · 点击 ${formatNumber(frame.metricValue)}`', template)
        self.assertNotIn('`${index + 1}：`', template)
        small_start = css_text.index(".unified-report .highlight-frame-meta small {")
        small_end = css_text.index("}", small_start)
        small_rules = css_text[small_start:small_end]
        timing_start = css_text.index(".unified-report .highlight-frame-timing {")
        timing_end = css_text.index("}", timing_start)
        timing_rules = css_text[timing_start:timing_end]
        self.assertIn("text-align: center;", small_rules)
        self.assertIn("font-size: 11px;", small_rules)
        self.assertIn("font-size: 11px;", timing_rules)
        self.assertIn("openFrameLightbox(text(frame.framePath), caption)", template)
        self.assertIn("activateMaterialById(materialId)", template)
        self.assertNotIn("startSec", template)
        self.assertNotIn("endSec", template)

    def test_audience_pie_legends_use_fixed_dashboard_layouts(self):
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(".unified-report .dashboard-data-card .chart-grid > .chart-card:nth-child(2) .pie-legend {", css_text)
        self.assertIn("grid-template-columns: repeat(3, max-content);", css_text)
        self.assertIn(".unified-report .dashboard-data-card .chart-grid > .chart-card:nth-child(3) .pie-legend {", css_text)
        self.assertIn("align-content: center;", css_text)
        self.assertIn("min-height: 96px;", css_text)
        self.assertIn(".unified-report .material-audience-dashboard .chart-grid > .chart-card:nth-child(2) .pie-legend {", css_text)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", css_text)

    def test_material_timeline_metrics_stay_compact(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(encoding="utf-8")
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(encoding="utf-8")
        self.assertIn('element("div", "metric-switch interaction-timeline-switcher")', template)
        self.assertIn(".unified-report .interaction-timeline-switcher {", css_text)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr));", css_text)
        self.assertIn("min-height: 30px !important;", css_text)
        self.assertIn("font-size: 11px;", css_text)
        self.assertIn("padding: 4px 3px;", css_text)

    def test_material_analysis_uses_named_script_toggle_and_compact_chart_controls(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(encoding="utf-8")
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(encoding="utf-8")

        self.assertIn('element("p", "highlight-selection-hint", "被选中的高光帧对应的说明会出现在下方。")', template)
        self.assertIn('appendCollapsibleHead(card, "脚本分析数据", body, "脚本数据");', template)
        self.assertIn(".unified-report .material-audience-dashboard .column-chart {", css_text)
        self.assertIn("min-height: 250px;", css_text)
        self.assertIn(".unified-report .material-audience-dashboard .column-item {", css_text)
        self.assertIn("height: 180px;", css_text)
        self.assertIn("min-height: 38px;", css_text)

    def test_highlight_selection_uses_full_frame_card_hit_area(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(encoding="utf-8")
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(encoding="utf-8")

        self.assertIn('button.setAttribute("aria-label", `查看大图：${captionText}`)', template)
        self.assertIn('caption.addEventListener("click", (event) => {', template)
        self.assertIn('selection.set(reference, !figure._selectionInput.checked);', template)
        self.assertIn('if (event.target.closest(".highlight-frame-choice")) return;', template)
        self.assertIn('font-size: 14px;', css_text)
        self.assertIn("width: 16px;", css_text)
        self.assertIn("height: 16px;", css_text)

    def test_unified_report_applies_latest_visual_comment_tweaks(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('const sample = element("div", "highlight-sample-overview");', template)
        self.assertNotIn("root.append(sample);", template)
        self.assertIn(".unified-report .copy-button {", css_text)
        self.assertIn("font-size: 14px;", css_text)
        self.assertIn("grid-template-columns: 184px minmax(0, 1fr);", css_text)
        self.assertIn("overflow-x: hidden;", css_text)
        self.assertIn(".unified-report .report-nav a {", css_text)
        self.assertIn("white-space: nowrap;", css_text)
        self.assertNotIn(".unified-report .report-nav a:first-of-type", css_text)
        self.assertIn(".unified-report #next-actions {", css_text)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", css_text)
        self.assertIn(".unified-report #next-actions .rec-card:nth-child(2n)::before", css_text)
        self.assertIn("left: calc(var(--unified-content-gap) / -2);", css_text)
        self.assertIn(".unified-report .audience-drill-panel .preference-card {", css_text)
        self.assertIn("background: transparent;", css_text)
        self.assertIn("padding: 0;", css_text)
        self.assertIn('"p",\n    "preference-rule-note",', template)
        self.assertIn("单个素材在该人群上的占比高于基准才进入候选池", template)
        self.assertIn(".unified-report .preference-rule-note {", css_text)
        self.assertIn("line-height: 1.7;", css_text)

    def test_template_preview_uses_unified_stylesheet_link(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        rendered = report_renderer.render_html_report({"status": "complete"})

        self.assertIn('data-template-preview-style="true"', template)
        self.assertIn('href="../assets/report/unified-report.css"', template)
        self.assertNotIn('data-template-preview-style="true"', rendered)
        self.assertNotIn('href="../assets/report/unified-report.css"', rendered)

    def test_template_delegates_visual_styles_to_unified_stylesheet(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("<style>\n    :root", template)
        self.assertIn('data-template-preview-style="true"', template)
        self.assertIn("--bg: #120b08;", css_text)
        self.assertIn(
            "background: linear-gradient(135deg, #160d09, #3b1f15 44%, #f5dfbd 44%, #fff8ec);",
            css_text,
        )

    def test_audience_region_card_header_lives_inside_card(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('const regionCard = element("div", "chart-card chart-card-full region-card");', template)
        self.assertIn('const regionHeader = element("div", "region-card-header");', template)
        self.assertIn('const regionTabs = element("div", "metric-switch region-tabs");', template)
        self.assertIn('regionHeader.append(element("h3", "", "地域分布"), regionTabs);', template)
        self.assertIn("card.append(regionHeader, body);", template)
        self.assertIn("hideTitle: true,", template)

    def test_unified_report_centers_pie_legends_and_unframes_highlight_tabs(self):
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("justify-content: center;", css_text)
        self.assertIn("flex-wrap: wrap;", css_text)
        self.assertIn("overflow-x: visible;", css_text)
        self.assertIn(".unified-report .highlight-dashboard-tabs {", css_text)
        self.assertIn("border: 0;", css_text)
        self.assertIn("background: transparent;", css_text)
        self.assertIn(".unified-report #script-dashboard .pie-layout {", css_text)
        self.assertIn("display: grid;", css_text)
        self.assertIn("grid-template-columns: 200px minmax(0, 1fr);", css_text)
        self.assertIn(".unified-report #script-dashboard .pie-chart {", css_text)
        self.assertIn("width: 200px !important;", css_text)
        self.assertIn(".unified-report #script-dashboard .pie-legend {", css_text)
        self.assertIn("grid-template-columns: repeat(2, max-content);", css_text)
        self.assertIn(".unified-report #audience-dashboard .script-dashboard-overview .column-chart {", css_text)
        self.assertIn("min-height: 240px;", css_text)
        self.assertIn(".unified-report #audience-dashboard .script-dashboard-overview .column-item {", css_text)
        self.assertIn("height: 180px;", css_text)
        self.assertIn(".unified-report #audience-dashboard .script-dashboard-overview,", css_text)
        self.assertIn(".unified-report #audience-dashboard .script-drill-detail {", css_text)
        self.assertIn("min-height: 0;", css_text)
        self.assertIn(".unified-report #audience-dashboard .script-drill-primary {", css_text)
        self.assertIn("grid-template-rows: auto 150px auto;", css_text)
        self.assertIn(".unified-report #audience-dashboard .pie-legend .legend-item > span:nth-child(2) {", css_text)
        self.assertIn("font-weight: 800;", css_text)
        self.assertIn(".unified-report #audience-dashboard .pie-legend .legend-item > span:nth-child(3) {", css_text)
        self.assertIn("font-weight: 400;", css_text)

    def test_region_columns_stay_within_plot_when_many_buckets_are_visible(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('plot.dataset.columnDensity = itemCount > 36 ? "dense" : itemCount > 18 ? "compact" : "normal";', template)
        self.assertIn('plot.style.setProperty("--column-gap", itemCount > 36 ? "0px" : itemCount > 24 ? "4px" : itemCount > 14 ? "8px" : "18px");', template)
        self.assertIn('bar.style.width = itemCount > 36 ? "10px" : itemCount > 24 ? "14px" : itemCount > 14 ? "22px" : "min(48px, 56%)";', template)
        self.assertIn(".unified-report .column-plot[data-column-density=\"dense\"]", css_text)
        self.assertIn("overflow: hidden;", css_text)
        self.assertIn(".unified-report .region-card .column-plot", css_text)
        self.assertIn("overflow: visible !important;", css_text)
        self.assertIn(".unified-report .column-plot[data-column-density=\"dense\"] .column-bar", css_text)
        self.assertIn("width: 100% !important;", css_text)
        self.assertIn("display: block;", css_text)
        self.assertIn("max-width: 100%;", css_text)
        self.assertIn("text-overflow: ellipsis;", css_text)
        self.assertIn("writing-mode: vertical-rl;", css_text)
        self.assertIn(".unified-report .chart-card-plain", css_text)
        self.assertIn("border: 0 !important;", css_text)
        self.assertIn("border-color: transparent !important;", css_text)
        self.assertIn(".unified-report .column-item:first-child .chart-tooltip", css_text)
        self.assertIn(".unified-report .region-card .column-item:first-child .chart-tooltip", css_text)
        self.assertIn("transform: translateX(0);", css_text)
        self.assertIn(".unified-report .region-tabs button", css_text)
        self.assertIn("top: 16px;", css_text)
        self.assertIn("bottom: auto;", css_text)
        self.assertIn("width: min(1280px, calc(100% - 20px));", css_text)
        self.assertIn("grid-template-columns: 184px minmax(0, 1fr);", css_text)

    def test_template_keeps_current_structure_with_livecheck_visual_style(self):
        template_path = ROOT / "templates" / "final-report-template.html"
        html_text = template_path.read_text(encoding="utf-8")
        css_text = (ROOT / "assets" / "report" / "unified-report.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="hero-copy"', html_text)
        self.assertIn('class="insight-layout"', html_text)
        self.assertIn('class="primary-summary"', html_text)
        self.assertIn('class="material-tabs side-tabs"', html_text)
        self.assertIn('id="report-data">__REPORT_DATA_JSON__</script>', html_text)
        self.assertNotIn("<style>\n    :root", html_text)
        self.assertIn(".insight-secondary {", css_text)
        self.assertIn("width: 100%;", css_text)
        self.assertIn("grid-template-columns: 1fr;", css_text)
        self.assertIn("grid-template-columns: 190px minmax(0, 1fr);", css_text)
        self.assertIn("统计周期 ${text(period.actualStartDate)} - ${text(period.actualEndDate)}`", html_text)
        self.assertNotIn("报告状态", html_text)
        self.assertNotIn("min-height: 360px;", html_text)
        self.assertIn("function humanizeEvidenceRef", html_text)
        self.assertIn("function displayText", html_text)
        self.assertNotIn('.replace(/\\b\\d{15,}\\b/g, "对应素材")', html_text)
        self.assertIn("function renderTextWithMaterialLinks", html_text)
        self.assertIn("function activateMaterialById", html_text)
        self.assertIn("scrollIntoView({ behavior: \"smooth\", block: \"start\" })", html_text)
        self.assertIn("data-material-link", html_text)
        self.assertIn("展现次数", html_text)
        self.assertIn("点击次数", html_text)
        self.assertIn("成交金额", html_text)
        self.assertIn("脚本标签", html_text)
        self.assertIn("高光帧", html_text)
        self.assertNotIn("evidenceRefs.join", html_text)
        self.assertIn('id="audience-dashboard"', html_text)
        self.assertIn('id="audience-preferences"', html_text)
        self.assertIn('id="script-dashboard"', html_text)
        self.assertIn("function animateScriptDrill", html_text)
        self.assertIn("function renderScriptDrill", html_text)
        self.assertIn("function returnToScriptOverview", html_text)
        self.assertIn("返回一级标签", html_text)
        self.assertIn("点击一级标签柱可查看二级标签分布", html_text)
        self.assertIn("二级标签分布", html_text)
        self.assertNotIn("function materialAnchorId", html_text)
        self.assertNotIn("function scriptMaterialLink", html_text)
        self.assertNotIn('id="integrated-insights"', html_text)
        self.assertIn("function renderInsightList", html_text)
        self.assertNotIn(
            'renderInsightList("integrated-insights", reportData.batchInsights?.integrated, "暂无组合洞察");',
            html_text,
        )
        self.assertIn(
            'renderInsightList("audience-primary", reportData.batchInsights?.audience, "暂无人群洞察");',
            html_text,
        )
        self.assertIn("function renderAudiencePreferences", html_text)
        self.assertIn(
            'renderInsightList("audience-primary", reportData.batchInsights?.audience, "暂无人群洞察");\n  renderAudiencePreferences();\n  renderAudienceDashboard();',
            html_text,
        )
        self.assertIn(
            'renderInsightList("script-primary", reportData.batchInsights?.script, "暂无脚本洞察");',
            html_text,
        )
        self.assertNotIn("function renderPrimaryInsight", html_text)
        self.assertIn('id="highlight-dashboard"', html_text)
        self.assertIn("function renderHighlightDashboard", html_text)
        self.assertIn("function renderDashboardFrame", html_text)
        self.assertIn("activateMaterialById(materialId)", html_text)
        self.assertNotIn("帧对应具体话术", html_text)
        self.assertIn("function renderAudienceDashboard", html_text)
        self.assertIn("function renderScriptDashboard", html_text)
        self.assertIn("function createCollapseToggle", html_text)
        self.assertIn("collapsible-toggle", html_text)
        self.assertIn("collapsible-toggle-action", html_text)
        self.assertIn("collapsible-body-title", html_text)
        self.assertNotIn("collapsible-toggle-label", html_text)
        self.assertIn("width: 100%;", css_text)
        self.assertIn("justify-content: center;", css_text)
        self.assertIn("font-size: 16px;", css_text)
        self.assertIn("collapsible-body", html_text)
        self.assertIn(".collapsible-body[hidden] {", css_text)
        self.assertIn("display: none;", css_text)
        self.assertIn('button.setAttribute("aria-expanded", String(expanded));', html_text)
        self.assertIn('action.textContent = expanded ? `收起${label}` : `展开${label}`;', html_text)
        self.assertIn('function createCollapseToggle(target, label = "数据", defaultExpanded = true)', html_text)
        self.assertIn("target.hidden = !expanded;", html_text)
        self.assertIn('if (title) target.prepend(element("b", "collapsible-body-title", title));', html_text)
        self.assertIn('createCollapsibleDataCard("", "人群汇总数据")', html_text)
        self.assertIn('className = "audience-dashboard-controls"', html_text)
        self.assertIn('function createCollapsibleDataCard(title = "", toggleLabel = "数据")', html_text)
        self.assertIn('appendCollapsibleHead(card, title, body, toggleLabel);', html_text)
        self.assertNotIn("收起真实数据", html_text)
        self.assertNotIn("展开真实数据", html_text)
        self.assertIn("function renderInteractionTimeline", html_text)
        self.assertIn("function renderTimelineChart", html_text)
        self.assertIn("function formatTimelineSecond", html_text)
        self.assertIn("function createTimelineScaleControl", html_text)
        self.assertIn("timeline-scale", html_text)
        self.assertIn("timeline-tooltip", html_text)
        self.assertIn("timeline-axis-label", html_text)
        self.assertIn("timeline-metric-label", html_text)
        self.assertIn("timeline-point-hitbox", html_text)
        self.assertIn("height: 320px;", css_text)
        self.assertIn('svg.setAttribute("viewBox", "0 0 840 320");', html_text)
        self.assertIn("right: 810,", html_text)
        self.assertIn("top: 56,", html_text)
        self.assertIn("bottom: 266,", html_text)
        self.assertIn('class: "timeline-metric-label"', html_text)
        self.assertIn("时间范围", html_text)
        self.assertIn("text(metric.label)", html_text)
        self.assertIn("formatTimelineSecond(point.second)", html_text)
        self.assertIn("renderTimelineChart(metric, state.visibleRatio)", html_text)
        self.assertIn("function updateTimelineScaleTrack", html_text)
        self.assertIn("--timeline-scale-progress", html_text)
        self.assertIn("updateTimelineScaleTrack(state);", html_text)
        self.assertIn("stroke: var(--gold);", css_text)
        self.assertIn("fill: #9b6a37;", css_text)
        self.assertIn("accent-color: #9b6a37;", css_text)
        self.assertNotIn("#2f66ff", html_text)
        self.assertIn("function hydrateMaterialPanel", html_text)
        self.assertIn("function renderVisualStrip", html_text)
        self.assertIn('element("div", "visual-strip")', html_text)
        self.assertIn('element("div", "video-panel")', html_text)
        self.assertIn("--visual-height: 560px;", css_text)
        self.assertIn("--video-width: calc(var(--visual-height) * 9 / 16);", css_text)
        self.assertIn("grid-template-columns: var(--video-width) minmax(0, 1fr);", css_text)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", css_text)
        self.assertIn("height: var(--visual-height);", css_text)
        self.assertIn("width: var(--video-width);", css_text)
        self.assertIn("justify-self: start;", css_text)
        self.assertIn("aspect-ratio: 9 / 16;", css_text)
        self.assertIn("grid-template-rows: repeat(2, minmax(0, 1fr));", css_text)
        self.assertIn("grid-template-rows: minmax(0, 1fr) auto;", css_text)
        self.assertIn("min-height: 0;", css_text)
        self.assertNotIn("height: 175px;", css_text)
        self.assertNotIn("min-height: 430px;", css_text)
        self.assertIn("frame-preview-button", html_text)
        self.assertIn("frame-lightbox", html_text)
        self.assertIn('id="frame-lightbox"', html_text)
        self.assertIn('aria-modal="true"', html_text)
        self.assertIn("function openFrameLightbox", html_text)
        self.assertIn("function closeFrameLightbox", html_text)
        self.assertIn('event.key === "Escape"', html_text)
        self.assertIn('button.setAttribute("aria-label", `查看大图：${captionText}`)', html_text)
        self.assertIn("function renderScriptAnalysis", html_text)
        self.assertIn("互动时序分析", html_text)
        self.assertIn("脚本分析数据", html_text)
        self.assertIn("暂无互动时序数据", html_text)
        self.assertIn("暂无脚本分析数据", html_text)
        self.assertNotIn('element("p", "muted", "脚本标签")', html_text)
        self.assertIn("function renderScriptFormulaChain", html_text)
        self.assertIn("function copyScriptFormula", html_text)
        self.assertIn("function copyScriptText", html_text)
        self.assertIn("function copyTextToClipboard", html_text)
        self.assertIn("function scriptFormulaColor", html_text)
        self.assertIn("function formulaKey", html_text)
        self.assertIn("function renderScriptTranscript", html_text)
        self.assertIn("script-highlight", html_text)
        self.assertIn("script-analysis-section-header", html_text)
        self.assertIn("script-formula-chain", html_text)
        self.assertIn("script-formula-item", html_text)
        self.assertIn("script-formula-label", html_text)
        self.assertIn("script-formula-text", html_text)
        self.assertIn("function formulaColorKey", html_text)
        self.assertIn("function buildFormulaColorMap", html_text)
        self.assertIn("function activateScriptFormula", html_text)
        self.assertIn('node.classList.contains("script-formula-token")', html_text)
        self.assertIn('segment.addEventListener("click"', html_text)
        self.assertIn("script-formula-connector", html_text)
        self.assertIn("script-text-body", html_text)
        self.assertIn("script-segment", html_text)
        self.assertIn("copy-button", html_text)
        self.assertIn("data-formula-key", html_text)
        self.assertIn("data-formula-color-key", html_text)
        self.assertIn("script-formula-token", html_text)
        self.assertIn("script-formula-title-label", html_text)
        self.assertIn("font-size: 14px;", css_text)
        self.assertIn("line-height: 1.55;", css_text)
        self.assertIn("overflow: visible;", css_text)
        self.assertNotIn("min-width: 138px;", css_text)
        self.assertNotIn("max-width: 240px;", css_text)
        self.assertNotIn("grid-template-columns: auto minmax(0, 1fr);", css_text)
        self.assertNotIn("max-height: 120px;", css_text)
        self.assertNotIn("function renderFormulaButtons", html_text)
        self.assertNotIn('button.className = "chip formula-chip"', html_text)
        self.assertNotIn('card.append(element("p", "muted", "证据句"), evidenceList)', html_text)
        self.assertIn("document.createElement(\"video\")", html_text)
        self.assertIn('video.preload = "none"', html_text)
        self.assertIn('image.loading = "lazy"', html_text)
        self.assertIn('image.decoding = "async"', html_text)
        self.assertNotIn("const visualNodes = [renderFrames(material)];", html_text)
        self.assertNotIn("panel.append(head, ...visualNodes, body);", html_text)
        self.assertIn("function renderMaterialWorkspace(material)", html_text)
        self.assertIn("function renderHighlightWorkspace(material)", html_text)
        self.assertIn('workspacePanels.get("interaction").append(', html_text)
        self.assertIn('workspacePanels.get("audience").append(', html_text)
        self.assertIn('workspacePanels.get("script").append(', html_text)
        self.assertIn('workspacePanels.get("decision").append(', html_text)
        self.assertIn("function renderHighlightDashboard", html_text)
        self.assertNotIn("data:video/", html_text)
        self.assertIn("八大人群分布", html_text)
        self.assertIn("年龄分布", html_text)
        self.assertIn("性别分布", html_text)
        self.assertIn("地域分布", html_text)
        self.assertIn("function renderPieChart", html_text)
        self.assertIn("function renderRegionChart", html_text)
        self.assertIn("省份", html_text)
        self.assertIn("城市", html_text)
        self.assertIn("pie-chart", html_text)
        self.assertIn("function renderColumnChart", html_text)
        self.assertIn("column-chart", html_text)
        self.assertIn("column-bar", html_text)
        self.assertIn("chart-card-full", html_text)
        self.assertIn("chart-tooltip", html_text)
        self.assertIn(".chart-tooltip-line {", css_text)
        self.assertIn("display: block;", css_text)
        self.assertIn("function appendTooltipLines", html_text)
        self.assertIn('tooltip.append(element("span", "chart-tooltip-line", line));', html_text)
        self.assertIn("item.title =", html_text)
        self.assertIn("function buildPieSliceTooltipMap", html_text)
        self.assertIn("function findPieBucketAtPosition", html_text)
        self.assertIn("pie.addEventListener(\"pointermove\"", html_text)
        self.assertIn("pie.addEventListener(\"pointerenter\"", html_text)
        self.assertIn("const activeBucket = findPieBucketAtPosition", html_text)
        self.assertIn("pie.append(pieTooltip)", html_text)
        self.assertIn("pie.title =", html_text)
        self.assertIn("width: 320px;", css_text)
        self.assertNotIn(".pie-chart {\n      position: relative;\n      width: 320px;\n      aspect-ratio: 1;\n      border-radius: 50%;\n      background: conic-gradient(var(--gold) 0deg, var(--gold) 360deg);\n      box-shadow: 0 0 0 1px rgba(82, 45, 24, .1);\n      overflow: hidden;", css_text)
        self.assertNotIn("inset 0 0 0 58px", css_text)
        self.assertIn("const allBuckets = Array.isArray", html_text)
        self.assertIn("axisLabel: \"人数\"", html_text)
        self.assertIn("function createRegionScaleControl", html_text)
        self.assertIn("const visibleRatio = Number(state.ratio || 0.35);", html_text)
        self.assertIn("const visibleCount = Math.max(minVisible, Math.ceil(allBuckets.length * visibleRatio));", html_text)
        self.assertIn("scaleInput.type = \"range\";", html_text)
        self.assertIn("scaleInput.max = \"1\";", html_text)
        self.assertIn("scaleInput.step = \"0.005\";", html_text)
        self.assertNotIn("requestAnimationFrame", html_text)
        self.assertIn("chartMount.replaceChildren(chart);", html_text)
        self.assertIn("const scaleMount = element(\"div\", \"\");", html_text)
        self.assertIn("body.append(chartMount, scaleMount);", html_text)
        self.assertIn("const scale = createRegionScaleControl(state, renderRegion, allBuckets.length);", html_text)
        self.assertIn("if (scaleMount.firstChild !== scale) {", html_text)
        self.assertIn("scaleMount.replaceChildren(scale);", html_text)
        self.assertIn("if (!state.control) {", html_text)
        self.assertIn("state.control = scaleInput;", html_text)
        self.assertIn("state.control.min = String(state.minRatio || 0.02);", html_text)
        self.assertIn("state.metaTitle = title;", html_text)
        self.assertIn("state.metaDetail = detail;", html_text)
        self.assertIn("province_name: { ratio: 0.35, minRatio: 0.05, minVisible: 6 },", html_text)
        self.assertIn("city_name: { ratio: 0.16, minRatio: 0.005, minVisible: 4 },", html_text)
        self.assertIn("const minVisible = state.minVisible || (activeDimensionKey === \"city_name\" ? 12 : 8);", html_text)
        self.assertIn("plot.style.gridTemplateColumns = `repeat(${itemCount}, minmax(0, 1fr))`;", html_text)
        self.assertIn("bar.style.width = itemCount > 36 ? \"10px\" : itemCount > 24 ? \"14px\" : itemCount > 14 ? \"22px\" : \"min(48px, 56%)\";", html_text)
        self.assertNotIn("column-chart-scroll", html_text)
        self.assertIn("tooltipLines: (bucket) => [", html_text)
        self.assertIn("`占比 ${formatNumber(bucket.metricPercentage ?? 0)}%`", html_text)
        self.assertIn("`人数 ${formatNumber(bucket.metricValue ?? bucket.value ?? 0)}`", html_text)
        self.assertIn('const regionCard = element("div", "chart-card chart-card-full region-card");', html_text)
        self.assertIn('"region-card-header"', html_text)
        self.assertIn(".chart-card-plain {", css_text)
        self.assertIn("border-color: transparent;", css_text)
        self.assertIn("padding-left: 10px;", css_text)
        self.assertIn("padding-right: 10px;", css_text)
        self.assertIn('const card = element("div", `chart-card chart-card-full column-chart-card${options.cardClass ? ` ${options.cardClass}` : ""}`);', html_text)
        self.assertIn(".column-chart-card {", css_text)
        self.assertIn('cardClass: "chart-card-plain"', html_text)
        self.assertIn("function renderAudienceGroupDrill", html_text)
        self.assertIn("renderAudiencePreferenceCard(selectedName, metricKey)", html_text)
        self.assertIn("preferencesByMetric[metricKey]", html_text)
        self.assertIn("`${metricLabels[metricKey] || \"人群\"}占比 ${formatNumber(item.audiencePercentage)}%`", html_text)
        self.assertIn("function renderAudienceDashboardOverview", html_text)
        self.assertIn("点击八大人群柱可查看该人群 TOP3 素材", html_text)
        self.assertIn("返回八大人群分布", html_text)
        self.assertIn("function returnToAudienceOverview", html_text)
        self.assertIn("detail.classList.add(\"is-leaving\");", html_text)
        self.assertIn("renderAudienceDashboardOverview(root, buckets, metricKey, { selectedName, transition })", html_text)
        self.assertIn("onBucketClick: (bucket, item) => renderAudienceGroupDrill(root, buckets, bucket, metricKey, item)", html_text)
        self.assertIn("const transition = captureScriptBarTransition(sourceItem);", html_text)
        self.assertIn("animateScriptDrill(transition, bar);", html_text)
        self.assertIn("renderAudienceDashboardOverview(audienceMount, audienceBuckets, metricKey)", html_text)
        self.assertIn("tooltipLines: (bucket) => [`占比 ${formatNumber(bucket.metricPercentage ?? bucket.value)}%`]", html_text)
        self.assertIn("const axisTicks = Array.isArray(options.axisTicks) && options.axisTicks.length > 0", html_text)
        self.assertIn("axisTicks.forEach((value) => {", html_text)
        self.assertIn('const summary = Array.isArray(reportData.scripts?.labelSummary)', html_text)
        self.assertIn('onBucketClick: (bucket, item) => renderScriptDrill(', html_text)
        self.assertIn('renderPieChart(`${level1} 二级标签分布`', html_text)
        self.assertIn('button.className = "script-drill-back"', html_text)
        self.assertIn('document.body.append(clone);', html_text)
        self.assertIn('clone.classList.add("script-drill-moving-bar")', html_text)
        self.assertNotIn('clone.className = "script-drill-moving-bar"', html_text)
        self.assertIn('sourceItem?.querySelector(".column-bar, .script-drill-primary-bar")', html_text)
        self.assertIn('targetRect.left + targetRect.width / 2 - (sourceRect.left + sourceRect.width / 2)', html_text)
        self.assertIn('targetRect.bottom - sourceRect.bottom', html_text)
        self.assertIn('element("div", "script-dashboard-control")', html_text)
        self.assertIn('element("div", "script-drill-detail is-entering")', html_text)
        self.assertIn('detail.classList.add("is-visible")', html_text)
        self.assertIn('detail.classList.add("is-leaving")', html_text)
        self.assertIn('window.setTimeout(() => renderScriptDashboard({ level1, transition }), 260)', html_text)
        self.assertIn('function renderScriptDashboard(options = {})', html_text)
        self.assertIn('if (options.level1) {', html_text)
        self.assertNotIn('script-dashboard-return-target', html_text)
        self.assertNotIn('is-departing', html_text)
        self.assertIn('.script-drill-detail.is-leaving {\n      opacity: 0;', css_text)
        self.assertIn('chartGrid.classList.add("is-entering")', html_text)
        self.assertIn('window.setTimeout(() => chartGrid.classList.add("is-visible"), 0)', html_text)
        self.assertIn(".script-dashboard-hint {\n      color: var(--muted);\n      font-size: 16px;", css_text)
        self.assertIn("#script-dashboard .script-dashboard-control {\n      min-height: 44px;", css_text)
        self.assertIn("#script-dashboard .script-drill-detail,\n    #script-dashboard .script-dashboard-overview {\n      min-height: 300px;", css_text)
        self.assertIn("#script-dashboard .column-chart {\n      min-height: 250px;", css_text)
        self.assertIn("#script-dashboard .script-dashboard-overview .column-item {\n      height: 178px;", css_text)
        self.assertIn("#script-dashboard .script-drill-layout {\n      gap: 12px;", css_text)
        self.assertIn("#script-dashboard .script-drill-primary {\n      grid-template-rows: auto 178px auto;", css_text)
        self.assertIn("#script-dashboard .script-drill-primary > b {\n      font-size: 18px;", css_text)
        self.assertIn("#script-dashboard .pie-layout {\n      grid-template-columns: 200px minmax(0, .75fr);", css_text)
        self.assertIn("#script-dashboard .pie-chart {\n      width: 200px;", css_text)
        self.assertIn("#script-dashboard .script-drill-layout > .chart-card {\n      padding: 12px;", css_text)
        self.assertNotIn("scripts?.dashboard", html_text)
        self.assertNotIn("7.5 次", html_text)
        self.assertNotIn("2.5 次", html_text)
        self.assertNotIn("function renderBarChart", html_text)
        self.assertNotIn("bar-row", html_text)
        self.assertNotIn("bar-track", html_text)
        self.assertNotIn("bar-fill", html_text)

    def test_prompt_explains_material_ai_summary_complements_raw_fact_cards(self):
        prompt_text = (ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("04｜高光画面洞察", prompt_text)
        self.assertIn("05｜单素材深度分析", prompt_text)
        self.assertIn("06｜下一轮策略", prompt_text)
        self.assertIn('"schemaVersion": 5', prompt_text)
        self.assertIn("互动时序", prompt_text)
        self.assertIn("highlightDashboard", prompt_text)
        self.assertIn("portrait", prompt_text)
        self.assertIn("product", prompt_text)
        self.assertIn("other", prompt_text)
        self.assertIn("不得宣称帧对应具体话术", prompt_text)
        self.assertIn("互动时序分析", prompt_text)
        self.assertIn("脚本分析数据", prompt_text)
        self.assertIn("AI 二次总结", prompt_text)
        self.assertIn("不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句", prompt_text)
        self.assertIn("解释这些事实为什么影响素材放量、改写或保留判断", prompt_text)
        self.assertIn("少量关键事实锚点 + 多句分析判断", prompt_text)
        self.assertIn("每条先用少量事实锚定，再给出判断、边界和下一轮验证方向", prompt_text)
        self.assertIn("优先输出组合公式、机会判断、风险边界和下一轮验证方向", prompt_text)
        self.assertIn("每条 `batchInsights.conclusion` 必须为 180-280 个中文字符", prompt_text)
        self.assertIn(
            "每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 必须为 120-220 个中文字符",
            prompt_text,
        )
        self.assertIn("不得输出 `batchInsights.topic=integrated`", prompt_text)
        self.assertIn("必须输出至少 3 条 audience、2 条 script 洞察", prompt_text)
        self.assertIn("一二级内容公式：", prompt_text)
        self.assertIn("脚本文案：", prompt_text)
        self.assertIn("必须输出至少 4 条 nextActions", prompt_text)

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
        self.assertNotIn("报告状态", html_text)
        self.assertIn("01｜TOP 素材组合结论", html_text)
        self.assertIn("02｜人群洞察", html_text)
        self.assertIn("03｜脚本洞察", html_text)
        self.assertIn("04｜高光画面洞察", html_text)
        self.assertIn("05｜单素材深度分析", html_text)
        self.assertIn("06｜下一轮策略", html_text)
        self.assertNotIn("07｜数据质量与执行口径", html_text)
        self.assertNotIn('id="report-cover"', html_text)
        self.assertNotIn("renderWarnings", html_text)
        self.assertIn('id="next-actions"', html_text)
        self.assertIn("扩展礼赠脚本", html_text)
        self.assertIn("围绕送礼对象拆分。", html_text)
        self.assertIn("礼赠素材可以继续放量。", html_text)
        self.assertIn(
            '"framePath":"../peak-frames/m-1/first_peak_0001s_3.jpg"',
            html_text,
        )
        self.assertIn('<script type="application/json" id="report-data">', html_text)
        self.assertIn('"materials"', html_text)
        self.assertIn('"audience"', html_text)
        self.assertIn('"scripts"', html_text)
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
        self.assertIn('"pipelineSummary"', html_text)
        self.assertNotIn("selectedMaterials", html_text)
        self.assertNotIn("top_materials_rpa_ready", html_text)
        self.assertNotIn('"batchVerification":', html_text)
        self.assertNotIn("batchVerification includes execution process", html_text)

    def test_embedded_template_data_includes_material_raw_facts_without_video_payload(self):
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
            write_json(
                records_dir / "m-1.json",
                [
                    material_info_record("m-1"),
                    interaction_record("m-1", "CLICK_COUNT", 1, 9),
                    interaction_record("m-1", "LIKE_COUNT", 1, 2),
                    script_record("m-1"),
                ],
            )
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_9.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 9,
                            "framePath": frame_path.name,
                        }
                    ]
                },
            )

            _data_path, html_path, _data = generate_final_report(
                top_path,
                peak_dir,
                records_dir,
                output_dir,
            )

            html_text = html_path.read_text(encoding="utf-8")

        self.assertIn('"interactionTimeline"', html_text)
        self.assertIn('"label":"整体点击次数"', html_text)
        self.assertIn('"peak":{"second":1.0,"value":9.0}', html_text)
        self.assertIn('"scriptAnalysis"', html_text)
        self.assertIn('"formula"', html_text)
        self.assertIn('"早餐可以不碰碳水', html_text)
        self.assertIn(
            '"video":{"src":"https://video.example/m-1.mp4?token=signed",'
            '"poster":"https://image.example/m-1.jpg?token=signed",'
            '"durationSec":46.022}',
            html_text,
        )
        self.assertNotIn("data:video/", html_text)
        self.assertNotIn(";base64", html_text)

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
                            "title": "点击次数集中",
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
            "04｜高光画面洞察",
            "05｜单素材深度分析",
            "06｜下一轮策略",
        ):
            self.assertIn(heading, html_text)
        self.assertIn("function renderHighlightDashboard", html_text)
        self.assertIn("activateMaterialById(materialId)", html_text)
        self.assertNotIn("07｜数据质量与执行口径", html_text)
        self.assertIn('role="tablist"', html_text)
        self.assertIn('setAttribute("role", "tab")', html_text)
        self.assertIn('setAttribute("role", "tabpanel")', html_text)
        self.assertIn('"materialId":"m-1"', html_text)
        self.assertIn('"materialId":"m-2"', html_text)
        self.assertIn("互动时序分析", html_text)
        self.assertIn("脚本分析数据", html_text)
        self.assertIn('"interactionTimeline"', html_text)
        self.assertIn('"scriptAnalysis"', html_text)
        self.assertIn("人群分析", html_text)
        self.assertIn("脚本分析", html_text)
        self.assertIn("高光帧观察", html_text)
        self.assertIn("综合判断与下一步", html_text)
        self.assertIn("高光帧观察", html_text)
        self.assertIn("互动时序分析", html_text)
        self.assertIn("人群分析", html_text)
        self.assertIn("脚本分析", html_text)
        self.assertIn("综合判断与下一步", html_text)
        self.assertIn('element("div", "material-workspace-tabs")', html_text)
        self.assertIn('data-workspace-tab', html_text)
        self.assertIn('container.querySelectorAll("#material-tab-list [role=\'tab\']")', html_text)
        self.assertIn('container.querySelectorAll(".material-panels > [role=\'tabpanel\']")', html_text)
        self.assertIn('tabs.querySelectorAll("[data-workspace-tab]")', html_text)
        self.assertIn('".material-workspace-panel:not([hidden]) [data-timeline-ready=\'false\']"', html_text)
        self.assertIn('素材 ID：${text(material.materialId)}', html_text)
        self.assertIn('全选高光帧', html_text)
        self.assertIn('全不选高光帧', html_text)
        self.assertIn('"互动时序分析数据"', html_text)
        self.assertIn('displayMaterialName(material.materialName, materialId)', html_text)
        self.assertIn('"materialAudience"', html_text)
        self.assertIn('function renderMaterialAudienceDashboard(material)', html_text)
        self.assertIn('function materialInteractionSummary(timeline)', html_text)
        self.assertIn('highlight-frame-choice', html_text)
        self.assertIn('input.type = "checkbox";', html_text)
        self.assertIn('`${formatNumber(frame.durationSec)}s · 点击 ${formatNumber(frame.metricValue)}`', html_text)
        self.assertNotIn("data:video/", html_text)
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
                            "title": "点击次数集中",
                            "conclusion": "点击层 24-30 岁突出。",
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

        self.assertNotIn("数据口径证据", html_text)
        for label in ("组合总述", "高光帧结论"):
            self.assertNotIn(label, html_text)
        self.assertNotIn("第一部分：", html_text)
        self.assertNotIn("第二部分：", html_text)
        self.assertIn('class="material-tabs side-tabs"', html_text)
        self.assertIn(".side-tabs {", html_text)
        self.assertIn(".insight-layout {", html_text)
        self.assertIn(".primary-summary {", html_text)
        self.assertIn(".primary-summary > p {", html_text)
        self.assertIn("font-size: 18px;", html_text)
        self.assertIn("font-weight: 800;", html_text)
        self.assertNotIn("renderTopicInsight", html_text)
        self.assertNotIn('id="integrated-insights"', html_text)
        self.assertIn('id="audience-primary"', html_text)
        self.assertIn('id="script-primary"', html_text)
        self.assertNotIn('id="highlight-insights"', html_text)
        self.assertNotIn("renderEvidenceCards", html_text)
        self.assertNotIn("renderEvidenceSummary", html_text)
        self.assertNotIn("humanizeEvidenceRefs", html_text)
        self.assertIn("metricValue", html_text)
        self.assertIn("metricPercentage", html_text)
        self.assertIn("occurrenceCount", html_text)
        self.assertIn("durationSec", html_text)
        self.assertIn("展现次数", html_text)
        self.assertIn("点击次数", html_text)
        self.assertNotIn("曝光人群", html_text)
        self.assertNotIn("点击人群", html_text)
        self.assertIn("insights.forEach((insight, index) => {", html_text)
        self.assertIn('elementWithMaterialLinks("p", "", insight.conclusion)', html_text)
        self.assertIn('elementWithMaterialLinks("p", "", paragraph)', html_text)
        self.assertIn('`素材 ID：${text(material.materialId)}`', html_text)
        self.assertNotIn('状态 ${displayStatus(material.extractionStatus)}', html_text)
        self.assertNotIn("`状态 ${text(material.extractionStatus)}`", html_text)
        self.assertNotIn('class="score"', html_text)
        self.assertNotIn(".score {", html_text)
        self.assertNotIn('element("span", "", "建议")', html_text)
        self.assertNotIn("text(analysis.decision, \"unknown\")", html_text)
        self.assertIn("`${formatNumber(frame.durationSec)}s · 点击 ${formatNumber(frame.metricValue)}`", html_text)
        self.assertNotIn("${text(frame.rankType)} ·", html_text)
        self.assertIn('element("p", "highlight-note")', html_text)
        self.assertIn('element("b", "", `${timingLabel}：`)', html_text)
        self.assertIn(".highlight-note b {", html_text)
        self.assertNotIn("humanizeEvidenceRef(item.frameRef)", html_text)
        self.assertIn('element("p", "eyebrow", `#${text(material.rank)}`)', html_text)
        self.assertIn("renderAudienceDashboard();", html_text)
        self.assertIn("renderScriptDashboard();", html_text)
        self.assertIn("renderHighlightDashboard();", html_text)
        self.assertIn("renderNextActions();", html_text)
        self.assertIn("function renderNextActions", html_text)
        self.assertIn('id="next-actions"', html_text)
        self.assertIn('elementWithMaterialLinks("b", "", action.title)', html_text)
        self.assertIn('elementWithMaterialLinks("p", "strategy-action", action.action || action.body)', html_text)
        self.assertIn('elementWithMaterialLinks("p", "strategy-validation", action.validation)', html_text)
        self.assertNotIn('element("small", "", text(action.priority, "medium"))', html_text)
        self.assertIn("内容公式一级标签分布", html_text)
        self.assertNotIn("脚本标签分组", html_text)
        self.assertNotIn("高频脚本标签", html_text)
        self.assertIn("本视频内容公式", html_text)
        self.assertIn("复制内容公式", html_text)
        self.assertIn("本视频脚本", html_text)
        self.assertIn("复制脚本", html_text)
        self.assertNotIn("脚本文案", html_text)
        self.assertNotIn("高光帧类型分布", html_text)
        self.assertNotIn("highlight-all-grid", html_text)
        self.assertNotIn("script-summary-grid", html_text)
        self.assertIn('class="hero report-cover"', html_text)
        self.assertNotIn("cover-title", html_text)
        self.assertNotIn("cover-empty", html_text)
        self.assertNotIn("report-warnings", html_text)
        self.assertIn("grid-template-columns: 190px minmax(0, 1fr);", html_text)
        self.assertIn("grid-template-columns: var(--video-width) minmax(0, 1fr);", html_text)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", html_text)
        self.assertIn("--visual-height: 560px;", html_text)
        self.assertIn("flex-direction: column;", html_text)
        self.assertIn("overflow-y: auto;", html_text)
        self.assertIn("align-items: stretch;", html_text)
        self.assertIn("object-fit: contain;", html_text)
        self.assertIn(".material-body {", html_text)
        self.assertIn("grid-template-columns: 1fr;", html_text)


class FinalReportCliTests(unittest.TestCase):
    def test_cli_copies_assets_without_embedding_images(self):
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
                    "actualEndDate": "2026-06-02",
                    "materials": [material("m-1")],
                },
            )
            write_json(records_dir / "m-1.json", [click_record("m-1", 1, 9)])
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            frame_path = material_dir / "first_peak_0001s_9.jpg"
            frame_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 9,
                            "framePath": frame_path.name,
                        }
                    ],
                    "warnings": [],
                },
            )

            result = render_final_report.main(
                [
                    "--top-materials-json",
                    str(top_path),
                    "--peak-output-dir",
                    str(peak_dir),
                    "--diagnosis-records-dir",
                    str(records_dir),
                    "--copy-assets",
                    "--output-dir",
                    str(output_dir),
                ]
            )

            html_text = (output_dir / "final-report.html").read_text(encoding="utf-8")
            copied_frame_exists = (
                output_dir
                / "assets"
                / "highlight-frames"
                / "m-1"
                / "first_peak_0001s_9.jpg"
            ).is_file()

        self.assertEqual(result, 0)
        self.assertTrue(copied_frame_exists)
        self.assertIn("assets/highlight-frames/m-1/first_peak_0001s_9.jpg", html_text)
        self.assertNotIn("data:image/", html_text)

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
