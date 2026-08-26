import json
import re
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from top_materials_lib import (  # noqa: E402
    MaterialAggregator,
    MaterialDataError,
    build_output,
    select_materials,
)
from export_top_materials import main  # noqa: E402


def origin_data(
    material_id="1",
    name="Winter campaign",
    material_type="live_video",
    video_type="portrait",
    cost="1",
    net_settlement="1",
    overall_payment="1",
):
    return json.dumps(
        {
            "dimensions": {
                "material_id": {"Value": material_id},
                "roi2_material_video_name": {"ValueStr": name},
                "roi2_material_type_v3": {"ValueStr": material_type},
                "roi2_material_video_type": {"ValueStr": video_type},
            },
            "metrics": {
                "stat_cost_for_overall_roi2": {"Value": cost},
                "total_order_settle_amount_for_roi2_1h": {"Value": net_settlement},
                "total_pay_order_gmv_include_coupon_for_roi2": {"Value": overall_payment},
            },
        }
    )


class MaterialSelectionTests(unittest.TestCase):
    def test_aggregates_costs_and_orders_ties_by_material_id(self):
        materials, excluded = select_materials(
            [
                origin_data(material_id="2", cost="9.50"),
                origin_data(material_id="1", cost="5"),
                origin_data(material_id="1", cost="4.50"),
            ],
            name_pattern=None,
            material_types=[],
            top_n=50,
        )

        self.assertEqual([material.material_id for material in materials], ["1", "2"])
        self.assertEqual(
            [material.total_cost for material in materials],
            [Decimal("9.50"), Decimal("9.50")],
        )
        self.assertEqual(excluded, [])

    def test_filters_by_name_and_exact_material_type_before_excluding_aigc(self):
        materials, excluded = select_materials(
            [
                origin_data(material_id="-2", name="Brand launch", cost="7"),
                origin_data(material_id="1", name="Brand launch", cost="3"),
                origin_data(material_id="2", name="Different campaign", cost="8"),
                origin_data(
                    material_id="3",
                    name="Brand launch",
                    material_type="image",
                    cost="9",
                ),
            ],
            name_pattern=re.compile(r"Brand"),
            material_types=["live_video"],
            top_n=50,
        )

        self.assertEqual([material.material_id for material in materials], ["1"])
        self.assertEqual(excluded[0].material_id, "-2")
        self.assertEqual(excluded[0].source_row_count, 1)
        self.assertEqual(excluded[0].total_cost, Decimal("7"))
        self.assertEqual(
            excluded[0].reason,
            "AIGC dynamic creative collection cannot map to a single-material diagnosis RPA",
        )

    def test_rejects_malformed_paths_and_nonfinite_or_negative_costs(self):
        malformed = json.dumps({"dimensions": {}, "metrics": {}})
        invalid_costs = ["NaN", "Infinity", "-1"]

        with self.assertRaises(MaterialDataError):
            select_materials(["not valid json"], None, [], 50)

        with self.assertRaises(MaterialDataError):
            select_materials([malformed], None, [], 50)

        for cost in invalid_costs:
            with self.subTest(cost=cost):
                with self.assertRaises(MaterialDataError):
                    select_materials([origin_data(cost=cost)], None, [], 50)

    def test_aggregates_conflicting_metadata_and_selects_dominant_variant(self):
        materials, excluded = select_materials(
            [
                origin_data(material_id="1", name="First name", cost="7"),
                origin_data(material_id="1", name="Second name", cost="4"),
                origin_data(material_id="1", name="Second name", cost="5"),
            ],
            None,
            [],
            50,
        )

        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0].material_id, "1")
        self.assertEqual(materials[0].material_name, "Second name")
        self.assertEqual(materials[0].total_cost, Decimal("16"))
        self.assertEqual(excluded, [])

    def test_streaming_aggregator_matches_selection_without_retaining_rows(self):
        rows = [
            origin_data(material_id="1", name="First name", cost="7"),
            origin_data(material_id="2", name="Second name", cost="13"),
            origin_data(material_id="1", name="First name", cost="6"),
            origin_data(material_id="-2", cost="5"),
        ]
        aggregator = MaterialAggregator(
            name_pattern=None,
            material_types=[],
            top_n=50,
        )

        for row in iter(rows):
            aggregator.add_raw_origin_data(row)
        streamed_materials, streamed_excluded = aggregator.select()
        selected_materials, selected_excluded = select_materials(
            rows,
            name_pattern=None,
            material_types=[],
            top_n=50,
        )

        self.assertEqual(streamed_materials, selected_materials)
        self.assertEqual(streamed_excluded, selected_excluded)

    def test_rejects_when_no_eligible_materials_remain(self):
        with self.assertRaisesRegex(MaterialDataError, "eligible"):
            select_materials(
                [origin_data(material_id="1", name="Not selected")],
                re.compile(r"Selected"),
                [],
                50,
            )

    def test_build_output_repeats_contract_fields_and_assigns_ranks(self):
        materials, excluded = select_materials(
            [
                origin_data(material_id="1", name=" First ", cost="5"),
                origin_data(material_id="2", name=" Second ", cost="9"),
                origin_data(material_id="-2", cost="7"),
            ],
            name_pattern=re.compile(r"First|Second|Winter"),
            material_types=["live_video"],
            top_n=50,
        )

        output = build_output(
            actual_start_date="2026-05-01",
            actual_end_date="2026-05-05",
            name_pattern=re.compile(r"First|Second|Winter"),
            material_types=["live_video"],
            advertiser_id="1765875532",
            materials=materials,
            excluded=excluded,
        )

        self.assertEqual(
            output,
            {
                "actualStartDate": "2026-05-01",
                "actualEndDate": "2026-05-05",
                "materialCount": 2,
                "rankingMetric": "stat_cost_for_overall_roi2",
                "filters": {
                    "materialNameRegex": "First|Second|Winter",
                    "materialTypes": ["live_video"],
                    "advertiserId": "1765875532",
                },
                "excluded": [
                    {
                        "materialId": "-2",
                        "reason": "AIGC dynamic creative collection cannot map to a single-material diagnosis RPA",
                        "sourceRowCount": 1,
                        "totalCost": 7.0,
                    }
                ],
                "skippedSourceRecordCount": 0,
                "skippedSourceRecords": [],
                "materials": [
                    {
                        "actualStartDate": "2026-05-01",
                        "actualEndDate": "2026-05-05",
                        "rankingMetric": "stat_cost_for_overall_roi2",
                        "rank": 1,
                        "materialId": "2",
                        "materialName": "Second",
                        "materialType": "live_video",
                        "videoType": "portrait",
                        "totalCost": 9.0,
                    },
                    {
                        "actualStartDate": "2026-05-01",
                        "actualEndDate": "2026-05-05",
                        "rankingMetric": "stat_cost_for_overall_roi2",
                        "rank": 2,
                        "materialId": "1",
                        "materialName": "First",
                        "materialType": "live_video",
                        "videoType": "portrait",
                        "totalCost": 5.0,
                    },
                ],
            },
        )

