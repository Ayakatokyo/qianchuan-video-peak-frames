import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from diagnostic_analysis_lib import (  # noqa: E402
    DEFAULT_PROMPT_PATH,
    DiagnosticAnalysisError,
    build_diagnostic_analysis,
    prepare_diagnostic_analysis,
)


def crowd_record(material_id, metric, dimension, bucket, value, **overrides):
    record = {
        "recordType": "crowd_analysis",
        "materialId": material_id,
        "displayMetric": metric,
        "dimensionKey": dimension,
        "dimensionName": {"age": "年龄", "gender": "性别"}.get(
            dimension, dimension
        ),
        "dimensionValue": bucket,
        "metricValue": value,
        "metricPercentage": 999,
    }
    record.update(overrides)
    return record


def write_diagnosis_csv(path, records):
    headers = [
        "recordType",
        "materialId",
        "displayMetric",
        "dimensionKey",
        "dimensionName",
        "dimensionValue",
        "metricValue",
        "Dimensions",
        "Metrics",
        "scriptInfo",
        "materialUri",
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


class DiagnosticAnalysisAudienceTests(unittest.TestCase):
    def test_chapter01_portfolio_keeps_full_material_list_facts(self):
        top_payload = {
            "materials": [{"materialId": "m-1", "materialName": "榜单素材"}],
            "portfolioFacts": {
                "materialCount": 2,
                "materials": {
                    "m-1": {"materialId": "m-1", "metrics": {"ctr": "1.0"}},
                    "m-2": {"materialId": "m-2", "metrics": {"ctr": "2.0"}},
                },
            },
        }

        result = build_diagnostic_analysis(top_payload, {})

        self.assertEqual(set(result["portfolio"]["materials"]), {"m-1", "m-2"})

    def test_chapter01_does_not_depend_on_selected_materials_for_portfolio_facts(self):
        top_payload = {
            "materials": [{"materialId": "m-1"}],
            "portfolioFacts": {
                "materialCount": 2,
                "materials": {
                    "m-1": {"materialId": "m-1"},
                    "m-2": {"materialId": "m-2"},
                },
            },
        }

        result = build_diagnostic_analysis(top_payload, {})

        self.assertEqual(result["portfolio"]["materialCount"], 2)
        self.assertEqual(set(result["portfolio"]["materials"]), {"m-1", "m-2"})

    def test_builds_relative_interaction_summary_for_selected_materials(self):
        top_payload = {
            "materials": [
                {"materialId": "m-1", "totalCost": 10},
                {"materialId": "m-2", "totalCost": 20},
            ]
        }
        records_by_material = {
            "m-1": [
                {"recordType": "interaction_series", "interactionMetric": "CLICK_COUNT", "durationSec": 0, "metricValue": 10},
                {"recordType": "interaction_series", "interactionMetric": "CLICK_COUNT", "durationSec": 10, "metricValue": 0},
            ],
            "m-2": [
                {"recordType": "interaction_series", "interactionMetric": "CLICK_COUNT", "durationSec": 0, "metricValue": 5},
                {"recordType": "interaction_series", "interactionMetric": "CLICK_COUNT", "durationSec": 20, "metricValue": 5},
            ],
        }

        result = build_diagnostic_analysis(top_payload, records_by_material)

        summary = result["interaction"]["metrics"]["CLICK_COUNT"]
        self.assertEqual(summary["sampleMaterialCount"], 2)
        self.assertEqual(summary["totalMetricValue"], 20.0)
        self.assertEqual(summary["buckets"][0]["materialCount"], 2)
        self.assertEqual(summary["buckets"][0]["share"], 0.75)

    def test_prompt_requires_full_chapter01_fact_coverage(self):
        prompt = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
        for portfolio_fact in ("近 7 天上新素材量", "点击率", "转化率", "3 秒完播率", "CPM"):
            self.assertIn(portfolio_fact, prompt)
        self.assertIn("T-7", prompt)
        self.assertNotIn("portfolioDimensionConclusions.audience", prompt)
        self.assertNotIn("portfolioDimensionConclusions.highlight", prompt)
        self.assertIn("只读取 `chapter01-portfolio-input.json` 的 `portfolio`", prompt)
        self.assertIn("`summaryMetrics.roi`", prompt)
        self.assertIn("total_prepay_and_pay_order_roi2", prompt)
        self.assertIn("不得读取 Chapter 01 顶层 `materials` 或 `materialIds`", prompt)
        self.assertIn("commercial.conclusion 不得重复 overallConclusion", prompt)
        self.assertIn("一个核心判断和一个优先验证动作", prompt)
        self.assertIn("Chapter 01 事实包不包含 `netSettlementRoi` 或 `overallPaymentRoi`", prompt)
        self.assertIn("不得读取 `topMaterialRoi`", prompt)
        self.assertIn("不得使用 RPA 精选素材数量、消耗", prompt)
        self.assertIn("不得使用候选补位结果", prompt)
        self.assertIn("不得从 Chapter 05 回退", prompt)
        self.assertIn("一二级内容公式", prompt)
        self.assertIn("脚本文案", prompt)

    def test_prompt_constrains_keyword_cloud_one_sentence_to_one_concise_judgement(self):
        prompt = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("28-45 个中文字符", prompt)
        self.assertIn("单一核心判断", prompt)
        self.assertIn("不得重复关键词频次、卖点卡片和验证动作", prompt)
        self.assertIn("验证动作放入 summary 或 sellingPoints", prompt)
        self.assertIn("summary 控制在 60-110 个中文字符", prompt)

    def test_prompt_requires_actual_observations_without_forced_differences(self):
        prompt = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("直接保存简短观察和分析 JSON 片段", prompt)
        self.assertIn("不强求六个角度全部有新信息", prompt)
        self.assertIn("不虚构独有动作、字幕和叙事作用", prompt)
        self.assertIn("不得创建临时脚本", prompt)
        self.assertIn("禁止随机标记、同义改写和关闭门禁", prompt)

    def test_builds_preference_top_materials_above_batch_baseline_by_total_cost(self):
        top_payload = {
            "materials": [
                {
                    "rank": 1,
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "totalCost": 100,
                },
                {
                    "rank": 2,
                    "materialId": "m-2",
                    "materialName": "素材二",
                    "totalCost": 200,
                },
                {
                    "rank": 3,
                    "materialId": "m-3",
                    "materialName": "素材三",
                    "totalCost": 50,
                },
                {
                    "rank": 4,
                    "materialId": "m-4",
                    "materialName": "素材四",
                    "totalCost": 999,
                },
            ]
        }
        records_by_material = {
            "m-1": [
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "新锐白领", 60
                ),
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "资深中产", 30
                ),
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 10
                ),
            ],
            "m-2": [
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "新锐白领", 70
                ),
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "资深中产", 20
                ),
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 10
                ),
            ],
            "m-3": [
                crowd_record(
                    "m-3", "SHOW_COUNT", "user_group_label_name", "新锐白领", 50
                ),
                crowd_record(
                    "m-3", "SHOW_COUNT", "user_group_label_name", "资深中产", 10
                ),
                crowd_record(
                    "m-3", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 40
                ),
            ],
            "m-4": [
                crowd_record(
                    "m-4", "SHOW_COUNT", "user_group_label_name", "新锐白领", 50
                ),
                crowd_record(
                    "m-4", "SHOW_COUNT", "user_group_label_name", "资深中产", 25
                ),
                crowd_record(
                    "m-4", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 25
                ),
            ],
        }

        preferences = build_diagnostic_analysis(top_payload, records_by_material)[
            "audience"
        ]["preferencesByMetric"]["SHOW_COUNT"]
        preferences_by_group = {
            group["audienceGroup"]: group["topMaterials"] for group in preferences
        }

        self.assertEqual(
            [item["materialId"] for item in preferences_by_group["新锐白领"]],
            ["m-2", "m-1"],
        )
        self.assertEqual(
            [item["materialId"] for item in preferences_by_group["资深中产"]],
            ["m-4", "m-1"],
        )
        self.assertEqual(
            [item["materialId"] for item in preferences_by_group["精致妈妈"]],
            ["m-4", "m-3"],
        )
        self.assertEqual(
            preferences_by_group["资深中产"][0],
            {
                "rank": 1,
                "materialId": "m-4",
                "materialName": "素材四",
                "preferenceMetric": "SHOW_COUNT",
                "audiencePercentage": 25.0,
                "audienceMetricValue": 25.0,
                "audienceBaselinePercentage": 21.25,
                "preferenceDeltaPercentage": 3.75,
                "preferenceLift": 1.176471,
                "audienceShowCount": 25.0,
                "totalCost": 999.0,
            },
        )

    def test_preference_omits_groups_not_above_baseline(self):
        top_payload = {
            "materials": [
                {
                    "rank": 1,
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "totalCost": 100,
                },
                {
                    "rank": 2,
                    "materialId": "m-2",
                    "materialName": "素材二",
                    "totalCost": 200,
                },
            ]
        }
        records_by_material = {
            "m-1": [
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 40
                ),
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "新锐白领", 60
                ),
            ],
            "m-2": [
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 10
                ),
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "新锐白领", 90
                ),
            ],
        }

        preferences = build_diagnostic_analysis(top_payload, records_by_material)[
            "audience"
        ]["preferencesByMetric"]["SHOW_COUNT"]
        preferences_by_group = {
            group["audienceGroup"]: group["topMaterials"] for group in preferences
        }

        self.assertIn("精致妈妈", preferences_by_group)
        self.assertEqual(
            preferences_by_group["精致妈妈"],
            [
                {
                    "rank": 1,
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "preferenceMetric": "SHOW_COUNT",
                    "audiencePercentage": 40.0,
                    "audienceMetricValue": 40.0,
                    "audienceBaselinePercentage": 25.0,
                    "preferenceDeltaPercentage": 15.0,
                    "preferenceLift": 1.6,
                    "audienceShowCount": 40.0,
                    "totalCost": 100.0,
                }
            ],
        )
        self.assertNotIn("m-2", [
            item["materialId"] for item in preferences_by_group["精致妈妈"]
        ])

    def test_preference_omits_groups_when_no_material_is_above_baseline(self):
        top_payload = {
            "materials": [
                {"rank": 1, "materialId": "m-1", "materialName": "素材一"},
                {"rank": 2, "materialId": "m-2", "materialName": "素材二"},
            ]
        }
        records_by_material = {
            "m-1": [
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 40
                ),
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "新锐白领", 60
                ),
            ],
            "m-2": [
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "精致妈妈", 40
                ),
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "新锐白领", 60
                ),
            ],
        }

        preferences = build_diagnostic_analysis(top_payload, records_by_material)[
            "audience"
        ]["preferencesByMetric"]["SHOW_COUNT"]

        self.assertEqual(preferences, [])

    def test_builds_metric_specific_preference_top_materials(self):
        top_payload = {
            "materials": [
                {
                    "rank": 1,
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "totalCost": 100,
                },
                {
                    "rank": 2,
                    "materialId": "m-2",
                    "materialName": "素材二",
                    "totalCost": 200,
                },
            ]
        }
        records_by_material = {
            "m-1": [
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "新锐白领", 80
                ),
                crowd_record(
                    "m-1", "SHOW_COUNT", "user_group_label_name", "资深中产", 20
                ),
                crowd_record(
                    "m-1", "CLICK_COUNT", "user_group_label_name", "新锐白领", 20
                ),
                crowd_record(
                    "m-1", "CLICK_COUNT", "user_group_label_name", "资深中产", 80
                ),
            ],
            "m-2": [
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "新锐白领", 40
                ),
                crowd_record(
                    "m-2", "SHOW_COUNT", "user_group_label_name", "资深中产", 60
                ),
                crowd_record(
                    "m-2", "CLICK_COUNT", "user_group_label_name", "新锐白领", 70
                ),
                crowd_record(
                    "m-2", "CLICK_COUNT", "user_group_label_name", "资深中产", 30
                ),
            ],
        }

        audience = build_diagnostic_analysis(top_payload, records_by_material)["audience"]
        preferences_by_metric = audience["preferencesByMetric"]

        self.assertNotIn("preferences", audience)
        show_preferences = {
            group["audienceGroup"]: group["topMaterials"]
            for group in preferences_by_metric["SHOW_COUNT"]
        }
        click_preferences = {
            group["audienceGroup"]: group["topMaterials"]
            for group in preferences_by_metric["CLICK_COUNT"]
        }

        self.assertEqual(show_preferences["新锐白领"][0]["materialId"], "m-1")
        self.assertEqual(click_preferences["新锐白领"][0]["materialId"], "m-2")
        self.assertEqual(show_preferences["资深中产"][0]["materialId"], "m-2")
        self.assertEqual(click_preferences["资深中产"][0]["materialId"], "m-1")
        self.assertEqual(click_preferences["资深中产"][0]["preferenceMetric"], "CLICK_COUNT")
        self.assertEqual(click_preferences["资深中产"][0]["audienceMetricValue"], 80.0)

    def test_materials_include_video_metadata_from_retained_material_info(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [{"rank": 1, "materialId": "m-1", "materialName": "素材一"}],
        }
        records_by_material = {
            "m-1": [
                {
                    "recordType": "material_info",
                    "materialId": "m-1",
                    "videoUrl": "https://video.example/m-1.mp4?token=signed",
                    "thumbnailUrl": "https://image.example/m-1.jpg?token=signed",
                    "videoDuration": "46.022",
                    "materialUri": "material-uri-1",
                }
            ]
        }

        analysis = build_diagnostic_analysis(top_payload, records_by_material)

        self.assertEqual(
            analysis["materials"][0]["video"],
            {
                "src": "https://video.example/m-1.mp4?token=signed",
                "poster": "https://image.example/m-1.jpg?token=signed",
                "durationSec": 46.022,
                "materialUri": "material-uri-1",
            },
        )

    def test_aggregates_each_audience_metric_and_recomputes_percentages(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [
                {"rank": 1, "materialId": "m-1", "materialName": "素材一"},
                {"rank": 2, "materialId": "m-2", "materialName": "素材二"},
            ],
        }
        records_by_material = {
            "m-1": [
                crowd_record("m-1", "SHOW_COUNT", "age", "18-23", 100),
                crowd_record("m-1", "SHOW_COUNT", "age", "24-30", 300),
                crowd_record(
                    "m-1",
                    "CLICK_COUNT",
                    "age",
                    "18-23",
                    "",
                    Metrics={"click": {"Value": "30"}},
                ),
                crowd_record(
                    "m-1",
                    "TRANSACTION_AMOUNT",
                    "gender",
                    "男",
                    "",
                    Metrics={"transaction": {"Value": 20}},
                ),
                crowd_record("m-1", "SHOW_COUNT", "age", "ignored", -5),
                crowd_record("other", "SHOW_COUNT", "age", "ignored", 500),
            ],
            "m-2": [
                crowd_record("m-2", "SHOW_COUNT", "age", "18-23", 100),
                crowd_record(
                    "m-2",
                    "TRANSACTION_AMOUNT",
                    "gender",
                    "",
                    "",
                    dimensionValue="",
                    Dimensions={"gender": {"ValueStr": "女"}},
                    Metrics={"transaction": {"Value": 40}},
                ),
                crowd_record("m-2", "UNUSED_METRIC", "age", "18-23", 50),
            ],
        }

        analysis = build_diagnostic_analysis(top_payload, records_by_material)

        self.assertEqual(analysis["schemaVersion"], 1)
        self.assertEqual(
            analysis["period"],
            {"actualStartDate": "2026-06-01", "actualEndDate": "2026-06-02"},
        )
        metrics = analysis["audience"]["metrics"]
        self.assertEqual(
            set(metrics), {"SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT"}
        )
        age = metrics["SHOW_COUNT"]["dimensions"]["age"]
        self.assertEqual(age["totalMetricValue"], 500.0)
        self.assertEqual(age["sourceMaterialIds"], ["m-1", "m-2"])
        self.assertEqual(
            age["buckets"],
            [
                {
                    "bucket": "24-30",
                    "metricValue": 300.0,
                    "metricPercentage": 60.0,
                    "sourceMaterialIds": ["m-1"],
                },
                {
                    "bucket": "18-23",
                    "metricValue": 200.0,
                    "metricPercentage": 40.0,
                    "sourceMaterialIds": ["m-1", "m-2"],
                },
            ],
        )
        self.assertEqual(metrics["SHOW_COUNT"]["missingMaterialIds"], [])
        self.assertEqual(metrics["CLICK_COUNT"]["missingMaterialIds"], ["m-2"])
        gender = metrics["TRANSACTION_AMOUNT"]["dimensions"]["gender"]
        self.assertEqual(gender["totalMetricValue"], 60.0)
        self.assertEqual(
            [(item["bucket"], item["metricValue"]) for item in gender["buckets"]],
            [("女", 40.0), ("男", 20.0)],
        )


