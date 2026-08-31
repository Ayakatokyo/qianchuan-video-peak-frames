import json
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qianchuan_presentation import build_presentation_report, validate_presentation_report
from report_renderer import report_data_for_template, render_html_report


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
            "preferencesByMetric": {
                "CLICK_COUNT": [{"audienceGroup": "24-30", "topMaterials": [{"materialId": "m-1"}]}]
            },
        },
        "scripts": {
            "keywordCloud": {"source": "facts.scripts.materials[*].text", "keywords": [{"keyword": "早餐", "frequency": 3, "materialIds": ["m-1"], "evidenceRefs": ["script:m-1:text:早餐"]}]},
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
        "batchInsights": [
            {"topic": "audience", "title": "展现结构：生活场景扩大覆盖", "conclusion": "展现次数主要集中在成熟女性与新锐白领，下一轮验证生活场景入口是否能稳定扩大覆盖。", "evidenceRefs": ["audience:SHOW_COUNT:user_group_label_name:新锐白领"]},
            {"topic": "audience", "title": "点击结构：问题前置更值得验证", "conclusion": "点击次数在女性与核心年龄段占比更高，下一轮拆分问题前置与产品特写验证点击变化。", "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"]},
            {"topic": "audience", "title": "成交结构：价值解释需要匹配", "conclusion": "成交金额的集中人群与展现口径不同，下一轮分别验证价格、规格和品质解释的成交承接。", "evidenceRefs": ["audience:TRANSACTION_AMOUNT:gender:女"]},
            {"topic": "script", "title": "一二级内容公式：原始 AI 标题", "conclusion": "脚本结论", "evidenceRefs": ["script:m-1:商品卖点:适用场景"]},
        ],
        "keywordCloudInsight": {"oneSentenceConclusion": "脚本围绕早餐场景组织内容。", "summary": "高频词集中在早餐场景。", "sellingPoints": [{"title": "场景明确", "description": "早餐是主要切入口。", "keywordRefs": ["早餐"], "evidenceRefs": ["script:m-1:text:早餐"]}]},
        "keywordCloudReview": [{"keyword": "早餐", "decision": "accept"}, {"keyword": "小仙", "decision": "exclude"}],
        "nextActions": [{
            "title": "优先测试开场", "body": "将利益点前置到前 3 秒", "action": "改写开场", "validation": "比较 CTR", "priority": "high",
            "evidenceRefs": ["audience:m-1"], "materialIds": ["m-1"],
        }],
    })
    return data


