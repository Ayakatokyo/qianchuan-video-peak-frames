import json
import re
import sys
import tempfile
import unittest
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_table_client import AssetTableClient, AssetTableError  # noqa: E402
from top_materials_lib import (  # noqa: E402
    MaterialDataError,
    build_output,
    select_materials,
)
from export_top_materials import main  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next(self.get_responses)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next(self.post_responses)

    @staticmethod
    def _next(responses):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def platform_response(data, code=200):
    return FakeResponse({"code": code, "data": data})


class AssetTableDiscoveryTests(unittest.TestCase):
    def test_discovers_exact_table_and_validates_required_fields(self):
        session = FakeSession(
            get_responses=[
                platform_response(
                    [
                        {"tableName": "Top Materials Archive", "objId": "older"},
                        {"tableName": "Top Materials", "objId": "table-42"},
                    ]
                )
            ],
            post_responses=[
                platform_response({"tableName": "material_origin_records"}),
                platform_response(
                    [
                        {"fieldName": "dt", "partitionKey": True},
                        {"fieldName": "origin_data", "partitionKey": False},
                    ]
                ),
            ],
        )
        client = AssetTableClient(
            "https://asset.example/", "session-value", session=session
        )

        table = client.discover_table("Top Materials")

        self.assertEqual(table, "material_origin_records")
        self.assertEqual(
            session.calls,
            [
                (
                    "GET",
                    "https://asset.example/search/searchAssetNodeByName",
                    {
                        "params": {"retrievalWord": "Top Materials", "limit": 20},
                        "headers": client.headers,
                        "cookies": {"YCSESSIONID": "session-value"},
                        "timeout": 15,
                        "allow_redirects": False,
                    },
                ),
                (
                    "POST",
                    "https://asset.example/worktable/loadWorkTableDetail",
                    {
                        "params": {"tableId": "table-42"},
                        "headers": client.headers,
                        "cookies": {"YCSESSIONID": "session-value"},
                        "timeout": 15,
                        "allow_redirects": False,
                    },
                ),
                (
                    "POST",
                    "https://asset.example/worktable/getAllFields?tableId=table-42",
                    {
                        "headers": client.headers,
                        "cookies": {"YCSESSIONID": "session-value"},
                        "timeout": 15,
                        "allow_redirects": False,
                    },
                ),
            ],
        )

    def test_accepts_string_platform_success_code(self):
        session = FakeSession(
            get_responses=[
                platform_response(
                    [{"tableName": "Top Materials", "objId": "table-42"}], code="200"
                )
            ],
            post_responses=[
                platform_response({"tableName": "material_origin_records"}, code="200"),
                platform_response(
                    [
                        {"fieldName": "dt", "partitionKey": True},
                        {"fieldName": "origin_data", "partitionKey": False},
                    ],
                    code="200",
                ),
            ],
        )
        client = AssetTableClient(
            "https://asset.example/", "session-value", session=session
        )

        table = client.discover_table("Top Materials")

        self.assertEqual(table, "material_origin_records")

    def test_discovery_rejects_missing_required_partition_field(self):
        session = FakeSession(
            get_responses=[platform_response([{"tableName": "Top", "objId": "id"}])],
            post_responses=[
                platform_response({"tableName": "physical_table"}),
                platform_response([{"fieldName": "dt", "partitionKey": False}]),
            ],
        )

        with self.assertRaisesRegex(AssetTableError, "partition"):
            AssetTableClient("https://asset.example", "token", session=session).discover_table(
                "Top"
            )

    def test_discovery_rejects_missing_or_malformed_search_result(self):
        for data in ([], [{"tableName": "Top"}], {"tableName": "Top"}):
            with self.subTest(data=data):
                session = FakeSession(get_responses=[platform_response(data)])
                with self.assertRaises(AssetTableError):
                    AssetTableClient(
                        "https://asset.example", "token", session=session
                    ).discover_table("Top")


