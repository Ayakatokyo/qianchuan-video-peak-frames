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
    build_display_facts,
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
        self.assertNotIn("preferencesByMetric", units["chapter02-audience"])
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
            material["highlight"]["frames"][0]["evidenceRef"],
            "highlight:m-1:zero_second",
        )
        self.assertEqual(
            material["evidenceIndex"]["script"],
            ["script:m-1:商品卖点:适用场景"],
        )
        self.assertNotIn("deliveryFacts", material)
        self.assertNotIn("delivery", json.dumps(units, ensure_ascii=False))
        self.assertNotIn("audiencePreferences", units["chapter05-material"]["materials"][0])

        self.assertIn("audience", material)
        self.assertIn("script", material)
        self.assertIn("highlight", material)
        self.assertIn("interactionTimeline", material)
        self.assertIn("evidenceIndex", material)
        self.assertEqual(material["highlight"]["frames"][0]["framePath"], "/tmp/m-1/zero.jpg")
        self.assertEqual(material["script"]["text"], "完整脚本文案")
        self.assertEqual(
            material["script"]["formula"],
            [{"level1": "商品卖点", "level2": "适用场景"}],
        )
        self.assertEqual(material["interactionTimeline"]["metrics"][0]["metric"], "CLICK_COUNT")

    def test_display_facts_keep_all_fixed_report_data(self):
        display = build_display_facts(example_analysis())
        self.assertEqual(
            set(display),
            {
                "schemaVersion", "sourceFactsSchemaVersion", "period", "portfolio",
                "materials", "audience", "scripts", "interaction", "delivery",
                "highlights", "commercial",
            },
        )
        self.assertIn("preferencesByMetric", display["audience"])
        self.assertIn("metrics", display["audience"])
        self.assertEqual(set(display["scripts"]), {"labelSummary", "keywordCloud"})

    def test_chapter03_builds_deterministic_keyword_cloud_from_script_text(self):
        analysis = example_analysis()
        analysis["materials"].append({"materialId": "m-2", "materialName": "标题词不应进入"})
        analysis["scripts"]["materials"].append({
            "materialId": "m-2", "text": "早餐燕窝，早餐营养", "labels": [], "evidence": []
        })
        cloud = build_analysis_units(analysis)["chapter03-script"]["keywordCloud"]
        self.assertEqual(cloud["source"], "facts.scripts.materials[*].text")
        breakfast = next(item for item in cloud["keywords"] if item["keyword"] == "早餐")
        self.assertEqual(breakfast["frequency"], 2)
        self.assertEqual(breakfast["materialIds"], ["m-2"])
        self.assertTrue(all("标题词不应进入" not in item["keyword"] for item in cloud["keywords"]))

    def test_keyword_cloud_keeps_domain_phrases_instead_of_two_character_fragments(self):
        analysis = example_analysis()
        analysis["scripts"]["materials"][0]["text"] = "直播间鲜炖燕窝和小冰箱，直播间鲜炖燕窝，小冰箱"
        keywords = [item["keyword"] for item in build_analysis_units(analysis)["chapter03-script"]["keywordCloud"]["keywords"]]
        self.assertIn("直播间", keywords)
        self.assertIn("鲜炖", keywords)
        self.assertIn("燕窝", keywords)
        self.assertIn("冰箱", keywords)
        self.assertNotIn("播间", keywords)
        self.assertNotIn("炖燕", keywords)

    def test_keyword_cloud_filters_function_words_by_part_of_speech_not_word_list(self):
        analysis = example_analysis()
        analysis["scripts"]["materials"][0]["text"] = "燕窝营养 现在 这个 真的 他们 赠送 加热器"
        keywords = [item["keyword"] for item in build_analysis_units(analysis)["chapter03-script"]["keywordCloud"]["keywords"]]
        self.assertNotIn("现在", keywords)
        self.assertNotIn("这个", keywords)
        self.assertNotIn("他们", keywords)

    def test_chapter03_reuses_precomputed_keyword_cloud_facts(self):
        analysis = example_analysis()
        analysis["scripts"]["keywordCloud"] = {
            "source": "facts.scripts.materials[*].text",
            "keywords": [{"keyword": "预计算短语", "frequency": 9, "materialIds": ["m-1"], "evidenceRefs": ["script:m-1:text:预计算短语"]}],
        }
        cloud = build_analysis_units(analysis)["chapter03-script"]["keywordCloud"]
        self.assertEqual(cloud["keywords"][0]["keyword"], "预计算短语")
        self.assertEqual(cloud["keywords"][0]["frequency"], 9)

    def test_chapter01_contains_only_material_list_facts(self):
        analysis = example_analysis()
        analysis["portfolio"] = {
            "newMaterialCount7d": 3,
            "newMaterialStartDate": "2026-08-21",
            "newMaterialEndDate": "2026-08-27",
            "metrics": {"cost": "100", "ctr": "2.5"},
            "hotMaterialRule": {
                "thresholdCost": "20000",
                "comparison": "gte",
                "qualifyingCount": 1,
                "qualifyingMaterials": [{
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "totalCost": "20000",
                    "metrics": {"roi": "2.1"},
                    "evidenceRef": "commercial:hotMaterial:m-1",
                }],
            },
        }
        units = build_analysis_units(analysis)
        chapter01 = units["chapter01-portfolio"]
        self.assertEqual(chapter01["portfolio"], analysis["portfolio"])
        self.assertEqual(
            chapter01["portfolio"]["hotMaterialRule"]["qualifyingCount"], 1
        )
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
            self.assertEqual(manifest["executionMode"], "current_model_fragments")
            self.assertEqual(manifest["analysisSchemaVersion"], 5)
            self.assertEqual(manifest["materialIds"], ["m-1"])
            self.assertEqual(manifest["frames"]["successfulCount"], 1)
            self.assertEqual(manifest["frames"]["missingCount"], 0)
            self.assertEqual(len(manifest["units"]), 5)
            display_path = Path(temp_dir) / "report-display-facts.json"
            self.assertTrue(display_path.is_file())
            display_raw = display_path.read_bytes()
            self.assertEqual(manifest["displayFacts"]["path"], "report-display-facts.json")
            self.assertEqual(manifest["displayFacts"]["byteSize"], len(display_raw))
            self.assertEqual(
                manifest["displayFacts"]["sha256"], hashlib.sha256(display_raw).hexdigest()
            )

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
