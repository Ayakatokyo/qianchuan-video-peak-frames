"""Fetch normalized Qianchuan live-video material report records."""

import csv
from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


DATA_SOURCE_TYPE = "api"
RESPONSE_CONTENT_TYPE = "fetch"
FUNCTION_CODE = "OCEANENGINE_UNI_PROMOTION_PLAN_DATA_REQUESTS_ZDY"
PLATFORM = "OCEANENGINE"
DEFAULT_DATA_TOPIC = "OVERALL_ROI_LIVE_MATERIAL_VIDEO"
DEFAULT_DIMENSIONS = [
    "material_id",
    "anchor_id",
    "roi2_material_video_name",
    "roi2_material_type_v3",
    "roi2_material_video_type",
]
DEFAULT_METRICS = [
    "stat_cost_for_overall_roi2",
    "total_prepay_and_pay_settle_overall_roi2_1h",
    "total_cost_per_pay_order_settle_for_overall_roi2_1h",
    "total_order_settle_amount_for_roi2_1h",
    "stat_cost_for_roi2",
    "total_prepay_and_pay_settle_roi2_1h",
    "total_cost_per_pay_order_settle_for_roi2_1h",
    "total_order_settle_count_for_roi2_1h",
    "no_refund_ecom_coupon_amount_for_roi2",
    "total_order_settle_amount_rate_for_roi2_1h",
    "total_refund_order_count_for_roi2_1h",
    "total_refund_order_gmv_for_roi2_1h_rate",
    "total_refund_order_gmv_for_roi2_1h_all",
    "total_order_real_settle_amount_for_roi2_1h",
    "no_refund_ecom_platform_subsidy_amount_for_roi2",
    "total_order_settle_count_rate_for_roi2_1h",
    "total_pay_order_count_for_roi2",
    "total_pay_order_gmv_rate_for_roi2",
    "total_pay_order_gmv_include_coupon_for_roi2",
    "cost_rate_for_roi2",
    "total_prepay_and_pay_order_roi2",
    "total_pay_order_gmv_for_roi2",
    "total_ecom_platform_subsidy_amount_for_roi2",
    "basic_stat_cost_for_roi2_v2",
    "total_cost_per_pay_order_for_roi2",
    "total_pay_order_coupon_amount_for_roi2",
    "total_prepay_order_count_for_roi2",
    "total_prepay_order_gmv_for_roi2",
    "total_unfinished_estimate_order_gmv_for_roi2",
    "total_prepay_and_pay_settle_roi2_7d",
    "total_order_settle_amount_for_roi2_7d",
    "total_order_settle_count_for_roi2_7d",
    "total_cost_per_pay_order_settle_for_roi2_7d",
    "total_order_settle_amount_rate_for_roi2_7d",
    "total_order_settle_count_rate_for_roi2_7d",
    "total_prepay_and_pay_settle_roi2_14d",
    "total_order_settle_amount_for_roi2_14d",
    "total_order_settle_count_for_roi2_14d",
    "total_cost_per_pay_order_settle_for_roi2_14d",
    "total_order_settle_amount_rate_for_roi2_14d",
    "total_order_settle_count_rate_for_roi2_14d",
    "total_prepay_and_pay_settle_roi2_30d",
    "total_order_settle_amount_for_roi2_30d",
    "total_order_settle_count_for_roi2_30d",
    "total_cost_per_pay_order_settle_for_roi2_30d",
    "total_order_settle_amount_rate_for_roi2_30d",
    "total_order_settle_count_rate_for_roi2_30d",
    "total_prepay_and_pay_settle_roi2_90d",
    "total_order_settle_amount_for_roi2_90d",
    "total_order_settle_count_for_roi2_90d",
    "total_cost_per_pay_order_settle_for_roi2_90d",
    "total_order_settle_amount_rate_for_roi2_90d",
    "total_order_settle_count_rate_for_roi2_90d",
    "additional_delivery_stat_cost_for_roi2_assist",
    "additional_delivery_total_pay_order_count_for_roi2_assist",
    "additional_delivery_total_pay_order_gmv_include_coupon_for_roi2_assist",
    "additional_delivery_total_prepay_and_pay_order_roi2_assist",
    "additional_delivery_show_cnt_for_roi2_assist",
    "additional_delivery_ctr_for_roi2_assist",
    "additional_delivery_click_cnt_for_roi2_assist",
    "additional_delivery_convert_rate_for_roi2_assist",
    "additional_delivery_total_pay_order_gmv_for_roi2_assist",
    "additional_delivery_total_pay_order_coupon_amount_for_roi2_assist",
    "additional_delivery_total_ecom_platform_subsidy_amount_for_roi2_assist",
    "additional_delivery_total_unfinished_estimate_order_gmv_for_roi2_assist",
    "additional_delivery_pay_convert_cost_for_roi2_assist_v2",
    "additional_delivery_pay_convert_cnt_for_roi2_assist_v2",
    "additional_delivery_total_order_settle_amount_for_roi2_1h_assist",
    "additional_delivery_total_prepay_and_pay_settle_roi2_1h_assist",
    "additional_delivery_total_order_settle_count_for_roi2_1h_assist",
    "additional_delivery_convert_rate_for_roi2_1h_assist",
    "additional_delivery_total_cost_per_pay_order_settle_for_roi2_1h_assist",
    "additional_delivery_total_order_real_settle_amount_for_roi2_1h_assist",
    "additional_delivery_no_refund_ecom_coupon_amount_for_roi2_assist",
    "additional_delivery_no_refund_ecom_platform_subsidy_amount_for_roi2_assist",
    "additional_delivery_total_order_settle_amount_rate_for_roi2_1h_assist",
    "additional_delivery_total_order_settle_count_rate_for_roi2_1h_assist",
    "additional_delivery_total_refund_order_count_for_roi2_1h_assist",
    "additional_delivery_total_refund_order_gmv_for_roi2_1h_all_assist",
    "additional_delivery_total_refund_order_gmv_for_roi2_1h_rate_assist",
    "additional_delivery_total_prepay_and_pay_settle_roi2_7d_assist",
    "additional_delivery_total_order_settle_amount_for_roi2_7d_assist",
    "additional_delivery_total_order_settle_count_for_roi2_7d_assist",
    "additional_delivery_total_cost_per_pay_order_settle_for_roi2_7d_assist",
    "additional_delivery_total_order_settle_amount_rate_for_roi2_7d_assist",
    "additional_delivery_total_order_settle_count_rate_for_roi2_7d_assist",
]
DEFAULT_FILTERS = [
    {"field": "ecp_app_id", "operator": 7, "values": ["1", "2"]},
    {"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]},
]
DEFAULT_ORDER_BY = [{"field": "stat_cost_for_roi2", "type": 2}]
_SENSITIVE_KEY = re.compile(r"(?:authorization|cookie|session|secret|token)", re.I)


class ReportClientError(RuntimeError):
    """Raised when the material report gateway cannot produce usable records."""


def resolve_runtime_context(environment):
    api_base = str(environment.get("ENV_BACKEND_HOST", "")).strip().rstrip("/")
    authorization = str(environment.get("YUCE_AUTHORIZATION", "")).strip()
    agent_session_id = str(environment.get("YUCE_SESSION_ID", "")).strip()
    agent_session_source = "YUCE_SESSION_ID"
    if not agent_session_id:
        agent_session_id = str(environment.get("YCSESSIONID", "")).strip()
        agent_session_source = "YCSESSIONID"
    if not agent_session_id:
        agent_session_id = authorization
        agent_session_source = "YUCE_AUTHORIZATION"

    cookie_session_id = str(environment.get("YCSESSIONID", "")).strip()
    cookie_session_source = "YCSESSIONID"
    if not cookie_session_id:
        cookie_session_id = authorization
        cookie_session_source = "YUCE_AUTHORIZATION"

    missing = [
        name for name, value in (
            ("ENV_BACKEND_HOST", api_base),
            ("YUCE_AUTHORIZATION", authorization),
            ("agent_session_id", agent_session_id),
            ("cookie_session_id", cookie_session_id),
        ) if not value
    ]
    return {
        "api_base": api_base,
        "authorization": authorization,
        "agent_session_id": agent_session_id,
        "agent_session_source": agent_session_source,
        "cookie_session_id": cookie_session_id,
        "cookie_session_source": cookie_session_source,
        "missing": missing,
    }


def check_auth(environ=None):
    environment = os.environ if environ is None else environ
    context = resolve_runtime_context(environment)
    variables = {}
    for name in ("ENV_BACKEND_HOST", "YUCE_AUTHORIZATION", "YUCE_SESSION_ID", "YCSESSIONID"):
        value = str(environment.get(name, "")).strip()
        variables[name] = {"configured": bool(value), "length": len(value)}
    return {
        "ready": not context["missing"],
        "variables": variables,
        "agentSession": {
            "configured": bool(context["agent_session_id"]),
            "length": len(context["agent_session_id"]),
            "source": context["agent_session_source"],
        },
        "cookieSession": {
            "configured": bool(context["cookie_session_id"]),
            "length": len(context["cookie_session_id"]),
            "source": context["cookie_session_source"],
        },
        "missing": context["missing"],
    }


def build_business_params(advertiser_id, start_date, end_date):
    advertiser = _advertiser_id(advertiser_id)
    start, end = _date_range(start_date, end_date)
    return {
        "advertiserId": advertiser,
        "startTime": start + " 00:00:00",
        "endTime": end + " 23:59:59",
        "dataTopic": DEFAULT_DATA_TOPIC,
        "dimensions": list(DEFAULT_DIMENSIONS),
        "metrics": list(DEFAULT_METRICS),
        "filters": list(DEFAULT_FILTERS),
        "orderBy": list(DEFAULT_ORDER_BY),
    }


def fetch_material_report(
    shop_id,
    advertiser_id,
    start_date,
    end_date,
    *,
    output_dir,
    environ=None,
    session=None,
):
    result, source_dir, session = _fetch_gateway_result(
        shop_id,
        advertiser_id,
        start_date,
        end_date,
        output_dir=output_dir,
        environ=environ,
        session=session,
    )
    records = _records_from_result(result, source_dir, session)
    if not records:
        raise ReportClientError("material report contains no records")
    return records


def fetch_material_report_metadata(
    shop_id,
    advertiser_id,
    start_date,
    end_date,
    *,
    output_dir,
    environ=None,
    session=None,
):
    result, _source_dir, _session = _fetch_gateway_result(
        shop_id,
        advertiser_id,
        start_date,
        end_date,
        output_dir=output_dir,
        environ=environ,
        session=session,
    )
    data = result.get("data")
    return {
        "resultType": data.get("result_type") if isinstance(data, dict) else "records",
    }


def _fetch_gateway_result(
    shop_id,
    advertiser_id,
    start_date,
    end_date,
    *,
    output_dir,
    environ=None,
    session=None,
):
    if not str(shop_id or "").strip():
        raise ReportClientError("shop_id is required")
    context = resolve_runtime_context(os.environ if environ is None else environ)
    if context["missing"]:
        raise ReportClientError(
            "missing report runtime environment: " + ", ".join(context["missing"])
        )
    source_dir = Path(output_dir)
    try:
        source_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReportClientError("unable to create report source directory") from exc

    session = requests.Session() if session is None else session
    payload = {
        "authorization": context["authorization"],
        "agent_session_id": context["agent_session_id"],
        "data_source_type": DATA_SOURCE_TYPE,
        "platform": PLATFORM,
        "function_code": FUNCTION_CODE,
        "response_content_type": RESPONSE_CONTENT_TYPE,
        "business_params": build_business_params(advertiser_id, start_date, end_date),
        "shop_id": str(shop_id).strip(),
    }
    try:
        response = session.post(
            context["api_base"] + "/adg/v1/agent/invoke",
            json=payload,
            headers={"Content-Type": "application/json"},
            cookies={"YCSESSIONID": context["cookie_session_id"]},
            timeout=3600,
        )
    except requests.RequestException as exc:
        raise ReportClientError("material report request failed") from exc
    _raise_for_gateway_status(response, context)
    try:
        result = response.json()
    except (TypeError, ValueError) as exc:
        raise ReportClientError("material report returned non-JSON response") from exc
    if not isinstance(result, dict):
        raise ReportClientError("material report gateway response must be an object")
    if result.get("success") is False:
        raise ReportClientError("material report gateway returned failure")
    _write_json(source_dir / "gateway-response.json", _redact(result))
    return result, source_dir, session


def _raise_for_gateway_status(response, context):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = getattr(response, "status_code", None)
        if status == 401:
            raise ReportClientError(
                "material report authentication failed (HTTP 401; "
                "agent_session_source={0}, cookie_session_source={1})".format(
                    context["agent_session_source"], context["cookie_session_source"]
                )
            ) from exc
        raise ReportClientError("material report gateway failed with HTTP {0}".format(status)) from exc


def _records_from_result(result, source_dir, session):
    data = result.get("data")
    if isinstance(data, list):
        return _validate_records(data)
    if not isinstance(data, dict):
        raise ReportClientError("material report response is missing data")
    content = data.get("result_content")
    result_type = data.get("result_type")
    if result_type == "files":
        return _records_from_files(content, source_dir, session)
    if result_type == "payload":
        return _validate_records(_payload_records(content))
    if isinstance(content, dict) and "payload" in content:
        return _validate_records(_payload_records(content))
    if isinstance(content, list):
        return _validate_records(content)
    raise ReportClientError("material report response has no supported result payload")


def _payload_records(content):
    payload = content.get("payload") if isinstance(content, dict) else content
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ReportClientError("material report payload is not valid JSON") from exc
    return payload


def _records_from_files(content, source_dir, session):
    if not isinstance(content, dict):
        raise ReportClientError("material report files response is missing result content")
    if content.get("status") != "COMPLETED" or content.get("failedInstances", 0) != 0:
        raise ReportClientError("material report files task did not complete")
    files = content.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        raise ReportClientError("material report files response has no result file")
    file_url = files[0].get("fileUrl")
    if not isinstance(file_url, str) or not file_url.strip():
        raise ReportClientError("material report result file URL is missing")
    filename = Path(urlsplit(file_url).path).name or "material-report.csv"
    raw_path = source_dir / filename
    _download_file(file_url, raw_path, session)
    structured_path = raw_path.with_suffix(raw_path.suffix + ".structured.json")
    records = _structure_json_csv(raw_path, structured_path)
    return _validate_records(records)


def _download_file(file_url, target_path, session):
    try:
        response = session.get(file_url, stream=True, timeout=300)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ReportClientError("material report result file download failed") from exc
    staging = target_path.with_name("." + target_path.name + ".tmp")
    try:
        with staging.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        if staging.stat().st_size == 0:
            raise ReportClientError("material report result file is empty")
        os.replace(staging, target_path)
    except OSError as exc:
        raise ReportClientError("unable to persist material report result file") from exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _structure_json_csv(raw_path, structured_path):
    records = []
    try:
        with raw_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row_number, row in enumerate(csv.reader(handle), start=1):
                if len(row) != 1:
                    raise ReportClientError(
                        "material report result file row {0} is not single-column JSON".format(row_number)
                    )
                record = json.loads(row[0])
                if not isinstance(record, dict):
                    raise ReportClientError(
                        "material report result file row {0} is not an object".format(row_number)
                    )
                records.append(record)
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
        raise ReportClientError("material report result file could not be structured") from exc
    _write_json(structured_path, records)
    return records


def _validate_records(records):
    if not isinstance(records, list) or not records:
        raise ReportClientError("material report payload must be a non-empty record list")
    if any(not isinstance(record, dict) for record in records):
        raise ReportClientError("material report payload contains a non-object record")
    return records


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _redact(value):
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if _SENSITIVE_KEY.search(str(key)):
            result[key] = "[redacted]"
        elif str(key) == "fileUrl" and isinstance(item, str):
            result[key] = _redact_url(item)
        else:
            result[key] = _redact(item)
    return result


def _redact_url(value):
    parsed = urlsplit(value)
    query = urlencode([
        (key, "[redacted]" if _SENSITIVE_KEY.search(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _advertiser_id(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise ReportClientError("advertiser_id must contain digits only")
    return int(text)


def _date_range(start_date, end_date):
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except (TypeError, ValueError) as exc:
        raise ReportClientError("report dates must use YYYY-MM-DD") from exc
    if end < start:
        raise ReportClientError("report end date must not precede start date")
    return start.isoformat(), end.isoformat()
