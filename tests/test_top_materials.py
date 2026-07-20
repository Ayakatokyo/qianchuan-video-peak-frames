import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_table_client import AssetTableClient, AssetTableError  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

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
                    },
                ),
                (
                    "POST",
                    "https://asset.example/worktable/getAllFields?tableId=table-42",
                    {
                        "headers": client.headers,
                        "cookies": {"YCSESSIONID": "session-value"},
                        "timeout": 15,
                    },
                ),
            ],
        )

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


class AssetTableOriginDataTests(unittest.TestCase):
    def test_iterates_two_pages_with_exact_restricted_sql(self):
        first_page = [["origin-%04d" % index] for index in range(2000)]
        session = FakeSession(
            post_responses=[
                platform_response({"fields": ["origin_data"], "rows": first_page}),
                platform_response({"fields": ["origin_data"], "rows": [["origin-2000"]]}),
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
            },
        )

    def test_rejects_unsafe_table_or_invalid_partition_date_without_request(self):
        client = AssetTableClient("https://asset.example", "token", session=FakeSession())

        for table, date in (
            ("material; DROP TABLE x", "2026-07-20"),
            ("material_table", "2026/07/20"),
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

    def test_rejects_malformed_origin_data_row(self):
        session = FakeSession(
            post_responses=[platform_response({"fields": ["origin_data"], "rows": [[]]})]
        )

        with self.assertRaisesRegex(AssetTableError, "origin_data"):
            list(
                AssetTableClient(
                    "https://asset.example", "token", session=session
                ).iter_origin_data("material_table", "2026-07-20")
            )

    def test_rejects_query_without_origin_data_field_or_string_value(self):
        for data in (
            {"fields": ["other_field"], "rows": [["value"]]},
            {"fields": ["origin_data"], "rows": [[123]]},
        ):
            with self.subTest(data=data):
                session = FakeSession(post_responses=[platform_response(data)])
                with self.assertRaisesRegex(AssetTableError, "origin_data"):
                    list(
                        AssetTableClient(
                            "https://asset.example", "token", session=session
                        ).iter_origin_data("material_table", "2026-07-20")
                    )


if __name__ == "__main__":
    unittest.main()