class PresentationTests(unittest.TestCase):
    def test_chapter03_projects_keyword_cloud_between_ai_and_script_summary(self):
        chapter03 = build_presentation_report(complete_presentation_data())["chapters"]["chapter03"]
        panel = chapter03["keywordCloudPanel"]
        self.assertEqual(panel["keywords"][0]["keyword"], "早餐")
        self.assertEqual(panel["oneSentenceConclusion"], "脚本围绕早餐场景组织内容。")
        self.assertEqual(len(panel["sellingPoints"]), 1)
        self.assertEqual(panel["source"], "facts.scripts.keywordCloud + ai.keywordCloudInsight")

    def test_chapter03_keeps_selling_point_with_canonical_label_evidence(self):
        data = complete_presentation_data()
        data["conclusion"]["keywordCloudInsight"]["sellingPoints"][0]["evidenceRefs"] = [
            "script:m-1:商品卖点:适用场景"
        ]

        panel = build_presentation_report(data)["chapters"]["chapter03"]["keywordCloudPanel"]

        self.assertEqual(len(panel["sellingPoints"]), 1)
        self.assertEqual(
            panel["sellingPoints"][0]["evidenceRefs"],
            ["script:m-1:商品卖点:适用场景"],
        )

    def test_chapter03_applies_ai_review_to_keyword_cloud(self):
        data = complete_presentation_data()
        data["scripts"]["keywordCloud"]["keywords"].append({"keyword": "小仙", "frequency": 3, "materialIds": ["m-1"], "evidenceRefs": ["script:m-1:text:小仙"]})
        chapter03 = build_presentation_report(data)["chapters"]["chapter03"]
        self.assertNotIn("小仙", [item["keyword"] for item in chapter03["keywordCloudPanel"]["keywords"]])

    def test_projects_material_list_insight_as_single_point_without_lead_in(self):
        data = sample_data()
        data["portfolio"] = {
            "schemaVersion": 1,
            "period": {"actualStartDate": "2026-08-01", "actualEndDate": "2026-08-07"},
            "newMaterialCount7d": 3,
            "newMaterialStartDate": "2026-08-21",
            "newMaterialEndDate": "2026-08-27",
            "materialCount": 2,
            "summaryMetrics": {"cost": "100", "roi": "1.5", "ctr": "2.5"},
            "materials": {"m-1": {"materialId": "m-1", "createdAt": "2026-08-02"}},
        }
        data["conclusion"] = {
            "schemaVersion": 6,
            "overallConclusion": "榜单结论",
            "portfolioDimensionConclusions": {
                "commercial": {
                    "conclusion": "素材榜单数据结论",
                    "evidenceRefs": ["commercial:cost"],
                }
            },
        }
        report = build_presentation_report(data)
        chapter01 = report["chapters"]["chapter01"]
        self.assertEqual(chapter01["title"], "素材榜单数据洞察")
        self.assertEqual([point["id"] for point in chapter01["points"]], ["commercial"])
        self.assertEqual(chapter01["points"][0]["leadIn"], "")
        self.assertEqual(
            chapter01["portfolio"],
            {key: value for key, value in data["portfolio"].items() if key != "materials"},
        )
        self.assertNotIn("materials", chapter01["portfolio"])

    def test_chapter01_does_not_fallback_to_rpa_roi_when_portfolio_is_missing(self):
        data = sample_data()
        data["topMaterialRoi"] = {
            "source": "verifiedAssetFacts",
            "materialCount": 15,
            "metrics": [{"key": "netSettlementRoi", "value": "1.20"}],
        }

        chapter01 = build_presentation_report(data)["chapters"]["chapter01"]

        self.assertEqual(chapter01["portfolio"], {})
        self.assertEqual(chapter01["hotMaterialPanel"]["availability"], "unavailable")
        self.assertNotIn("topMaterialRoi", json.dumps(chapter01, ensure_ascii=False))
        self.assertNotIn("netSettlementRoi", json.dumps(chapter01, ensure_ascii=False))

    def test_chapter01_drops_rpa_shaped_portfolio_fields_from_presentation_and_html(self):
        data = sample_data()
        data["portfolio"] = {
            "source": "verifiedAssetFacts",
            "materialCount": 15,
            "netSettlementRoi": "1.20",
            "overallPaymentRoi": "1.50",
        }
        data["presentation"] = build_presentation_report(data)

        chapter01 = data["presentation"]["chapters"]["chapter01"]
        rendered = report_data_for_template(data, None)

        self.assertEqual(chapter01["portfolio"], {})
        self.assertEqual(
            rendered["presentation"]["chapters"]["chapter01"]["portfolio"], {}
        )
        self.assertNotIn("netSettlementRoi", json.dumps(rendered, ensure_ascii=False))
        self.assertNotIn("overallPaymentRoi", json.dumps(rendered, ensure_ascii=False))

    def test_chapter01_filters_nested_rpa_metrics_from_portfolio_projection(self):
        data = sample_data()
        data["portfolio"] = {
            "schemaVersion": 1,
            "summaryMetrics": {
                "cost": "100",
                "roi": "2.0",
                "netSettlementRoi": "1.20",
                "overallPaymentRoi": "1.50",
            },
            "hotMaterialRule": {
                "thresholdCost": "20000",
                "comparison": "gte",
                "qualifyingCount": 1,
                "qualifyingMaterials": [{
                    "materialId": "m-1",
                    "materialName": "真实素材名",
                    "totalCost": "20000",
                    "metrics": {"roi": "2.0", "netSettlementRoi": "1.20"},
                    "evidenceRef": "commercial:hotMaterial:m-1",
                }],
            },
        }

        chapter01 = build_presentation_report(data)["chapters"]["chapter01"]

        self.assertEqual(chapter01["portfolio"]["summaryMetrics"], {"cost": "100", "roi": "2.0"})
        self.assertEqual(chapter01["hotMaterialPanel"]["materials"][0]["metrics"], {"roi": "2.0"})
        self.assertNotIn("netSettlementRoi", json.dumps(chapter01, ensure_ascii=False))

    def test_projects_hot_material_panel_with_only_qualifying_materials(self):
        data = sample_data()
        data["portfolio"] = {
            "schemaVersion": 1,
            "materialCount": 1145,
            "summaryMetrics": {"cost": "1167092.47", "roi": "2.29"},
            "materials": {"m-1": {"materialId": "m-1"}, "m-2": {"materialId": "m-2"}},
            "hotMaterialRule": {
                "thresholdCost": "20000",
                "comparison": "gte",
                "qualifyingCount": 1,
                "qualifyingMaterials": [{
                    "materialId": "m-1",
                    "materialName": "爆款真实名称",
                    "totalCost": "20000",
                    "metrics": {"roi": "2.0", "ctr": "1.5"},
                    "evidenceRef": "commercial:hotMaterial:m-1",
                }],
            },
        }
        data["conclusion"]["portfolioDimensionConclusions"] = {
            "commercial": {"conclusion": "整体榜单结论", "evidenceRefs": ["commercial:cost"]},
        }
        data["conclusion"]["hotMaterialInsight"] = {
            "conclusion": "命中素材值得保留并做小预算对照。",
            "evidenceRefs": ["commercial:hotMaterialCount", "commercial:hotMaterial:m-1"],
        }

        panel = build_presentation_report(data)["chapters"]["chapter01"]["hotMaterialPanel"]

        self.assertEqual(panel["thresholdCost"], "20000")
        self.assertEqual(panel["qualifyingCount"], 1)
        self.assertEqual([item["materialName"] for item in panel["materials"]], ["爆款真实名称"])
        self.assertNotIn("insight", panel)
        self.assertNotIn("m-2", json.dumps(panel, ensure_ascii=False))

    def test_projects_overall_and_hot_ai_content_as_one_chapter01_ai_conclusion(self):
        data = sample_data()
        data["portfolio"] = {
            "materialCount": 2,
            "summaryMetrics": {"cost": "30000"},
            "hotMaterialRule": {
                "thresholdCost": "20000",
                "comparison": "gte",
                "qualifyingCount": 1,
                "qualifyingMaterials": [{
                    "materialId": "m-1",
                    "materialName": "爆款真实名称",
                    "totalCost": "20000",
                    "metrics": {"roi": "2.0"},
                    "evidenceRef": "commercial:hotMaterial:m-1",
                }],
            },
        }
        data["conclusion"]["overallConclusion"] = "整体榜单结论"
        data["conclusion"]["portfolioDimensionConclusions"] = {
            "commercial": {"conclusion": "整体维度结论", "evidenceRefs": ["commercial:cost"]},
        }
        data["conclusion"]["hotMaterialInsight"] = {
            "conclusion": "命中素材值得保留并做小预算对照。",
            "evidenceRefs": ["commercial:hotMaterialCount", "commercial:hotMaterial:m-1"],
        }

        chapter = build_presentation_report(data)["chapters"]["chapter01"]

        self.assertEqual(
            chapter["aiConclusion"]["paragraphs"],
            ["整体榜单结论", "命中素材值得保留并做小预算对照。"],
        )
        self.assertEqual(
            chapter["aiConclusion"]["evidenceRefs"],
            ["commercial:hotMaterialCount", "commercial:hotMaterial:m-1"],
        )
        self.assertEqual(chapter["hotMaterialPanel"]["materials"][0]["materialId"], "m-1")

    def test_renderer_includes_hot_material_panel_without_full_portfolio_materials(self):
        data = sample_data()
        data["portfolio"] = {
            "materialCount": 1145,
            "summaryMetrics": {"cost": "1167092.47"},
            "materials": {"m-1": {"materialId": "m-1"}, "m-2": {"materialId": "m-2"}},
            "hotMaterialRule": {
                "thresholdCost": "20000",
                "comparison": "gte",
                "qualifyingCount": 1,
                "qualifyingMaterials": [{
                    "materialId": "m-1",
                    "materialName": "爆款真实名称",
                    "totalCost": "20000",
                    "metrics": {"roi": "2.0"},
                    "evidenceRef": "commercial:hotMaterial:m-1",
                }],
            },
        }
        data["conclusion"]["portfolioDimensionConclusions"] = {
            "commercial": {"conclusion": "整体榜单结论", "evidenceRefs": ["commercial:cost"]},
        }
        data["conclusion"]["hotMaterialInsight"] = {
            "conclusion": "命中素材值得保留并做小预算对照。",
            "evidenceRefs": ["commercial:hotMaterialCount"],
        }
        data["presentation"] = build_presentation_report(data)

        rendered = report_data_for_template(data, None)
        html = render_html_report(data)

        panel = rendered["presentation"]["chapters"]["chapter01"]["hotMaterialPanel"]
        self.assertEqual(panel["materials"][0]["materialName"], "爆款真实名称")
        self.assertNotIn('"m-2"', json.dumps(rendered["presentation"]["chapters"]["chapter01"], ensure_ascii=False))
        self.assertIn("function renderHotMaterialPanel", html)
        self.assertIn("hotMaterialPanel", html)
        self.assertIn("chapter01?.aiConclusion", html)
        self.assertIn("navigateToMaterial(materialId)", html)

    def test_projects_new_chapter_contracts_with_stable_material_references(self):
        report = build_presentation_report(complete_presentation_data())
        self.assertEqual(report["report"]["sections"], [f"chapter0{index}" for index in range(1, 6)])
        self.assertNotIn("chapter06", report["chapters"])
        chapter01 = report["chapters"]["chapter01"]
        self.assertEqual(chapter01["title"], "素材榜单数据洞察")
        self.assertNotIn("tabs", chapter01)
        self.assertEqual(
            [point["id"] for point in chapter01["points"]],
            ["commercial"],
        )
        self.assertEqual(chapter01["points"][0]["leadIn"], "")
        self.assertEqual(chapter01["points"][0]["evidenceRefs"], ["commercial:m-1"])
        self.assertNotIn("batchInsights", report["chapters"]["chapter01"])
        self.assertEqual(
            [item["title"] for item in report["chapters"]["chapter02"]["insights"]],
            ["展现结构：生活场景扩大覆盖", "点击结构：问题前置更值得验证", "成交结构：价值解释需要匹配"],
        )
        bucket = report["chapters"]["chapter02"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"]["buckets"][0]
        self.assertEqual(bucket["sourceMaterialIds"], ["m-1"])
        self.assertEqual(bucket["evidenceRefs"], ["audience:CLICK_COUNT:age:24-30"])
        self.assertEqual(
            report["chapters"]["chapter02"]["preferencesByMetric"]["CLICK_COUNT"][0]["topMaterials"][0]["materialId"],
            "m-1",
        )
        self.assertNotIn("preferences", report["chapters"]["chapter02"])
        projected_candidate = report["chapters"]["chapter02"]["preferencesByMetric"]["CLICK_COUNT"][0]["topMaterials"][0]
        self.assertTrue(set(projected_candidate).issubset({"rank", "materialId", "materialName", "audiencePercentage", "totalCost"}))
        self.assertNotIn("preferenceLift", projected_candidate)

    def test_projects_chapter02_metrics_without_unused_dimension_metadata(self):
        data = complete_presentation_data()
        data["audience"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"].update({
            "dimensionKey": "age",
            "dimensionName": "年龄",
            "totalMetricValue": 12,
            "missingMaterialIds": ["m-2"],
        })
        report = build_presentation_report(data)
        dimension = report["chapters"]["chapter02"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"]
        self.assertNotIn("dimensionKey", dimension)
        self.assertNotIn("dimensionName", dimension)
        self.assertNotIn("totalMetricValue", dimension)
        self.assertNotIn("missingMaterialIds", report["chapters"]["chapter02"]["metrics"]["CLICK_COUNT"])
        group = report["chapters"]["chapter03"]["groups"][0]
        self.assertEqual((group["level1"], group["labels"][0]["level2"]), ("商品卖点", "适用场景"))
        self.assertEqual(group["labels"][0]["evidenceRefs"], ["script:m-1:商品卖点:适用场景"])
        chapter03 = report["chapters"]["chapter03"]
        self.assertEqual(chapter03["source"], "facts.scripts + ai.batchInsights[script]")
        self.assertEqual(group["source"], "facts.scripts.labelSummary")
        self.assertEqual(
            [item["title"] for item in chapter03["insights"]],
            ["一二级内容公式：原始 AI 标题"],
        )
        self.assertEqual(chapter03["insights"][0]["source"], "ai.batchInsights[script]")
        self.assertNotIn("summary", chapter03["insights"][0])
        chapter04 = report["chapters"]["chapter04"]
        self.assertEqual(chapter04["source"], "facts.materials.highlightFrames + ai.highlightDashboard")
        self.assertEqual(chapter04["conclusion"]["source"], "ai.portfolioDimensionConclusions.highlight")
        self.assertEqual(chapter04["conclusion"]["evidenceRefs"], ["highlight:m-1"])
        chapter05 = report["chapters"]["chapter05"]
        self.assertEqual(chapter05["source"], "facts.chapter05 + ai.materialAnalyses")
        self.assertEqual(chapter05["materials"][0]["analysis"]["source"], "ai.materialAnalyses")
        self.assertNotIn("nextActions", report["chapters"])

    def test_validation_rejects_invalid_projected_references_and_priorities(self):
        report = build_presentation_report(complete_presentation_data())
        report = build_presentation_report(complete_presentation_data())
        report["chapters"]["chapter02"]["metrics"]["CLICK_COUNT"]["dimensions"]["age"]["buckets"][0]["sourceMaterialIds"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "materialId"):
            validate_presentation_report(report)
        report = build_presentation_report(complete_presentation_data())
        report["chapters"]["chapter03"]["groups"][0]["labels"][0]["materialIds"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "materialId"):
            validate_presentation_report(report)

    def test_missing_new_chapter_data_stays_renderable(self):
        report = build_presentation_report(sample_data())
        for chapter in ("chapter01", "chapter02", "chapter03"):
            self.assertIn(report["chapters"][chapter]["availability"], ("partial", "unavailable"))
        self.assertEqual(report["chapters"]["chapter02"]["metrics"], {})
        self.assertEqual(report["chapters"]["chapter03"]["groups"], [])
        self.assertEqual(list(report["chapters"]), ["chapter01", "chapter02", "chapter03", "chapter04", "chapter05"])

    def test_projects_chapters_with_stable_refs_and_tabs(self):
        report = build_presentation_report(sample_data())
        self.assertEqual(report["presentationSchemaVersion"], 1)
        self.assertEqual(list(report["chapters"]["chapter04"]["groups"]), ["portrait", "product", "other"])
        self.assertEqual(report["chapters"]["chapter04"]["groups"]["portrait"]["frameRefs"], ["highlight:m-1:peak_top_1"])
        self.assertNotIn("frames", report["chapters"]["chapter04"]["groups"]["portrait"])
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

    def test_missing_highlight_reference_degrades_only_its_category(self):
        data = sample_data()
        data["conclusion"]["highlightDashboard"]["groups"][0]["frameRefs"].append("highlight:m-1:missing")
        report = build_presentation_report(data)
        group = report["chapters"]["chapter04"]["groups"]["portrait"]
        self.assertEqual(group["availability"], "partial")
        self.assertEqual(report["chapters"]["chapter04"]["availability"], "partial")

    def test_missing_frame_path_is_not_available(self):
        data = sample_data()
        data["materials"][0]["highlightFrames"][0]["framePath"] = ""
        report = build_presentation_report(data)
        self.assertEqual(report["chapters"]["chapter04"]["availability"], "unavailable")

    def test_validation_rejects_highlight_frame_assigned_to_multiple_categories(self):
        report = build_presentation_report(sample_data())
        report["chapters"]["chapter04"]["groups"]["product"]["frameRefs"] = ["highlight:m-1:peak_top_1"]
        with self.assertRaisesRegex(ValueError, "frameRef"):
            validate_presentation_report(report)

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

    def test_presentation_preserves_optional_material_ranking_facts(self):
        data = sample_data()
        data["materials"][0]["materialRanking"] = {"roi": 1.8, "cost": 100}
        report = build_presentation_report(data)
        self.assertEqual(
            report["chapters"]["chapter05"]["materials"][0]["materialRanking"],
            {"roi": 1.8, "cost": 100},
        )

    def test_chapter05_does_not_duplicate_material_evidence_refs(self):
        data = complete_presentation_data()
        report = build_presentation_report(data)
        material = report["chapters"]["chapter05"]["materials"][0]
        self.assertNotIn("evidenceRefs", material)
        self.assertEqual(material["analysis"]["evidenceRefs"], ["material:m-1"])

    def test_chapter05_prefers_canonical_material_fact_package(self):
        data = sample_data()
        data["materials"][0]["materialAudience"] = {"legacy": True}
        data["chapter05Facts"] = {
            "materials": [
                {
                    "materialId": "m-1",
                    "audience": {"metrics": {"SHOW_COUNT": {"canonical": True}}},
                    "script": {"text": "canonical script"},
                    "highlight": {"frames": data["materials"][0]["highlightFrames"]},
                    "interactionTimeline": {"metrics": []},
                }
            ]
        }
        report = build_presentation_report(data)
        material = report["chapters"]["chapter05"]["materials"][0]
        self.assertTrue(material["audience"]["metrics"]["SHOW_COUNT"]["canonical"])
        self.assertEqual(material["script"]["text"], "canonical script")

    def test_chapter05_keeps_script_formula_from_legacy_facts_when_canonical_labels_only(self):
        data = sample_data()
        data["chapter05Facts"] = {
            "materials": [{
                "materialId": "m-1",
                "script": {
                    "text": "canonical script",
                    "labels": [{"level1": "商品卖点", "level2": "适用场景"}],
                    "evidence": [{"level2": "适用场景", "sentence": "早餐场景口播"}],
                },
            }]
        }
        report = build_presentation_report(data)
        script = report["chapters"]["chapter05"]["materials"][0]["script"]
        self.assertEqual(script["formula"], [{"level1": "利益点", "level2": "高蛋白"}])

    def test_renderer_removes_script_display_duplicates_for_v1_presentation(self):
        data = complete_presentation_data()
        data["displayFacts"] = {
            "schemaVersion": 1,
            "sourceFactsSchemaVersion": 1,
            "scripts": data["scripts"],
            "audience": data["audience"],
        }
        data["presentation"] = build_presentation_report(data)

        rendered = report_data_for_template(data, None)

        self.assertNotIn("scripts", rendered)
        self.assertNotIn("scripts", rendered["displayFacts"])
        self.assertEqual(set(rendered["materials"][0]), {"rank", "materialId", "materialName"})
        self.assertNotIn("highlightFrames", rendered["materials"][0])
        self.assertIn("metrics", rendered["displayFacts"]["audience"])
        self.assertIn("preferencesByMetric", rendered["displayFacts"]["audience"])
        self.assertEqual(set(rendered["displayFacts"]), {"audience"})

    def test_renderer_keeps_only_audience_display_facts_for_v1_presentation(self):
        data = complete_presentation_data()
        data["displayFacts"] = {
            "schemaVersion": 1,
            "sourceFactsSchemaVersion": 1,
            "period": data["period"],
            "portfolio": data.get("portfolio", {}),
            "materials": [{"rank": 1, "materialId": "m-1", "materialName": "素材一"}],
            "audience": data["audience"],
            "interaction": {"metrics": {}},
            "commercial": {"metrics": {}},
            "topMaterialRoi": data["topMaterialRoi"],
        }
        data["presentation"] = build_presentation_report(data)
        rendered = report_data_for_template(data, None)
        self.assertEqual(set(rendered["displayFacts"]), {"audience"})

    def test_renderer_reduces_display_materials_when_chapter05_is_available(self):
        data = complete_presentation_data()
        data["displayFacts"] = {
            "materials": [
                {
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "rank": 1,
                    "materialAudience": {"metrics": {"SHOW_COUNT": {}}},
                    "interactionTimeline": {"metrics": [{"metric": "CLICK_COUNT"}]},
                    "scriptAnalysis": {"text": "重复事实"},
                    "video": {"src": "video"},
                }
            ],
            "audience": {"metrics": {}},
            "scripts": {"labelSummary": []},
        }
        data["presentation"] = build_presentation_report(data)
        rendered = report_data_for_template(data, None)
        self.assertEqual(set(rendered["displayFacts"]), {"audience"})

    def test_renderer_separates_materials_when_chapter02_is_unavailable(self):
        data = sample_data()
        data["displayFacts"] = {
            "materials": [
                {
                    "materialId": "m-1",
                    "materialName": "素材一",
                    "rank": 1,
                    "materialAudience": {"metrics": {}},
                    "interactionTimeline": {"metrics": []},
                    "scriptAnalysis": {"text": "重复事实"},
                }
            ],
            "audience": {"metrics": {}},
        }
        data["presentation"] = build_presentation_report(data)
        rendered = report_data_for_template(data, None)
        self.assertEqual(
            set(rendered["materials"][0]), {"rank", "materialId", "materialName"}
        )
        self.assertEqual(set(rendered["displayFacts"]), {"audience"})

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
        material = rendered["presentation"]["chapters"]["chapter05"]["materials"][0]
        group = rendered["presentation"]["chapters"]["chapter04"]["groups"]["portrait"]
        self.assertNotIn("frames", group)
        self.assertEqual(group["frameRefs"], ["highlight:m-1:peak_top_1"])
        self.assertEqual(material["highlight"]["frames"][0]["framePath"], "assets/highlight-frames/m-1/peak.jpg")
        self.assertEqual(material["video"]["src"], "https://video.example/m-1.mp4")

    def test_template_passes_presentation_video_to_material_workspace(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(encoding="utf-8")
        self.assertIn("video: item.video || {},", template)
        self.assertIn("materialRanking: item.materialRanking || {},", template)

    def test_template_prefers_presentation_chapters_with_legacy_fallback(self):
        template = (ROOT / "templates" / "final-report-template.html").read_text(encoding="utf-8")
        self.assertIn("function presentationChapter(id)", template)
        for chapter in ("chapter01", "chapter02", "chapter03"):
            self.assertIn(f'presentationChapter("{chapter}")', template)


if __name__ == "__main__":
    unittest.main()
