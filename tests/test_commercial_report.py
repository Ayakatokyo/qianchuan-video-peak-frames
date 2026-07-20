import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from commercial_report_lib import (  # noqa: E402
    aggregate_crowd_analysis,
    generate_report,
)


def crowd_record(material_id, dimension_key, bucket, metric_value):
    return {
        "recordType": "crowd_analysis",
        "displayMetric": "SHOW_COUNT",
        "materialId": material_id,
        "dimensionKey": dimension_key,
        "dimensionName": {"age": "年龄"}.get(dimension_key, "人群"),
        "dimensionValue": bucket,
        "metricValue": metric_value,
        "metricPercentage": 999,
    }


class CrowdAggregationTests(unittest.TestCase):
    def test_aggregates_show_count_crowd_records_by_dimension_and_bucket(self):
        records_by_material = {
            "764": [
                crowd_record("764", "age", "18-23", 100),
                crowd_record("764", "age", "24-30", 300),
                crowd_record("764", "interest", "滋补养生", 80),
                {
                    **crowd_record("764", "age", "ignored", 999),
                    "displayMetric": "CLICK_COUNT",
                },
            ],
            "765": [
                crowd_record("765", "age", "18-23", 200),
                crowd_record("765", "interest", "滋补养生", 120),
            ],
        }

        result = aggregate_crowd_analysis(records_by_material, ["764", "765"])

        age = result["dimensions"]["age"]
        self.assertEqual(age["dimensionName"], "年龄")
        self.assertEqual(age["totalMetricValue"], 600.0)
        self.assertEqual(
            [(bucket["bucket"], bucket["metricValue"]) for bucket in age["buckets"]],
            [("24-30", 300.0), ("18-23", 300.0)],
        )
        self.assertAlmostEqual(age["buckets"][0]["metricPercentage"], 50.0)
        self.assertAlmostEqual(age["buckets"][1]["metricPercentage"], 50.0)

        interest = result["dimensions"]["interest"]
        self.assertEqual(interest["totalMetricValue"], 200.0)
        self.assertEqual(interest["buckets"][0]["bucket"], "滋补养生")
        self.assertAlmostEqual(interest["buckets"][0]["metricPercentage"], 100.0)
        self.assertEqual(result["missingCrowdMaterialIds"], [])

    def test_extracts_bucket_from_nested_dimensions_and_records_missing_materials(self):
        records_by_material = {
            "764": [
                {
                    "recordType": "crowd_analysis",
                    "displayMetric": "SHOW_COUNT",
                    "materialId": "764",
                    "dimensionKey": "age",
                    "dimensionName": "年龄",
                    "Dimensions": {"age": {"ValueStr": "31-40"}},
                    "metricValue": "42.5",
                }
            ],
            "765": [],
        }

        result = aggregate_crowd_analysis(records_by_material, ["764", "765", "766"])

        age = result["dimensions"]["age"]
        self.assertEqual(age["buckets"][0]["bucket"], "31-40")
        self.assertEqual(age["buckets"][0]["metricValue"], 42.5)
        self.assertEqual(result["missingCrowdMaterialIds"], ["765", "766"])


class CommercialReportGenerationTests(unittest.TestCase):
    def test_generates_analysis_json_and_static_html_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            peak_output = root / "peak-output"
            diagnosis_records = root / "diagnosis-records"
            output_json = root / "analysis.json"
            output_html = root / "report.html"

            top_materials.write_text(
                json.dumps(
                    {
                        "actualStartDate": "2026-05-01",
                        "actualEndDate": "2026-05-10",
                        "rankingMetric": "totalCost",
                        "materials": [
                            {
                                "rank": 1,
                                "materialId": "764",
                                "materialName": "章子怡 <明星素材>.mp4",
                                "materialType": "短视频",
                                "videoType": "混剪",
                                "totalCost": 900.25,
                            },
                            {
                                "rank": 2,
                                "materialId": "765",
                                "materialName": "机制种草.mp4",
                                "materialType": "短视频",
                                "videoType": "口播",
                                "totalCost": 99.75,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            diagnosis_records.mkdir()
            (diagnosis_records / "764.json").write_text(
                json.dumps(
                    [
                        crowd_record("764", "age", "18-23", 100),
                        crowd_record("764", "age", "24-30", 300),
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (diagnosis_records / "765.json").write_text(
                json.dumps(
                    [crowd_record("765", "age", "18-23", 200)],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            material_dir = peak_output / "764"
            material_dir.mkdir(parents=True)
            (material_dir / "first_peak_0001s_5.jpg").write_bytes(b"\xff\xd8x\xff\xd9")
            (material_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "materialId": "764",
                        "frames": [
                            {
                                "rankType": "first_peak",
                                "durationSec": 1.0,
                                "metricValue": 5.0,
                                "framePath": "first_peak_0001s_5.jpg",
                            }
                        ],
                        "warnings": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            analysis = generate_report(
                top_materials,
                peak_output,
                diagnosis_records,
                output_json,
                output_html,
            )

            self.assertTrue(output_json.is_file())
            self.assertTrue(output_html.is_file())
            saved_analysis = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(saved_analysis, analysis)
            self.assertEqual(saved_analysis["period"]["actualStartDate"], "2026-05-01")
            self.assertEqual(saved_analysis["overview"]["materialCount"], 2)
            self.assertEqual(saved_analysis["overview"]["totalCost"], 1000.0)
            age = saved_analysis["audience"]["dimensions"]["age"]
            self.assertEqual(age["totalMetricValue"], 600.0)
            self.assertEqual(age["buckets"][0]["bucket"], "24-30")
            self.assertAlmostEqual(age["buckets"][0]["metricPercentage"], 50.0)
            self.assertEqual(saved_analysis["materials"][0]["highlightFrameCount"], 1)

            html = output_html.read_text(encoding="utf-8")
            self.assertIn("千川素材商业分析报告", html)
            self.assertIn("素材级曝光人群结构", html)
            self.assertIn("章子怡 &lt;明星素材&gt;.mp4", html)
            self.assertIn("素材级相关性", html)
            self.assertNotIn("章子怡 <明星素材>.mp4", html)


if __name__ == "__main__":
    unittest.main()