class DiagnosticAnalysisScriptAndDeliveryTests(unittest.TestCase):
    def test_preserves_scripts_and_delivery_facts_without_aggregating_rates(self):
        top_payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materials": [
                {"rank": 1, "materialId": "m-1"},
                {"rank": 2, "materialId": "m-2"},
                {"rank": 3, "materialId": "m-3"},
            ],
        }
        records_by_material = {
            "m-1": [
                {
                    "recordType": "script_analysis",
                    "materialId": "m-1",
                    "materialUri": "uri-1",
                    "scriptInfo": (
                        "{'formula': {'labels': [{'level_1_label': 1, "
                        "'level_2_labels': [21]}]}, 'detail': {'21': "
                        "{'label': '降价/折扣', 'list': ['今天四九折', '今天四九折']}}, "
                        "'text': '完整口播', 'data': [{'level_1_label': "
                        "{'value': 1, 'label': '价格优惠'}, 'level_2_labels': "
                        "[{'value': 21, 'label': '降价/折扣'}]}]}"
                    ),
                },
                {
                    "recordType": "delivery_material_detail",
                    "materialId": "m-1",
                    "statDate": "2026-06-02",
                    "clickCount": "1,490",
                    "clickRate": "0.89%",
                    "payRoi": "2.3",
                    "netPayGmv": "47,793.00",
                    "videoUrl": "https://signed.example/video.mp4?token=secret",
                    "materialUri": "uri-1",
                },
            ],
            "m-2": [
                {
                    "recordType": "script_analysis",
                    "materialId": "m-2",
                    "materialUri": "uri-2",
                    "scriptInfo": (
                        '{"detail": {"21": {"label": "降价/折扣", '
                        '"list": ["今天四九折"]}}, "text": "第二段口播", '
                        '"data": [{"level_1_label": {"value": 1, '
                        '"label": "价格优惠"}, "level_2_labels": '
                        '[{"value": 21, "label": "降价/折扣"}]}]}'
                    ),
                }
            ],
            "m-3": [
                {
                    "recordType": "script_analysis",
                    "materialId": "m-3",
                    "materialUri": "uri-3",
                    "scriptInfo": "this is not structured data",
                },
                {
                    "recordType": "delivery_material_detail",
                    "materialId": "m-3",
                    "statDate": "2026-06-02",
                    "clickCount": "NaN",
                },
            ],
        }

        analysis = build_diagnostic_analysis(top_payload, records_by_material)

        self.assertEqual(
            analysis["scripts"]["materials"],
            [
                {
                    "materialId": "m-1",
                    "materialUri": "uri-1",
                    "text": "完整口播",
                    "labels": [{"level1": "价格优惠", "level2": "降价/折扣"}],
                    "evidence": [
                        {"level2": "降价/折扣", "sentence": "今天四九折"}
                    ],
                },
                {
                    "materialId": "m-2",
                    "materialUri": "uri-2",
                    "text": "第二段口播",
                    "labels": [{"level1": "价格优惠", "level2": "降价/折扣"}],
                    "evidence": [
                        {"level2": "降价/折扣", "sentence": "今天四九折"}
                    ],
                },
            ],
        )
        self.assertEqual(
            analysis["scripts"]["labelSummary"],
            [
                {
                    "level1": "价格优惠",
                    "level2": "降价/折扣",
                    "occurrenceCount": 2,
                    "materialIds": ["m-1", "m-2"],
                    "evidence": ["今天四九折"],
                }
            ],
        )
        self.assertEqual(
            analysis["delivery"]["materials"],
            [
                {
                    "materialId": "m-1",
                    "facts": [
                        {
                            "statDate": "2026-06-02",
                            "values": {
                                "clickCount": 1490.0,
                                "clickRate": "0.89%",
                                "netPayGmv": 47793.0,
                                "payRoi": 2.3,
                            },
                        }
                    ],
                },
                {
                    "materialId": "m-3",
                    "facts": [
                        {
                            "statDate": "2026-06-02",
                            "values": {"clickCount": "NaN"},
                        }
                    ],
                },
            ],
        )
        self.assertTrue(
            any("invalid scriptInfo for material m-3" in item for item in analysis["warnings"])
        )


