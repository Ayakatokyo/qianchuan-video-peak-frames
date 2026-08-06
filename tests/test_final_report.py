import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


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
from report_renderer import create_report_archive  # noqa: E402


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
    def test_template_renders_frame_coverage_note_inside_material_panel(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("function renderFrameCoverageNote", template)
        self.assertIn("renderFrameCoverageNote(analysis.frameCoverageNote)", template)
        self.assertIn("高光帧说明", template)


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


class FinalReportQualityGateTests(unittest.TestCase):
    def test_standard_quality_gate_rejects_short_unanchored_v2_analysis(self):
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
        for field_name in (
            "overallConclusion",
            "batchInsights",
            "evidenceRefs",
            "visualObservation",
            "materialAnalyses",
        ):
            self.assertIn(field_name, message)

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
        self.assertIn("repeated", message)
        for field_name in (
            "audienceConclusion",
            "scriptConclusion",
            "integratedConclusion",
            "nextAction",
        ):
            self.assertIn(field_name, message)


class FinalReportRenderTests(unittest.TestCase):
    def test_template_keeps_current_structure_with_livecheck_visual_style(self):
        template_path = ROOT / "templates" / "final-report-template.html"
        html_text = template_path.read_text(encoding="utf-8")

        self.assertIn('class="hero-copy"', html_text)
        self.assertIn('class="insight-layout"', html_text)
        self.assertIn('class="primary-summary"', html_text)
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
        self.assertIn(".insight-secondary {", html_text)
        self.assertIn("width: 100%;", html_text)
        self.assertIn("grid-template-columns: 1fr;", html_text)
        self.assertIn("grid-template-columns: 190px minmax(0, 1fr);", html_text)
        self.assertIn("统计周期 ${text(period.actualStartDate)} - ${text(period.actualEndDate)}。", html_text)
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
        self.assertIn('id="script-dashboard"', html_text)
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
        self.assertIn(
            'renderInsightList("script-primary", reportData.batchInsights?.script, "暂无脚本洞察");',
            html_text,
        )
        self.assertNotIn("function renderPrimaryInsight", html_text)
        self.assertNotIn('id="highlight-dashboard"', html_text)
        self.assertIn("function renderAudienceDashboard", html_text)
        self.assertIn("function renderScriptDashboard", html_text)
        self.assertIn("function createCollapseToggle", html_text)
        self.assertIn("collapsible-toggle", html_text)
        self.assertIn("collapsible-toggle-action", html_text)
        self.assertIn("collapsible-body-title", html_text)
        self.assertNotIn("collapsible-toggle-label", html_text)
        self.assertIn("width: 100%;", html_text)
        self.assertIn("justify-content: center;", html_text)
        self.assertIn("font-size: 16px;", html_text)
        self.assertIn("collapsible-body", html_text)
        self.assertIn(".collapsible-body[hidden] {", html_text)
        self.assertIn("display: none;", html_text)
        self.assertIn('button.setAttribute("aria-expanded", String(expanded));', html_text)
        self.assertIn('action.textContent = expanded ? "收起数据" : "展开数据";', html_text)
        self.assertIn("target.hidden = !expanded;", html_text)
        self.assertIn('target.prepend(element("b", "collapsible-body-title", title));', html_text)
        self.assertIn("收起数据", html_text)
        self.assertIn("展开数据", html_text)
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
        self.assertIn("height: 320px;", html_text)
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
        self.assertIn("stroke: var(--gold);", html_text)
        self.assertIn("fill: #9b6a37;", html_text)
        self.assertIn("accent-color: #9b6a37;", html_text)
        self.assertNotIn("#2f66ff", html_text)
        self.assertIn("function hydrateMaterialPanel", html_text)
        self.assertIn("function renderVisualStrip", html_text)
        self.assertIn('element("div", "visual-strip")', html_text)
        self.assertIn('element("div", "video-panel")', html_text)
        self.assertIn("--visual-height: 560px;", html_text)
        self.assertIn("--video-width: calc(var(--visual-height) * 9 / 16);", html_text)
        self.assertIn("grid-template-columns: var(--video-width) minmax(0, 1fr);", html_text)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", html_text)
        self.assertIn("height: var(--visual-height);", html_text)
        self.assertIn("width: var(--video-width);", html_text)
        self.assertIn("justify-self: start;", html_text)
        self.assertIn("aspect-ratio: 9 / 16;", html_text)
        self.assertIn("grid-template-rows: repeat(2, minmax(0, 1fr));", html_text)
        self.assertIn("grid-template-rows: minmax(0, 1fr) auto;", html_text)
        self.assertIn("min-height: 0;", html_text)
        self.assertNotIn("height: 175px;", html_text)
        self.assertNotIn("min-height: 430px;", html_text)
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
        self.assertIn("font-size: 14px;", html_text)
        self.assertIn("line-height: 1.55;", html_text)
        self.assertIn("overflow: visible;", html_text)
        self.assertNotIn("min-width: 138px;", html_text)
        self.assertNotIn("max-width: 240px;", html_text)
        self.assertNotIn("grid-template-columns: auto minmax(0, 1fr);", html_text)
        self.assertNotIn("max-height: 120px;", html_text)
        self.assertNotIn("function renderFormulaButtons", html_text)
        self.assertNotIn('button.className = "chip formula-chip"', html_text)
        self.assertNotIn('card.append(element("p", "muted", "证据句"), evidenceList)', html_text)
        self.assertIn("document.createElement(\"video\")", html_text)
        self.assertIn('video.preload = "none"', html_text)
        self.assertIn('image.loading = "lazy"', html_text)
        self.assertIn('image.decoding = "async"', html_text)
        self.assertNotIn("const visualNodes = [renderFrames(material)];", html_text)
        self.assertNotIn("panel.append(head, ...visualNodes, body);", html_text)
        self.assertIn(
            "body.append(\n"
            "    renderHighlightFormula(analysis.highlightAnalyses),\n"
            "    renderInteractionTimeline(material),",
            html_text,
        )
        self.assertIn(
            "body.append(\n"
            "    renderHighlightFormula(analysis.highlightAnalyses),\n"
            "    renderInteractionTimeline(material),\n"
            "    renderFormula(\"人群与脚本证据\", [analysis.audienceConclusion, analysis.scriptConclusion]),\n"
            "    renderScriptAnalysis(material),",
            html_text,
        )
        self.assertNotIn("function renderHighlightDashboard", html_text)
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
        self.assertIn(".chart-tooltip-line {", html_text)
        self.assertIn("display: block;", html_text)
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
        self.assertIn("width: 320px;", html_text)
        self.assertNotIn(".pie-chart {\n      position: relative;\n      width: 320px;\n      aspect-ratio: 1;\n      border-radius: 50%;\n      background: conic-gradient(var(--gold) 0deg, var(--gold) 360deg);\n      box-shadow: 0 0 0 1px rgba(82, 45, 24, .1);\n      overflow: hidden;", html_text)
        self.assertNotIn("inset 0 0 0 58px", html_text)
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
        self.assertIn("bar.style.width = itemCount > 24 ? \"100%\" : \"min(64px, 72%)\";", html_text)
        self.assertNotIn("column-chart-scroll", html_text)
        self.assertIn("tooltipLines: (bucket) => [", html_text)
        self.assertIn("`占比 ${formatNumber(bucket.metricPercentage ?? 0)}%`", html_text)
        self.assertIn("`人数 ${formatNumber(bucket.metricValue ?? bucket.value ?? 0)}`", html_text)
        self.assertIn('const regionCard = element("div", "chart-card chart-card-full chart-card-plain");', html_text)
        self.assertIn(".chart-card-plain {", html_text)
        self.assertIn("border-color: transparent;", html_text)
        self.assertIn("padding-left: 10px;", html_text)
        self.assertIn("padding-right: 10px;", html_text)
        self.assertIn('const card = element("div", `chart-card chart-card-full${options.cardClass ? ` ${options.cardClass}` : ""}`);', html_text)
        self.assertIn('cardClass: "chart-card-plain"', html_text)
        self.assertIn("renderColumnChart(\"八大人群分布\", topBuckets(dimensions.user_group_label_name), null, {", html_text)
        self.assertIn("tooltipLines: (bucket) => [`占比 ${formatNumber(bucket.metricPercentage ?? bucket.value)}%`]", html_text)
        self.assertIn("const axisTicks = Array.isArray(options.axisTicks) && options.axisTicks.length > 0", html_text)
        self.assertIn("axisTicks.forEach((value) => {", html_text)
        self.assertIn("const totalOccurrences = level1Buckets.reduce((sum, bucket) => sum + Number(bucket.value || 0), 0) || 1;", html_text)
        self.assertIn("axisTicks: [maxCount, Math.round(maxCount * 0.75), Math.round(maxCount * 0.5), Math.round(maxCount * 0.25), 0]", html_text)
        self.assertIn("axisValueFormatter: (value) => `${Math.round(value)} 次`", html_text)
        self.assertIn("tooltipLines: (bucket) => [", html_text)
        self.assertIn("`占比 ${formatNumber((Number(bucket.value || 0) / totalOccurrences) * 100)}%`", html_text)
        self.assertIn("`次数 ${formatNumber(bucket.value || 0)} 次`", html_text)
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

        self.assertIn("04｜单素材深度分析", prompt_text)
        self.assertIn("互动时序分析", prompt_text)
        self.assertIn("脚本分析数据", prompt_text)
        self.assertIn("AI 二次总结", prompt_text)
        self.assertIn("不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句", prompt_text)
        self.assertIn("解释这些事实为什么影响素材放量、改写或保留判断", prompt_text)
        self.assertIn("少量关键事实锚点 + 多句分析判断", prompt_text)
        self.assertIn("整体结论的数据陈述只作为判断依据，不作为段落主体", prompt_text)
        self.assertIn("优先输出组合公式、机会判断、风险边界和下一轮验证方向", prompt_text)
        self.assertIn("每条 `batchInsights.conclusion` 必须为 180-280 个中文字符", prompt_text)
        self.assertIn(
            "每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 必须为 120-220 个中文字符",
            prompt_text,
        )
        self.assertIn("不得输出 `batchInsights.topic=integrated`", prompt_text)
        self.assertIn("必须输出至少 3 条 audience、2 条 script 洞察", prompt_text)
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
        self.assertNotIn("04｜高光帧洞察", html_text)
        self.assertIn("04｜单素材深度分析", html_text)
        self.assertIn("05｜下一轮策略", html_text)
        self.assertNotIn("06｜下一轮策略", html_text)
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
            "04｜单素材深度分析",
            "05｜下一轮策略",
        ):
            self.assertIn(heading, html_text)
        self.assertNotIn("04｜高光帧洞察", html_text)
        self.assertNotIn("06｜下一轮策略", html_text)
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
        self.assertIn("人群与脚本证据", html_text)
        self.assertIn("高光帧观察", html_text)
        self.assertIn("综合判断与下一步", html_text)
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
        for label in ("组合总述", "人群结论", "脚本结论", "高光帧结论"):
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
        self.assertIn("displayStatus(material.extractionStatus)", html_text)
        self.assertIn('succeeded: "已完成"', html_text)
        self.assertNotIn("`状态 ${text(material.extractionStatus)}`", html_text)
        self.assertNotIn('class="score"', html_text)
        self.assertNotIn(".score {", html_text)
        self.assertNotIn('element("span", "", "建议")', html_text)
        self.assertNotIn("text(analysis.decision, \"unknown\")", html_text)
        self.assertIn("`${formatNumber(frame.durationSec)}s · 点击 ${formatNumber(frame.metricValue)}`", html_text)
        self.assertNotIn("${text(frame.rankType)} ·", html_text)
        self.assertIn('element("p", "highlight-note")', html_text)
        self.assertIn('element("b", "", `${index + 1}：`)', html_text)
        self.assertIn(".highlight-note b {", html_text)
        self.assertNotIn("humanizeEvidenceRef(item.frameRef)", html_text)
        self.assertIn('element("p", "eyebrow", `#${text(material.rank)}`)', html_text)
        self.assertIn("renderAudienceDashboard();", html_text)
        self.assertIn("renderScriptDashboard();", html_text)
        self.assertNotIn("renderHighlightDashboard();", html_text)
        self.assertIn("renderNextActions();", html_text)
        self.assertIn("function renderNextActions", html_text)
        self.assertIn('id="next-actions"', html_text)
        self.assertIn('elementWithMaterialLinks("b", "", action.title)', html_text)
        self.assertIn('elementWithMaterialLinks("p", "", action.body)', html_text)
        self.assertNotIn('element("small", "", text(action.priority, "medium"))', html_text)
        self.assertIn("内容公式一级标签分布", html_text)
        self.assertNotIn("高频脚本标签", html_text)
        self.assertIn("本视频内容公式", html_text)
        self.assertIn("复制内容公式", html_text)
        self.assertIn("本视频脚本", html_text)
        self.assertIn("复制脚本", html_text)
        self.assertNotIn("脚本文案", html_text)
        self.assertNotIn("高光帧类型分布", html_text)
        self.assertNotIn("highlight-all-grid", html_text)
        self.assertNotIn("script-summary-grid", html_text)
        self.assertNotIn("report-cover", html_text)
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
