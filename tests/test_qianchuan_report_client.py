import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qianchuan_report_client as client  # noqa: E402


def material_record(material_id="m-1"):
    dimensions = {
        field: {"Value": field, "ValueStr": field}
        for field in client.DEFAULT_DIMENSIONS
    }
    dimensions["material_id"] = {"Value": material_id, "ValueStr": material_id}
    return {
        "dimensions": dimensions,
        "metrics": {
            field: {"Value": 1, "ValueStr": "1"}
            for field in client.DEFAULT_METRICS
        },
    }


def valid_env(**overrides):
    environment = {
        "ENV_BACKEND_HOST": "http://gateway.test/api",
        "YUCE_AUTHORIZATION": "authorization-token",
        "YUCE_SESSION_ID": "agent-session",
    }
    environment.update(overrides)
    return environment


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, content=b""):
        self.payload = payload
        self.status_code = status_code
        self.content = content
        self.text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = client.requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.payload

    def iter_content(self, chunk_size=1024 * 1024):
        del chunk_size
        yield self.content

    def close(self):
        return None


class FakeSession:
    def __init__(self, post_response, get_response=None):
        self.post_response = post_response
        self.get_response = get_response
        self.post_call = None
        self.get_call = None

    def post(self, *args, **kwargs):
        self.post_call = {"args": args, **kwargs}
        return self.post_response

    def get(self, *args, **kwargs):
        self.get_call = {"args": args, **kwargs}
        return self.get_response


