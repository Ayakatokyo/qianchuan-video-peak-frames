import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from top_material_roi_lib import (  # noqa: E402
    TopMaterialRoiError,
    build_top_material_roi_payload,
    write_top_material_roi_facts,
)


def asset_facts(materials):
    return {
        "schemaVersion": 1,
        "actualStartDate": "2026-08-01",
        "actualEndDate": "2026-08-02",
        "filters": {"advertiserId": "123"},
        "metricKeys": {
            "cost": "stat_cost_for_overall_roi2",
            "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
            "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
        },
        "materials": materials,
    }


class TopMaterialRoiTests(unittest.TestCase):
    def test_calculates_both_roi_values_after_summing_ready_materials(self):
        payload = build_top_material_roi_payload(
            asset_facts({
                "m-1": {
                    "stat_cost_for_overall_roi2": "10",
                    "total_order_settle_amount_for_roi2_1h": "20",
                    "total_pay_order_gmv_include_coupon_for_roi2": "30",
                },
                "m-2": {
                    "stat_cost_for_overall_roi2": "90",
                    "total_order_settle_amount_for_roi2_1h": "90",
                    "total_pay_order_gmv_include_coupon_for_roi2": "180",
                },
            }),
            {"materials": [{"materialId": "m-1"}, {"materialId": "m-2"}]},
        )

        self.assertEqual(payload["materialIds"], ["m-1", "m-2"])
        self.assertEqual(payload["totals"], {
            "cost": "100",
            "netSettlementAmount": "110",
            "overallPaymentAmount": "210",
        })
        self.assertEqual(payload["metrics"], [
            {
                "key": "netSettlementRoi",
                "label": "净成交 ROI",
                "formula": "sum(total_order_settle_amount_for_roi2_1h) / sum(stat_cost_for_overall_roi2)",
                "value": "1.1",
                "available": True,
            },
            {
                "key": "overallPaymentRoi",
                "label": "整体支付 ROI",
                "formula": "sum(total_pay_order_gmv_include_coupon_for_roi2) / sum(stat_cost_for_overall_roi2)",
                "value": "2.1",
                "available": True,
            },
        ])
        self.assertNotEqual(payload["metrics"][0]["value"], "1.5")

    def test_missing_ready_material_facts_is_a_contract_error(self):
        with self.assertRaisesRegex(TopMaterialRoiError, "m-2"):
            build_top_material_roi_payload(
                asset_facts({"m-1": {
                    "stat_cost_for_overall_roi2": "1",
                    "total_order_settle_amount_for_roi2_1h": "1",
                    "total_pay_order_gmv_include_coupon_for_roi2": "1",
                }}),
                {"materials": [{"materialId": "m-2"}]},
            )

    def test_zero_cost_marks_both_metrics_unavailable(self):
        payload = build_top_material_roi_payload(
            asset_facts({"m-1": {
                "stat_cost_for_overall_roi2": "0",
                "total_order_settle_amount_for_roi2_1h": "1",
                "total_pay_order_gmv_include_coupon_for_roi2": "2",
            }}),
            {"materials": [{"materialId": "m-1"}]},
        )

        self.assertEqual(
            [(metric["available"], metric["value"]) for metric in payload["metrics"]],
            [(False, None), (False, None)],
        )

    def test_writes_facts_from_verified_ready_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_path = root / "asset-material-facts-v1.json"
            ready_path = root / "top_materials_rpa_ready.json"
            output_path = root / "top-material-roi-facts-v1.json"
            asset_path.write_text(json.dumps(asset_facts({"m-1": {
                "stat_cost_for_overall_roi2": "2",
                "total_order_settle_amount_for_roi2_1h": "3",
                "total_pay_order_gmv_include_coupon_for_roi2": "4",
            }})), encoding="utf-8")
            ready_path.write_text(json.dumps({"materials": [{"materialId": "m-1"}]}), encoding="utf-8")

            result = write_top_material_roi_facts(asset_path, ready_path, output_path)

            self.assertEqual(result["metrics"][0]["value"], "1.5")
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), result)


if __name__ == "__main__":
    unittest.main()