class ExportCliTests(unittest.TestCase):
    def test_exports_from_one_report_query_and_preserves_rank_and_facts(self):
        records = [
            json.loads(origin_data("m-1", cost="3", net_settlement="4", overall_payment="5")),
            json.loads(origin_data("m-1", cost="7", net_settlement="8", overall_payment="9")),
            json.loads(origin_data("m-2", cost="5", net_settlement="6", overall_payment="7")),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "top_materials.json"
            facts_output = root / "asset-material-facts-v1.json"
            with patch(
                "export_top_materials.fetch_material_report", return_value=records
            ) as fetch:
                result = main([
                    "--shop-id", "shop-1",
                    "--advertiser-id", "1765875532",
                    "--start-date", "2026-05-01",
                    "--end-date", "2026-05-07",
                    "--top-n", "1",
                    "--output", str(output),
                    "--asset-facts-output", str(facts_output),
                    "--source-output-dir", str(root / "source-data"),
                ])

            self.assertEqual(result, 0)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(
                fetch.call_args.args[:4],
                ("shop-1", "1765875532", "2026-05-01", "2026-05-07"),
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["materials"][0]["materialId"],
                "m-1",
            )
            self.assertEqual(json.loads(facts_output.read_text(encoding="utf-8"))["materials"]["m-1"], {
                "stat_cost_for_overall_roi2": "10",
                "total_order_settle_amount_for_roi2_1h": "12",
                "total_pay_order_gmv_include_coupon_for_roi2": "14",
            })

    def test_does_not_publish_when_report_query_fails(self):
        from qianchuan_report_client import ReportClientError

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "top_materials.json"
            with patch(
                "export_top_materials.fetch_material_report",
                side_effect=ReportClientError("unavailable"),
            ):
                result = main([
                    "--shop-id", "shop-1",
                    "--advertiser-id", "1765875532",
                    "--start-date", "2026-05-01",
                    "--end-date", "2026-05-07",
                    "--output", str(output),
                    "--source-output-dir", str(root / "source-data"),
                ])

        self.assertEqual(result, 1)
        self.assertFalse(output.exists())

    def test_skips_incomplete_source_records_and_exports_eligible_materials(self):
        valid = json.loads(origin_data("m-1", cost="7"))
        incomplete = json.loads(origin_data("m-bad", cost="99"))
        del incomplete["dimensions"]["roi2_material_video_name"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "top_materials.json"
            facts_output = root / "asset-material-facts-v1.json"
            with patch(
                "export_top_materials.fetch_material_report",
                return_value=[valid, incomplete],
            ):
                result = main([
                    "--shop-id", "shop-1",
                    "--advertiser-id", "1765875532",
                    "--start-date", "2026-05-01",
                    "--end-date", "2026-05-07",
                    "--output", str(output),
                    "--asset-facts-output", str(facts_output),
                    "--source-output-dir", str(root / "source-data"),
                ])

            payload = json.loads(output.read_text(encoding="utf-8"))
            facts_materials = list(
                json.loads(facts_output.read_text(encoding="utf-8"))["materials"]
            )

        self.assertEqual(result, 0)
        self.assertEqual([item["materialId"] for item in payload["materials"]], ["m-1"])
        self.assertEqual(payload["skippedSourceRecordCount"], 1)
        self.assertEqual(payload["skippedSourceRecords"][0]["sourceRowIndex"], 1)
        self.assertEqual(payload["skippedSourceRecords"][0]["materialId"], "m-bad")
        self.assertIn("roi2_material_video_name", payload["skippedSourceRecords"][0]["reason"])
        self.assertEqual(facts_materials, ["m-1"])


if __name__ == "__main__":
    unittest.main()
