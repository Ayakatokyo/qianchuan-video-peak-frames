import json
import re
from decimal import Decimal
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_metrics_lib import (  # noqa: E402
    AssetMetricsError,
    aggregate_asset_metrics,
    build_asset_facts_payload,
)


def origin_row(material_id, cost, net_settlement, overall_payment):
    return json.dumps({
        "dimensions": {
            "material_id": {"Value": material_id},
            "roi2_material_video_name": {"ValueStr": "素材 %s" % material_id},
            "roi2_material_type_v3": {"ValueStr": "短视频"},
            "roi2_material_video_type": {"ValueStr": "素材视频"},
        },
        "metrics": {
            "stat_cost_for_overall_roi2": {"Value": cost},
            "total_order_settle_amount_for_roi2_1h": {"Value": net_settlement},
            "total_pay_order_gmv_include_coupon_for_roi2": {"Value": overall_payment},
        },
    }, ensure_ascii=False)


class AssetMetricsTests(unittest.TestCase):
    def test_aggregates_atomic_metrics_per_material_independent_of_input_order(self):
        rows = [
            origin_row("m-2", "20.10", "45.40", "50.60"),
            origin_row("m-1", "1.25", "2.50", "3.75"),
            origin_row("m-1", "10.75", "21.50", "32.25"),
        ]

        facts = aggregate_asset_metrics(rows)
        reversed_facts = aggregate_asset_metrics(list(reversed(rows)))

        self.assertEqual(facts, reversed_facts)
        self.assertEqual(facts["m-1"], {
            "stat_cost_for_overall_roi2": Decimal("12.00"),
            "total_order_settle_amount_for_roi2_1h": Decimal("24.00"),
            "total_pay_order_gmv_include_coupon_for_roi2": Decimal("36.00"),
        })
        self.assertEqual(facts["m-2"]["stat_cost_for_overall_roi2"], Decimal("20.10"))

    def test_preserves_confirmed_swapped_name_metric_keys_in_artifact(self):
        payload = build_asset_facts_payload(
            "2026-08-01", "2026-08-02", None, (), "123", {
                "m-1": {
                    "stat_cost_for_overall_roi2": Decimal("10"),
                    "total_order_settle_amount_for_roi2_1h": Decimal("17"),
                    "total_pay_order_gmv_include_coupon_for_roi2": Decimal("19"),
                }
            },
        )

        self.assertEqual(payload["metricKeys"], {
            "cost": "stat_cost_for_overall_roi2",
            "netSettlementAmount": "total_order_settle_amount_for_roi2_1h",
            "overallPaymentAmount": "total_pay_order_gmv_include_coupon_for_roi2",
        })
        self.assertEqual(
            payload["materials"]["m-1"]["total_order_settle_amount_for_roi2_1h"], "17"
        )
        self.assertEqual(
            payload["materials"]["m-1"]["total_pay_order_gmv_include_coupon_for_roi2"], "19"
        )

    def test_rejects_missing_invalid_and_negative_required_metrics(self):
        missing = json.dumps({"dimensions": {"material_id": {"Value": "m-1"}}, "metrics": {}})
        non_finite = origin_row("m-1", "NaN", "1", "1")
        negative = origin_row("m-1", "1", "-1", "1")

        for row in (missing, non_finite, negative):
            with self.assertRaises(AssetMetricsError):
                aggregate_asset_metrics([row])

    def test_ignores_off_filter_rows_before_requiring_roi_metrics(self):
        excluded = json.dumps({
            "dimensions": {
                "material_id": {"Value": "excluded"},
                "roi2_material_video_name": {"ValueStr": "不在范围"},
                "roi2_material_type_v3": {"ValueStr": "短视频"},
            },
            "metrics": {"stat_cost_for_overall_roi2": {"Value": "1"}},
        }, ensure_ascii=False)

        facts = aggregate_asset_metrics(
            [excluded, origin_row("m-1", "2", "3", "4")],
            name_pattern=re.compile("素材"),
        )

        self.assertEqual(list(facts), ["m-1"])


if __name__ == "__main__":
    unittest.main()
