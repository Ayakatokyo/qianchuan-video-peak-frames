import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analysis_units_lib import (  # noqa: E402
    build_analysis_units,
    publish_analysis_units,
)


def example_analysis():
    return {
        "schemaVersion": 1,
        "period": {
            "actualStartDate": "2026-08-10",
            "actualEndDate": "2026-08-20",
        },
        "materials": [
            {
                "rank": 1,
                "materialId": "m-1",
                "materialName": "素材一",
                "materialType": "视频",
                "videoType": "自选投放视频",
                "totalCost": 100.0,
                "video": {"src": "https://signed.example/video.mp4"},
            }
        ],
        "audience": {
            "metrics": {
                "SHOW_COUNT": {
                    "missingMaterialIds": [],
                    "dimensions": {
                        "user_group_label_name": {
                            "dimensionKey": "user_group_label_name",
                            "dimensionName": "八大人群",
                            "totalMetricValue": 100.0,
                            "sourceMaterialIds": ["m-1"],
                            "buckets": [
                                {
                                    "bucket": "Z世代",
                                    "metricValue": 100.0,
                                    "metricPercentage": 100.0,
                                    "sourceMaterialIds": ["m-1"],
                                }
                            ],
                        }
                    },
                }
            },
            "preferences": [
                {
                    "audienceGroup": "Z世代",
                    "topMaterials": [
                        {
                            "materialId": "m-1",
                            "audiencePercentage": 100.0,
                            "preferenceLift": 1.5,
                        }
                    ],
                }
            ],
            "preferencesByMetric": {
                "SHOW_COUNT": [
                    {
                        "audienceGroup": "Z世代",
                        "topMaterials": [
                            {
                                "materialId": "m-1",
                                "audiencePercentage": 100.0,
                                "preferenceLift": 1.5,
                            }
                        ],
                    }
                ]
            },
        },
        "scripts": {
            "materials": [
                {
                    "materialId": "m-1",
                    "materialUri": "temporary-uri",
                    "text": "完整脚本文案",
                    "labels": [{"level1": "商品卖点", "level2": "适用场景"}],
                    "evidence": [
                        {"level2": "适用场景", "sentence": "早餐场景口播"}
                    ],
                }
            ],
            "labelSummary": [],
        },
        "interaction": {
            "available": True,
            "metrics": {
                "CLICK_COUNT": {
                    "available": True,
                    "sampleMaterialCount": 1,
                    "totalMetricValue": 5.0,
                    "buckets": [
                        {"label": "0-10%", "share": 1.0, "materialCount": 1}
                    ],
                }
            },
        },
        "delivery": {
            "materials": [
                {
                    "materialId": "m-1",
                    "facts": [
                        {"statDate": "2026-08-20", "values": {"totalCost": 100.0}}
                    ],
                }
            ]
        },
        "commercial": {
            "available": True,
            "totals": {"cost": "100"},
            "metrics": [
                {
                    "evidenceRef": "commercial:netSettlementRoi",
                    "key": "netSettlementRoi",
                    "label": "净成交 ROI",
                    "value": "1.2",
                    "available": True,
                }
            ],
        },
        "highlights": {
            "materials": [
                {
                    "materialId": "m-1",
                    "frames": [
                        {
                            "materialId": "m-1",
                            "rankType": "zero_second",
                            "durationSec": 0.0,
                            "metricValue": 3.0,
                            "framePath": "/tmp/m-1/zero.jpg",
                            "evidenceRef": "highlight:m-1:zero_second",
                        }
                    ],
                    "missingFrames": [],
                    "frameCoverage": {"succeededFrameCount": 1},
                }
            ]
        },
        "warnings": [],
    }