class QianchuanReportClientTests(unittest.TestCase):
    def test_lists_api_advertiser_authorizations_without_fetching_report(self):
        rows = [{"shop_id": "shop-1", "advertiser_id": "123", "account_name": "account"}]
        session = FakeSession(FakeResponse({"success": True, "data": rows}))

        result = client.list_authorized_advertisers(environ=valid_env(), session=session)

        self.assertEqual(result, rows)
        self.assertEqual(
            session.post_call["args"][0],
            "http://gateway.test/api/adg/v1/agent/api-platform/shops/advertisers",
        )
        self.assertEqual(
            session.post_call["json"],
            {"authorization": "authorization-token", "platform": "OCEANENGINE"},
        )
        self.assertIsNone(session.get_call)

    def test_default_query_matches_reference_material_report_contract(self):
        self.assertEqual(
            client.DEFAULT_DIMENSIONS,
            [
                "ecp_app_id",
                "roi2_material_type_v3",
                "roi2_material_video_play_info",
                "roi2_material_video_name",
                "roi2_material_video_type",
                "material_create_time_v2",
                "material_id",
                "anchor_id",
            ],
        )
        self.assertEqual(len(client.DEFAULT_METRICS), 102)
        self.assertEqual(len(client.DEFAULT_METRICS), len(set(client.DEFAULT_METRICS)))
        self.assertEqual(
            hashlib.sha256("\0".join(client.DEFAULT_METRICS).encode()).hexdigest(),
            "ef5af9efb7c10495f8d0ab9476df4655c976e2745ecbbc2ca9987a8a68980aae",
        )
        for field in (
            "stat_cost_for_overall_roi2",
            "total_prepay_and_pay_settle_overall_roi2_1h",
            "total_cost_per_pay_order_settle_for_overall_roi2_1h",
            "total_prepay_and_pay_settle_roi2_1h",
            "live_show_count_for_roi2_v2",
            "video_play_finish_rate_for_roi2_v2",
        ):
            self.assertIn(field, client.DEFAULT_METRICS)
        for field in (
            "roi2_material_advice",
            "roi2_material_advice_reason",
            "stat_time_day",
            "range_stat_time_hour",
        ):
            self.assertNotIn(field, client.DEFAULT_DIMENSIONS)
        self.assertIn(
            "additional_delivery_total_refund_order_count_for_roi2_1h_assist",
            client.DEFAULT_METRICS,
        )
        self.assertNotIn(
            "additional_delivery_total_refund_order_count_for_roi2_assist",
            client.DEFAULT_METRICS,
        )
        self.assertEqual(client.DEFAULT_FILTERS, [
            {"field": "ecp_app_id", "operator": 7, "values": ["1", "2"]},
            {"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]},
        ])
        self.assertEqual(
            client.DEFAULT_ORDER_BY,
            [{"field": "stat_cost_for_roi2", "type": 2}],
        )

    def test_fetch_sends_connector_contract_with_independent_sessions(self):
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "payload",
                "result_content": {"payload": [material_record()]},
            },
        })
        session = FakeSession(response)

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            valid_env(YUCE_SESSION_ID="agent-session", YCSESSIONID="cookie-session"),
            clear=True,
        ):
            records = client.fetch_material_report(
                "shop-1",
                "1770096415226951",
                "2026-08-01",
                "2026-08-07",
                output_dir=Path(temp_dir),
                session=session,
            )

        self.assertEqual(records, [material_record()])
        payload = session.post_call["json"]
        self.assertEqual(payload["function_code"], client.FUNCTION_CODE)
        self.assertEqual(payload["platform"], "OCEANENGINE")
        self.assertEqual(payload["agent_session_id"], "agent-session")
        self.assertEqual(session.post_call["cookies"], {"YCSESSIONID": "cookie-session"})
        self.assertEqual(payload["business_params"]["advertiserId"], 1770096415226951)
        self.assertEqual(payload["business_params"]["startTime"], "2026-08-01 00:00:00")
        self.assertEqual(payload["business_params"]["endTime"], "2026-08-07 23:59:59")

    def test_fetch_validates_full_contract_and_writes_field_manifest(self):
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "payload",
                "result_content": {"payload": [material_record()]},
            },
        })
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            valid_env(YUCE_SESSION_ID="agent-secret", YCSESSIONID="cookie-secret"),
            clear=True,
        ):
            root = Path(temp_dir)
            records = client.fetch_material_report(
                "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                output_dir=root, session=FakeSession(response),
            )
            manifest = json.loads(
                (root / "field-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(records, [material_record()])
        self.assertEqual(manifest["dimensions"], client.DEFAULT_DIMENSIONS)
        self.assertEqual(manifest["metrics"], client.DEFAULT_METRICS)
        self.assertEqual(manifest["recordCount"], 1)
        self.assertEqual(manifest["startTime"], "2026-08-01 00:00:00")
        self.assertEqual(manifest["endTime"], "2026-08-07 23:59:59")
        serialized_manifest = json.dumps(manifest)
        for secret in ("authorization-token", "agent-secret", "cookie-secret"):
            self.assertNotIn(secret, serialized_manifest)

    def test_fetch_rejects_a_nonempty_record_missing_a_contract_field(self):
        record = material_record()
        del record["metrics"]["video_play_finish_rate_for_roi2_v2"]
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "payload",
                "result_content": {"payload": [record]},
            },
        })
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, valid_env(), clear=True
        ):
            with self.assertRaisesRegex(
                client.ReportClientError, "missing expected fields"
            ) as caught:
                client.fetch_material_report(
                    "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                    output_dir=Path(temp_dir), session=FakeSession(response),
                )

        self.assertIn("video_play_finish_rate_for_roi2_v2", str(caught.exception))
        self.assertNotIn("authorization-token", str(caught.exception))

    def test_fetch_allows_a_record_missing_selection_dimensions(self):
        record = material_record()
        del record["dimensions"]["roi2_material_video_name"]
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "payload",
                "result_content": {"payload": [record]},
            },
        })
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, valid_env(), clear=True
        ):
            records = client.fetch_material_report(
                "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                output_dir=Path(temp_dir), session=FakeSession(response),
            )

        self.assertEqual(records, [record])

    def test_files_response_downloads_and_returns_structured_records(self):
        record = material_record()
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "files",
                "result_content": {
                    "status": "COMPLETED",
                    "failedInstances": 0,
                    "files": [{"fileUrl": "http://gateway.test/files/report.csv"}],
                },
            },
        })
        csv_content = (
            '"' + json.dumps(record, ensure_ascii=False).replace('"', '""') + '"\r\n'
        ).encode("utf-8")
        session = FakeSession(
            response,
            FakeResponse(content=csv_content),
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, valid_env(), clear=True
        ):
            root = Path(temp_dir)
            records = client.fetch_material_report(
                "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                output_dir=root, session=session,
            )

            self.assertEqual(records, [record])
            self.assertTrue((root / "gateway-response.json").is_file())
            self.assertTrue((root / "report.csv").is_file())
            self.assertEqual(
                json.loads((root / "report.csv.structured.json").read_text(encoding="utf-8")),
                [record],
            )

    def test_fetch_metadata_writes_redacted_gateway_response_without_downloading_file(self):
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "files",
                "result_content": {
                    "status": "COMPLETED",
                    "failedInstances": 0,
                    "files": [{"fileUrl": "http://gateway.test/files/report.csv?token=secret"}],
                },
            },
        })
        session = FakeSession(response)

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, valid_env(), clear=True
        ):
            root = Path(temp_dir)
            metadata = client.fetch_material_report_metadata(
                "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                output_dir=root, session=session,
            )

            gateway_response = (root / "gateway-response.json").read_text(encoding="utf-8")

        self.assertEqual(metadata, {"resultType": "files"})
        self.assertIsNone(session.get_call)
        self.assertNotIn("secret", gateway_response)

    def test_files_response_without_a_valid_result_file_raises(self):
        response = FakeResponse({
            "success": True,
            "data": {
                "result_type": "files",
                "result_content": {"status": "COMPLETED", "failedInstances": 0, "files": []},
            },
        })

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, valid_env(), clear=True
        ):
            with self.assertRaisesRegex(client.ReportClientError, "result file"):
                client.fetch_material_report(
                    "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                    output_dir=Path(temp_dir), session=FakeSession(response),
                )

    def test_check_auth_is_network_free_and_never_serializes_secret_values(self):
        with patch.dict(
            os.environ,
            valid_env(
                YUCE_AUTHORIZATION="authorization-secret",
                YUCE_SESSION_ID="agent-secret",
                YCSESSIONID="cookie-secret",
            ),
            clear=True,
        ):
            diagnostics = client.check_auth()

        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertTrue(diagnostics["ready"])
        self.assertNotIn("authorization-secret", serialized)
        self.assertNotIn("agent-secret", serialized)
        self.assertNotIn("cookie-secret", serialized)

    def test_http_401_does_not_include_credentials(self):
        response = FakeResponse({"success": False}, status_code=401)
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, valid_env(YUCE_AUTHORIZATION="authorization-secret"), clear=True
        ):
            with self.assertRaisesRegex(client.ReportClientError, "HTTP 401") as caught:
                client.fetch_material_report(
                    "shop-1", "1770096415226951", "2026-08-01", "2026-08-07",
                    output_dir=Path(temp_dir), session=FakeSession(response),
                )

        self.assertNotIn("authorization-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