class AssetTableClientTests(unittest.TestCase):
    def test_iterates_two_pages_with_exact_restricted_sql(self):
        first_page = [{"origs": ["origin-%04d" % index]} for index in range(2000)]
        session = FakeSession(
            post_responses=[
                platform_response({"fields": ["origin_data"], "rows": first_page}),
                platform_response(
                    {"fields": ["origin_data"], "rows": [{"origs": ["origin-2000"]}]}
                ),
            ]
        )
        client = AssetTableClient("https://asset.example", "token", session=session)

        rows = list(client.iter_origin_data("material_origin_records", "2026-07-20"))

        self.assertEqual(len(rows), 2001)
        self.assertEqual(rows[0], "origin-0000")
        self.assertEqual(rows[-1], "origin-2000")
        self.assertEqual(
            [call[2]["json"]["sql"] for call in session.calls],
            [
                "SELECT origin_data FROM material_origin_records WHERE dt = '2026-07-20' ORDER BY origin_data LIMIT 2000 OFFSET 0",
                "SELECT origin_data FROM material_origin_records WHERE dt = '2026-07-20' ORDER BY origin_data LIMIT 2000 OFFSET 2000",
            ],
        )
        self.assertEqual(
            [call[1] for call in session.calls],
            [
                "https://asset.example/analysis/previewDynamicSqlData",
                "https://asset.example/analysis/previewDynamicSqlData",
            ],
        )
        self.assertEqual(
            session.calls[0][2],
            {
                "json": {
                    "dynamicParameterList": [],
                    "sql": "SELECT origin_data FROM material_origin_records WHERE dt = '2026-07-20' ORDER BY origin_data LIMIT 2000 OFFSET 0",
                    "pageSize": 2000,
                },
                "headers": client.headers,
                "cookies": {"YCSESSIONID": "token"},
                "timeout": 15,
                "allow_redirects": False,
            },
        )

    def test_rejects_unsafe_base_urls_and_allows_http(self):
        for base_url in (
            "ftp://asset.example",
            "https://user@asset.example",
            "https://asset.example?source=test",
            "https://asset.example#section",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(AssetTableError, "base_url"):
                    AssetTableClient(base_url, "token", session=FakeSession())

        client = AssetTableClient("http://asset.example", "token", session=FakeSession())
        self.assertEqual(client.base_url, "http://asset.example")

    def test_rejects_unsafe_table_or_invalid_partition_date_without_request(self):
        client = AssetTableClient("https://asset.example", "token", session=FakeSession())

        for table, date in (
            ("material; DROP TABLE x", "2026-07-20"),
            ("material_table", "2026/07/20"),
            ("material_table", "2026-02-29"),
            ("material_table", "2026-13-01"),
        ):
            with self.subTest(table=table, date=date):
                with self.assertRaises(AssetTableError):
                    list(client.iter_origin_data(table, date))

        self.assertEqual(client.session.calls, [])

    def test_retries_status_failure_then_raises_platform_error(self):
        session = FakeSession(post_responses=[FakeResponse({}, 503), FakeResponse({}, 503)])
        client = AssetTableClient("https://asset.example", "token", session=session, retries=1)

        with self.assertRaisesRegex(AssetTableError, "HTTP status 503"):
            list(client.iter_origin_data("material_table", "2026-07-20"))

        self.assertEqual(len(session.calls), 2)

    def test_rejects_redirect_without_following_it(self):
        session = FakeSession(post_responses=[FakeResponse({}, 302)])
        client = AssetTableClient("https://asset.example", "token", session=session)

        with self.assertRaisesRegex(AssetTableError, "HTTP status 302"):
            list(client.iter_origin_data("material_table", "2026-07-20"))

        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][2]["allow_redirects"])

    def test_does_not_retry_client_error_statuses(self):
        session = FakeSession(post_responses=[FakeResponse({}, 400)])
        client = AssetTableClient("https://asset.example", "token", session=session)

        with self.assertRaisesRegex(AssetTableError, "HTTP status 400"):
            list(client.iter_origin_data("material_table", "2026-07-20"))

        self.assertEqual(len(session.calls), 1)

    def test_retries_429_after_retry_after_delay(self):
        sleeps = []
        session = FakeSession(
            post_responses=[
                FakeResponse({}, 429, headers={"Retry-After": "4"}),
                platform_response({"fields": ["origin_data"], "rows": []}),
            ]
        )
        client = AssetTableClient(
            "https://asset.example", "token", session=session, retries=1, sleep=sleeps.append
        )

        self.assertEqual(
            list(client.iter_origin_data("material_table", "2026-07-20")), []
        )
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleeps, [4.0])

    def test_retries_after_http_date_delay(self):
        sleeps = []
        session = FakeSession(
            post_responses=[
                FakeResponse(
                    {}, 429, headers={"Retry-After": "Tue, 21 Oct 2025 07:28:00 GMT"}
                ),
                platform_response({"fields": ["origin_data"], "rows": []}),
            ]
        )
        client = AssetTableClient(
            "https://asset.example",
            "token",
            session=session,
            retries=1,
            sleep=sleeps.append,
            clock=lambda: datetime(2025, 10, 21, 7, 27, 55, tzinfo=timezone.utc),
        )

        self.assertEqual(
            list(client.iter_origin_data("material_table", "2026-07-20")), []
        )
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleeps, [5.0])

    def test_rejects_retry_after_delay_above_maximum(self):
        sleeps = []
        session = FakeSession(
            post_responses=[FakeResponse({}, 503, headers={"Retry-After": "61"})]
        )
        client = AssetTableClient(
            "https://asset.example", "token", session=session, retries=1, sleep=sleeps.append
        )

        with self.assertRaisesRegex(AssetTableError, "Retry-After.*maximum"):
            list(client.iter_origin_data("material_table", "2026-07-20"))

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(sleeps, [])

    def test_ignores_non_finite_retry_after_delay(self):
        sleeps = []
        session = FakeSession(
            post_responses=[
                FakeResponse({}, 429, headers={"Retry-After": "nan"}),
                platform_response({"fields": ["origin_data"], "rows": []}),
            ]
        )
        client = AssetTableClient(
            "https://asset.example", "token", session=session, retries=1, sleep=sleeps.append
        )

        self.assertEqual(
            list(client.iter_origin_data("material_table", "2026-07-20")), []
        )
        self.assertEqual(sleeps, [])

    def test_rejects_malformed_origin_data_row(self):
        for rows in (
            None,
            [["legacy-list-row"]],
            [{}],
            [{"origs": "not-a-list"}],
            [{"origs": []}],
            [{"origs": [123]}],
        ):
            with self.subTest(rows=rows):
                session = FakeSession(
                    post_responses=[
                        platform_response({"fields": ["origin_data"], "rows": rows})
                    ]
                )
                with self.assertRaisesRegex(AssetTableError, "origin_data|rows"):
                    list(
                        AssetTableClient(
                            "https://asset.example", "token", session=session
                        ).iter_origin_data("material_table", "2026-07-20")
                    )

    def test_rejects_query_without_origin_data_field_or_string_value(self):
        for data in (
            {"fields": ["other_field"], "rows": [{"origs": ["value"]}]},
            {"fields": ["origin_data"], "rows": [{"origs": [123]}]},
        ):
            with self.subTest(data=data):
                session = FakeSession(post_responses=[platform_response(data)])
                with self.assertRaisesRegex(AssetTableError, "origin_data"):
                    list(
                        AssetTableClient(
                            "https://asset.example", "token", session=session
                        ).iter_origin_data("material_table", "2026-07-20")
                    )