class AnalysisUnitProjectionTests(unittest.TestCase):
    def test_projects_normalized_facts_into_isolated_chapter_inputs(self):
        units = build_analysis_units(example_analysis())

        self.assertEqual(
            list(units),
            [
                "chapter01-portfolio",
                "chapter02-audience",
                "chapter03-script",
                "chapter04-highlight",
                "chapter05-material",
            ],
        )
        self.assertNotIn("delivery", units["chapter01-portfolio"])
        self.assertNotIn("audience", units["chapter04-highlight"])
        self.assertNotIn(
            "sourceMaterialIds",
            json.dumps(units["chapter02-audience"], ensure_ascii=False),
        )
        self.assertNotIn("preferences", units["chapter02-audience"])
        audience_bucket = units["chapter02-audience"]["metrics"]["SHOW_COUNT"][
            "dimensions"
        ]["user_group_label_name"]["buckets"][0]
        self.assertEqual(
            audience_bucket["evidenceRef"],
            "audience:SHOW_COUNT:user_group_label_name:Z世代",
        )
        self.assertNotIn(
            "https://signed.example",
            json.dumps(units, ensure_ascii=False),
        )

        script = units["chapter03-script"]["materials"][0]
        self.assertNotIn("materialUri", script)
        self.assertEqual(
            script["labels"][0]["evidenceRef"],
            "script:m-1:商品卖点:适用场景",
        )

        material = units["chapter05-material"]["materials"][0]
        self.assertEqual(
            material["highlightRefs"], ["highlight:m-1:zero_second"]
        )
        self.assertEqual(
            material["scriptEvidenceRefs"],
            ["script:m-1:商品卖点:适用场景"],
        )
        self.assertNotIn("deliveryFacts", material)
        self.assertNotIn("delivery", json.dumps(units, ensure_ascii=False))

    def test_chapter01_contains_only_material_list_facts(self):
        analysis = example_analysis()
        analysis["portfolio"] = {
            "newMaterialCount7d": 3,
            "newMaterialStartDate": "2026-08-21",
            "newMaterialEndDate": "2026-08-27",
            "metrics": {"cost": "100", "ctr": "2.5"},
        }
        units = build_analysis_units(analysis)
        chapter01 = units["chapter01-portfolio"]
        self.assertEqual(chapter01["portfolio"], analysis["portfolio"])
        self.assertNotIn("materials", chapter01)
        self.assertNotIn("materialIds", chapter01)
        for key in ("audience", "scripts", "interaction", "highlights", "delivery"):
            self.assertNotIn(key, chapter01)


class AnalysisUnitPublicationTests(unittest.TestCase):
    def test_publishes_compact_units_and_verified_manifest_last(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path, manifest = publish_analysis_units(
                temp_dir, example_analysis()
            )

            self.assertEqual(
                manifest_path,
                Path(temp_dir) / "analysis-units" / "analysis-unit-manifest.json",
            )
            self.assertNotIn("compatibilitySnapshotPath", manifest)
            self.assertEqual(manifest["schemaVersion"], 2)
            self.assertEqual(manifest["sourceFactsSchemaVersion"], 1)
            self.assertNotIn("sourceSnapshotSchemaVersion", manifest)
            self.assertEqual(manifest["executionMode"], "single_model_call")
            self.assertEqual(manifest["analysisSchemaVersion"], 5)
            self.assertEqual(manifest["materialIds"], ["m-1"])
            self.assertEqual(manifest["frames"]["successfulCount"], 1)
            self.assertEqual(manifest["frames"]["missingCount"], 0)
            self.assertEqual(len(manifest["units"]), 5)

            for item in manifest["units"]:
                unit_path = manifest_path.parent / item["path"]
                raw = unit_path.read_bytes()
                self.assertEqual(item["byteSize"], len(raw))
                self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(
                    json.loads(raw.decode("utf-8"))["unitId"], item["unitId"]
                )

            self.assertEqual(
                json.loads(manifest_path.read_text(encoding="utf-8")), manifest
            )

    def test_does_not_publish_manifest_when_a_unit_is_not_serializable(self):
        analysis = example_analysis()
        analysis["materials"][0]["materialName"] = {"not-json-serializable"}
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(TypeError):
                publish_analysis_units(temp_dir, analysis)

            self.assertFalse(
                (
                    Path(temp_dir)
                    / "analysis-units"
                    / "analysis-unit-manifest.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
