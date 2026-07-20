"""Read-only client for retrieving origin_data from an asset work table."""

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from urllib.parse import quote, urlsplit

try:
    import requests
except ImportError:  # Keeps fake-session tests independent of installed dependencies.
    requests = None


class AssetTableError(Exception):
    """Raised when the asset table platform returns invalid data or a request fails."""


class AssetTableClient:
    """Use the verified asset-table endpoints without accepting arbitrary SQL."""

    _TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _PARTITION_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _PAGE_SIZE = 2000
    _MAX_RETRY_AFTER_SECONDS = 60.0

    def __init__(
        self,
        base_url,
        session_token,
        session=None,
        timeout=15,
        retries=2,
        sleep=None,
        clock=None,
    ):
        if not isinstance(base_url, str) or not base_url.strip():
            raise AssetTableError("base_url must be a non-empty string")
        try:
            parsed_base_url = urlsplit(base_url.strip())
        except ValueError as error:
            raise AssetTableError("base_url must be a valid HTTP URL") from error
        if (
            parsed_base_url.scheme not in ("http", "https")
            or not parsed_base_url.netloc
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise AssetTableError(
                "base_url must be an HTTP URL without userinfo, query, or fragment"
            )
        if not isinstance(session_token, str) or not session_token:
            raise AssetTableError("session_token must be a non-empty string")
        if not isinstance(retries, int) or retries < 0:
            raise AssetTableError("retries must be a non-negative integer")
        if sleep is not None and not callable(sleep):
            raise AssetTableError("sleep must be callable")
        if clock is not None and not callable(clock):
            raise AssetTableError("clock must be callable")

        self.base_url = base_url.strip().rstrip("/")
        if session is None:
            if requests is None:
                raise AssetTableError("requests must be installed to create a default session")
            session = requests.Session()
        self.session = session
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep if sleep is not None else time.sleep
        self.clock = (
            clock if clock is not None else lambda: datetime.now(timezone.utc)
        )
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "qianchuan-video-peak-frames/1.0",
        }
        self.cookies = {"YCSESSIONID": session_token}

    def discover_table(self, table_name):
        """Return a physical table name after checking its required schema."""
        if not isinstance(table_name, str) or not table_name:
            raise AssetTableError("table_name must be a non-empty string")

        search_data = self._platform_data(
            self._request(
                "GET",
                "/search/searchAssetNodeByName",
                params={"retrievalWord": table_name, "limit": 20},
            )
        )
        if not isinstance(search_data, list):
            raise AssetTableError("search response data must be a list")

        table_id = None
        for item in search_data:
            if not isinstance(item, dict):
                raise AssetTableError("search result entry must be an object")
            if item.get("tableName") == table_name:
                table_id = item.get("objId")
                if not isinstance(table_id, (str, int)) or isinstance(table_id, bool):
                    raise AssetTableError("matching search result has no valid objId")
                break
        if table_id is None:
            raise AssetTableError("asset table was not found")

        detail_data = self._platform_data(
            self._request(
                "POST", "/worktable/loadWorkTableDetail", params={"tableId": table_id}
            )
        )
        if not isinstance(detail_data, dict):
            raise AssetTableError("work table detail data must be an object")
        physical_table = detail_data.get("tableName")
        if not isinstance(physical_table, str) or not physical_table:
            raise AssetTableError("work table detail has no physical tableName")

        fields_data = self._platform_data(
            self._request(
                "POST",
                "/worktable/getAllFields?tableId=" + quote(str(table_id), safe=""),
            )
        )
        self._validate_required_fields(fields_data)
        return physical_table

    def iter_origin_data(self, physical_table, partition_date):
        """Yield every origin_data value for one date partition in fixed-size pages."""
        if not isinstance(physical_table, str) or not self._TABLE_NAME_PATTERN.fullmatch(
            physical_table
        ):
            raise AssetTableError("physical_table is not a valid SQL identifier")
        if not isinstance(partition_date, str) or not self._PARTITION_DATE_PATTERN.fullmatch(
            partition_date
        ):
            raise AssetTableError("partition_date must use YYYY-MM-DD")
        try:
            datetime.strptime(partition_date, "%Y-%m-%d")
        except ValueError as error:
            raise AssetTableError("partition_date must be a valid calendar date") from error

        offset = 0
        while True:
            sql = (
                "SELECT origin_data FROM {table} WHERE dt = '{date}' "
                "ORDER BY origin_data LIMIT {limit} OFFSET {offset}"
            ).format(
                table=physical_table,
                date=partition_date,
                limit=self._PAGE_SIZE,
                offset=offset,
            )
            data = self._platform_data(
                self._request(
                    "POST",
                    "/analysis/previewDynamicSqlData",
                    json={
                        "dynamicParameterList": [],
                        "sql": sql,
                        "pageSize": self._PAGE_SIZE,
                    },
                )
            )
            if not isinstance(data, dict):
                raise AssetTableError("query response data must be an object")
            fields = data.get("fields")
            if not isinstance(fields, list):
                raise AssetTableError("query response is missing fields")
            if not any(
                field == "origin_data"
                or (isinstance(field, dict) and field.get("fieldName") == "origin_data")
                for field in fields
            ):
                raise AssetTableError("query response is missing origin_data field")
            rows = data.get("rows")
            if not isinstance(rows, list):
                raise AssetTableError("query response is missing rows")

            for row in rows:
                if not isinstance(row, dict):
                    raise AssetTableError("query row is missing origin_data")
                origs = row.get("origs")
                if not isinstance(origs, list) or not origs:
                    raise AssetTableError("query row is missing origin_data")
                origin_data = origs[0]
                if not isinstance(origin_data, str):
                    raise AssetTableError("query row has invalid origin_data")
                yield origin_data

            if len(rows) < self._PAGE_SIZE:
                return
            offset += self._PAGE_SIZE

    def _validate_required_fields(self, fields_data):
        if not isinstance(fields_data, list):
            raise AssetTableError("fields response data must be a list")

        found_partition = False
        found_origin_data = False
        for field in fields_data:
            if not isinstance(field, dict):
                raise AssetTableError("field entry must be an object")
            if field.get("fieldName") == "dt" and field.get("partitionKey"):
                found_partition = True
            if field.get("fieldName") == "origin_data":
                found_origin_data = True
        if not found_partition:
            raise AssetTableError("required dt partition field is missing")
        if not found_origin_data:
            raise AssetTableError("required origin_data field is missing")

    def _request(self, method, path, **kwargs):
        url = self.base_url + path
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                response = getattr(self.session, method.lower())(
                    url,
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=self.timeout,
                    allow_redirects=False,
                    **kwargs
                )
            except self._request_exceptions() as error:
                last_error = "request failed: {0}".format(error)
                if attempt < self.retries:
                    continue
                break
            if response.status_code != 200:
                last_error = "HTTP status {0}".format(response.status_code)
                if not self._is_retryable_status(response.status_code) or attempt == self.retries:
                    break
                retry_after = self._retry_after_seconds(response)
                if retry_after is not None:
                    if retry_after > self._MAX_RETRY_AFTER_SECONDS:
                        raise AssetTableError(
                            "Retry-After delay exceeds the 60 second maximum wait policy"
                        )
                    self.sleep(retry_after)
                continue
            return response
        raise AssetTableError("asset table request failed after retries: {0}".format(last_error))

    @staticmethod
    def _request_exceptions():
        if requests is None:
            return (OSError,)
        return (requests.RequestException,)

    @staticmethod
    def _is_retryable_status(status_code):
        return (
            isinstance(status_code, int)
            and not isinstance(status_code, bool)
            and (status_code in (408, 429) or 500 <= status_code <= 599)
        )

    def _retry_after_seconds(self, response):
        headers = getattr(response, "headers", None)
        if not hasattr(headers, "get"):
            return None
        value = headers.get("Retry-After")
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                now = self.clock()
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                return None
            if now.tzinfo is None:
                return None
            seconds = (retry_at - now).total_seconds()
        if not isfinite(seconds) or seconds < 0:
            return None
        return seconds

    @staticmethod
    def _platform_data(response):
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise AssetTableError("platform response is not valid JSON") from error
        if not isinstance(payload, dict):
            raise AssetTableError("platform response must be an object")
        if str(payload.get("code")) != "200":
            raise AssetTableError("platform response code is not 200")
        if "data" not in payload:
            raise AssetTableError("platform response is missing data")
        return payload["data"]