class DiagnosticAnalysisPublicationTests(unittest.TestCase):
    def _write_json(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_publishes_fact_package_and_default_prompt_with_record_warnings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            output_dir = root / "analysis"
            records_dir.mkdir()
            self._write_json(
                top_materials,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-02",
                    "materials": [
                        {"rank": 1, "materialId": "m-1", "materialName": "素材一"},
                        {"rank": 2, "materialId": "m-2", "materialName": "素材二"},
                        {"rank": 3, "materialId": "m-3", "materialName": "素材三"},
                    ],
                },
            )
            self._write_json(
                records_dir / "m-1.json",
                [
                    crowd_record("m-1", "SHOW_COUNT", "age", "24-30", 100),
                    {
                        "recordType": "delivery_material_detail",
                        "materialId": "m-1",
                        "statDate": "2026-06-02",
                        "videoUrl": "https://signed.example/video.mp4?token=secret",
                    },
                ],
            )
            self._write_json(records_dir / "m-2.json", {"not": "a record list"})

            prompt_path, analysis = prepare_diagnostic_analysis(
                top_materials, records_dir, output_dir
            )

            self.assertEqual(prompt_path, output_dir / "diagnostic-analysis-prompt.md")
            self.assertFalse((output_dir / "diagnostic-analysis-data.json").exists())
            self.assertEqual(
                prompt_path.read_text(encoding="utf-8"),
                DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"),
            )
            self.assertNotIn("https://signed.example", json.dumps(analysis, ensure_ascii=False))
            self.assertEqual(
                [(item["rank"], item["materialId"], item["materialName"]) for item in analysis["materials"]],
                [(1, "m-1", "素材一"), (2, "m-2", "素材二"), (3, "m-3", "素材三")],
            )
            self.assertIn("audience", analysis["materials"][0])
            self.assertIn("script", analysis["materials"][0])
            self.assertIn("interactionTimeline", analysis["materials"][0])
            self.assertIn("evidenceIndex", analysis["materials"][0])
            self.assertIn("diagnosis records for material m-2 are not a list", analysis["warnings"])
            self.assertIn("missing diagnosis records for material m-3", analysis["warnings"])

    def test_does_not_publish_legacy_compatibility_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            output_dir = root / "analysis"
            records_dir.mkdir()
            self._write_json(
                top_materials,
                {"materials": [{"rank": 1, "materialId": "m-1", "materialName": "素材一"}]},
            )
            self._write_json(
                records_dir / "m-1.json",
                [crowd_record("m-1", "SHOW_COUNT", "age", "24-30", 100)],
            )

            _prompt_path, analysis = prepare_diagnostic_analysis(
                top_materials, records_dir, output_dir
            )

            self.assertFalse((output_dir / "diagnostic-analysis-data.json").exists())
            self.assertEqual(analysis["materials"][0]["materialId"], "m-1")

    def test_prepare_accepts_retained_diagnosis_csv_dir_without_json_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            csv_dir = root / "diagnosis-csv"
            output_dir = root / "analysis"
            self._write_json(
                top_materials,
                {
                    "actualStartDate": "2026-06-01",
                    "actualEndDate": "2026-06-02",
                    "materials": [{"rank": 1, "materialId": "m-1", "materialName": "素材一"}],
                },
            )
            write_diagnosis_csv(
                csv_dir / "m-1.csv",
                [
                    crowd_record("m-1", "SHOW_COUNT", "age", "24-30", 100),
                    {
                        "recordType": "script_analysis",
                        "materialId": "m-1",
                        "materialUri": "uri-1",
                        "scriptInfo": json.dumps(
                            {
                                "text": "早餐场景口播",
                                "data": [
                                    {
                                        "level_1_label": {"label": "商品卖点"},
                                        "level_2_labels": [
                                            {"value": 11, "label": "适用场景"}
                                        ],
                                    }
                                ],
                                "detail": {
                                    "11": {
                                        "label": "适用场景",
                                        "list": ["早餐场景口播"],
                                    }
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )

            _prompt_path, analysis = prepare_diagnostic_analysis(
                top_materials, csv_dir, output_dir
            )

            age = analysis["audience"]["metrics"]["SHOW_COUNT"]["dimensions"]["age"]
            self.assertEqual(age["buckets"][0]["bucket"], "24-30")
            self.assertEqual(
                analysis["scripts"]["labelSummary"][0]["level2"], "适用场景"
            )
            self.assertNotIn(
                "missing diagnosis records for material m-1", analysis["warnings"]
            )

    def test_copies_override_prompt_and_rejects_missing_template_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            output_dir = root / "analysis"
            template = root / "custom-prompt.md"
            records_dir.mkdir()
            self._write_json(
                top_materials,
                {"materials": [{"materialId": "m-1"}]},
            )
            self._write_json(records_dir / "m-1.json", [])
            template.write_text("custom prompt\n", encoding="utf-8")

            prompt_path, _analysis = prepare_diagnostic_analysis(
                top_materials, records_dir, output_dir, template
            )

            self.assertEqual(prompt_path.read_text(encoding="utf-8"), "custom prompt\n")
            with self.assertRaisesRegex(DiagnosticAnalysisError, "prompt template"):
                prepare_diagnostic_analysis(
                    top_materials, records_dir, root / "bad-output", root / "missing.md"
                )
            self.assertFalse((root / "bad-output").exists())

    def test_publishes_highlight_evidence_and_warns_for_missing_jpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            peak_dir = root / "peak-frames"
            output_dir = root / "analysis"
            records_dir.mkdir()
            self._write_json(
                top_materials,
                {"materials": [{"materialId": "m-1", "materialName": "素材一"}]},
            )
            self._write_json(records_dir / "m-1.json", [])
            material_dir = peak_dir / "m-1"
            material_dir.mkdir(parents=True)
            (material_dir / "first_peak_0001s_3.jpg").write_bytes(
                b"\xff\xd8fake-jpeg\xff\xd9"
            )
            self._write_json(
                material_dir / "summary.json",
                {
                    "frames": [
                        {
                            "rankType": "first_peak",
                            "durationSec": 1,
                            "metricValue": 3,
                            "framePath": "first_peak_0001s_3.jpg",
                        },
                        {
                            "rankType": "later_top_1",
                            "durationSec": 5,
                            "metricValue": 2,
                            "framePath": "later_top_1_0005s_2.jpg",
                        },
                    ],
                    "missingFrames": [
                        {
                            "rankType": "later_top_1",
                            "durationSec": 5,
                            "metricValue": 2,
                            "requestedTimestampSec": 5,
                            "errorType": "FfmpegError",
                            "errorMessage": "decoder killed",
                        }
                    ],
                },
            )

            _prompt_path, analysis = prepare_diagnostic_analysis(
                top_materials,
                records_dir,
                output_dir,
                peak_output_dir=peak_dir,
            )

            frames = analysis["highlights"]["materials"][0]["frames"]
            self.assertEqual(frames[0]["evidenceRef"], "highlight:m-1:first_peak")
            self.assertEqual(
                frames[0]["framePath"],
                str(material_dir / "first_peak_0001s_3.jpg"),
            )
            self.assertIn(
                "missing high-light JPEG for material m-1: later_top_1",
                analysis["warnings"],
            )
            self.assertEqual(
                analysis["highlights"]["materials"][0]["missingFrames"],
                [
                    {
                        "rankType": "later_top_1",
                        "durationSec": 5,
                        "metricValue": 2,
                        "requestedTimestampSec": 5,
                        "errorType": "FfmpegError",
                        "errorMessage": "decoder killed",
                    }
                ],
            )


class DiagnosticAnalysisCommercialFactsTests(unittest.TestCase):
    def test_prepare_includes_verified_commercial_facts_for_selected_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            roi_facts = root / "top-material-roi-facts-v1.json"
            output_dir = root / "analysis"
            records_dir.mkdir()
            top_payload = {
                "actualStartDate": "2026-06-01",
                "actualEndDate": "2026-06-02",
                "materials": [{"rank": 1, "materialId": "m-1"}],
            }
            top_materials.write_text(json.dumps(top_payload), encoding="utf-8")
            (records_dir / "m-1.json").write_text("[]", encoding="utf-8")
            roi_facts.write_text(json.dumps({
                "schemaVersion": 1,
                "actualStartDate": "2026-06-01",
                "actualEndDate": "2026-06-02",
                "materialIds": ["m-1"],
                "totals": {
                    "cost": "100",
                    "netSettlementAmount": "120",
                    "overallPaymentAmount": "150",
                },
                "metrics": [
                    {"key": "netSettlementRoi", "label": "净成交 ROI", "value": "1.2", "available": True},
                    {"key": "overallPaymentRoi", "label": "整体支付 ROI", "value": "1.5", "available": True},
                ],
            }), encoding="utf-8")

            _prompt_path, analysis = prepare_diagnostic_analysis(
                top_materials,
                records_dir,
                output_dir,
                top_material_roi_facts_path=roi_facts,
            )

        self.assertEqual(analysis["commercial"], {
            "available": True,
            "totals": {
                "cost": "100",
                "netSettlementAmount": "120",
                "overallPaymentAmount": "150",
            },
            "metrics": [
                {"evidenceRef": "commercial:netSettlementRoi", "key": "netSettlementRoi", "label": "净成交 ROI", "value": "1.2", "available": True},
                {"evidenceRef": "commercial:overallPaymentRoi", "key": "overallPaymentRoi", "label": "整体支付 ROI", "value": "1.5", "available": True},
            ],
        })


class DiagnosticAnalysisUnitIntegrationTests(unittest.TestCase):
    def test_prepare_publishes_analysis_units_without_compatibility_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top_materials = root / "top_materials.json"
            records_dir = root / "diagnosis-records"
            output_dir = root / "diagnostic-analysis"
            records_dir.mkdir()
            top_materials.write_text(
                json.dumps(
                    {
                        "actualStartDate": "2026-08-10",
                        "actualEndDate": "2026-08-20",
                        "materials": [
                            {"rank": 1, "materialId": "m-1", "materialName": "素材一"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (records_dir / "m-1.json").write_text("[]", encoding="utf-8")

            prompt_path, analysis = prepare_diagnostic_analysis(
                top_materials, records_dir, output_dir
            )

            self.assertEqual(prompt_path, output_dir / "diagnostic-analysis-prompt.md")
            self.assertFalse((output_dir / "diagnostic-analysis-data.json").exists())
            manifest_path = (
                output_dir / "analysis-units" / "analysis-unit-manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["units"]), 5)
            self.assertEqual(manifest["materialIds"], ["m-1"])


if __name__ == "__main__":
    unittest.main()