def origin_data(
    material_id="1",
    name="Winter campaign",
    material_type="live_video",
    video_type="portrait",
    cost="1",
):
    return json.dumps(
        {
            "dimensions": {
                "material_id": {"Value": material_id},
                "roi2_material_video_name": {"ValueStr": name},
                "roi2_material_type_v3": {"ValueStr": material_type},
                "roi2_material_video_type": {"ValueStr": video_type},
            },
            "metrics": {"stat_cost_for_overall_roi2": {"Value": cost}},
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
            materials=materials,
            excluded=excluded,
        )

        self.assertEqual(
            output,
            {
                "actualStartDate": "2026-05-01",
                "actualEndDate": "2026-05-05",
                "rankingMetric": "stat_cost_for_overall_roi2",
                "filters": {
                    "materialNameRegex": "First|Second|Winter",
                    "materialTypes": ["live_video"],
                },
                "excluded": [
                    {
                        "materialId": "-2",
                        "reason": "AIGC dynamic creative collection cannot map to a single-material diagnosis RPA",
                        "sourceRowCount": 1,
                        "totalCost": 7.0,
                    }
                ],
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
    def test_exports_only_after_every_partition_succeeds(self):
        rows = [origin_data(material_id="m-1", cost="3")]

        class Client:
            def __init__(self, *_args, **_kwargs): pass
            def discover_table(self, _name): return "material_table"
            def iter_origin_data(self, _table, _date): return iter(rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "top_materials.json"
            environment = {"ENV_BACKEND_HOST": "http://asset.example", "YCSESSIONID": "token"}
            with patch.dict("os.environ", environment, clear=True), patch("export_top_materials.AssetTableClient", Client):
                self.assertEqual(main(["--start-date", "2026-05-01", "--end-date", "2026-05-02", "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["materials"][0]["rank"], 1)

    def test_does_not_publish_when_partition_fails(self):
        class Client:
            def __init__(self, *_args, **_kwargs): pass
            def discover_table(self, _name): return "material_table"
            def iter_origin_data(self, _table, date):
                if date == "2026-05-02": raise AssetTableError("unavailable")
                return iter([origin_data(material_id="m-1", cost="3")])

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "top_materials.json"
            environment = {"ENV_BACKEND_HOST": "http://asset.example", "YCSESSIONID": "token"}
            with patch.dict("os.environ", environment, clear=True), patch("export_top_materials.AssetTableClient", Client):
                self.assertEqual(main(["--start-date", "2026-05-01", "--end-date", "2026-05-02", "--output", str(output)]), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
