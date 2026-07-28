from datetime import datetime
from dataclasses import dataclass
import json
import os
from pathlib import Path
import uuid

from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv


FUNCTION_CODE = "rpa.conn.juliang.qc.material.analysis.video.live.diagnosis"
PLATFORM = "RPA_JULIANG_QC_ACCOUNT_PASSWORD"
DATA_SOURCE_TYPE = "rpa"
DATE_RANGE_TYPES = {
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_15_DAYS",
    "LAST_WEEK",
    "THIS_MONTH",
    "LAST_MONTH",
    "CUSTOM",
}
HEADERS = {"Content-Type": "application/json"}


class RpaAdapterError(RuntimeError):
    """Raised when the Yuce RPA gateway cannot provide usable records."""


class RpaNotConfiguredError(RpaAdapterError):
    """Backward-compatible name for older callers."""


@dataclass(frozen=True)
class GatewayFile:
    file_url: str


def _default_session():
    try:
        import requests
    except ImportError as exc:
        raise RpaAdapterError(
            "requests must be installed to call the Yuce RPA gateway"
        ) from exc
    return requests.Session()


def runtime_config(environ=None):
    environment = os.environ if environ is None else environ
    required = ("ENV_BACKEND_HOST", "YUCE_AUTHORIZATION", "YUCE_SESSION_ID")
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise RpaAdapterError(
            "missing RPA runtime environment variables: "
            + ", ".join(missing)
        )

    api_base = environment["ENV_BACKEND_HOST"].rstrip("/")
    authorization = environment["YUCE_AUTHORIZATION"]
    session_id = environment["YUCE_SESSION_ID"]
    cookies = {"YCSESSIONID": authorization}
    return api_base, authorization, session_id, cookies


def _parse_date(value, field_name):
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except (TypeError, ValueError):
            continue
    raise RpaAdapterError(
        f"{field_name} must use YYYYMMDD or YYYY-MM-DD format"
    )


def build_business_params(
    material_id,
    date_range_type,
    custom_start_date=None,
    custom_end_date=None,
):
    if not str(material_id or "").strip():
        raise RpaAdapterError("material_id is required")
    if date_range_type not in DATE_RANGE_TYPES:
        raise RpaAdapterError(
            "date_range_type must be one of: "
            + ", ".join(sorted(DATE_RANGE_TYPES))
        )
    if date_range_type == "CUSTOM":
        if not custom_start_date or not custom_end_date:
            raise RpaAdapterError(
                "CUSTOM requires custom_start_date and custom_end_date"
            )
        start = _parse_date(custom_start_date, "custom_start_date")
        end = _parse_date(custom_end_date, "custom_end_date")
        if end < start:
            raise RpaAdapterError(
                "custom_end_date cannot be earlier than custom_start_date"
            )

    params = {
        "material_id": str(material_id).strip(),
        "date_range_type": date_range_type,
    }
    if custom_start_date:
        params["custom_start_date"] = custom_start_date
    if custom_end_date:
        params["custom_end_date"] = custom_end_date
    return params


def list_accounts(environ=None, session=None):
    session = _default_session() if session is None else session
    api_base, authorization, _session_id, cookies = runtime_config(environ)
    response = session.post(
        f"{api_base}/adg/v1/agent/rpa/authorizations/query",
        json={"authorization": authorization, "platform": PLATFORM},
        headers=HEADERS,
        cookies=cookies,
        timeout=30,
    )
    _raise_for_status(response, "list RPA accounts")
    return response.json()


def _raise_for_status(response, action):
    try:
        response.raise_for_status()
    except Exception as exc:
        raise RpaAdapterError(f"unable to {action}: {exc}") from exc


def normalize_gateway_records(result):
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        raise RpaAdapterError("RPA gateway result must be a list or object")

    payload = result.get("payload")
    if payload is None and isinstance(result.get("data"), list):
        payload = result["data"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RpaAdapterError(
                "RPA payload string must contain valid JSON records"
            ) from exc
    if not isinstance(payload, list):
        raise RpaAdapterError("RPA gateway payload must be a record list")
    return payload


def unpack_gateway_result(result):
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and data.get("result_type") == "files":
            return GatewayFile(_files_response_url(result))
    return normalize_gateway_records(result)


def _files_response_url(result):
    if result.get("success") is not True or result.get("code") not in (200, "200"):
        raise RpaAdapterError("RPA gateway files response was not successful")

    data = result.get("data")
    content = data.get("result_content") if isinstance(data, dict) else None
    if not isinstance(content, dict):
        raise RpaAdapterError("RPA gateway files response is missing result_content")
    if content.get("status") != "COMPLETED" or content.get("failedInstances") != 0:
        raise RpaAdapterError("RPA gateway files task did not complete successfully")

    files = content.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        raise RpaAdapterError("RPA gateway files response has no diagnostic CSV")
    file_url = files[0].get("fileUrl")
    if not isinstance(file_url, str) or not file_url.strip():
        raise RpaAdapterError("RPA gateway diagnostic CSV URL is missing")
    return file_url.strip()


def download_diagnosis_csv(file_url, target_path, session=None, timeout=300):
    session = _default_session() if session is None else session
    target = Path(target_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpaAdapterError(
            f"unable to create RPA diagnostic CSV directory {target.parent}: {exc}"
        ) from exc

    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.csv")
    try:
        response = session.get(file_url, stream=True, timeout=timeout)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RpaAdapterError("unable to download RPA diagnostic CSV") from exc
        with staging.open("wb") as csv_file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    csv_file.write(chunk)
        if staging.stat().st_size == 0:
            raise RpaAdapterError("downloaded RPA diagnostic CSV is empty")
        os.replace(staging, target)
    except RpaAdapterError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise RpaAdapterError(f"unable to download RPA diagnostic CSV: {exc}") from exc
    return target


def fetch_material_diagnosis(
    *,
    shop_id,
    material_id,
    date_range_type,
    custom_start_date=None,
    custom_end_date=None,
    diagnosis_csv_dir=None,
    environ=None,
    session=None,
    timeout_sec=3600,
    return_metadata=False,
):
    if not str(shop_id or "").strip():
        raise RpaAdapterError("shop_id is required")
    business_params = build_business_params(
        material_id,
        date_range_type,
        custom_start_date=custom_start_date,
        custom_end_date=custom_end_date,
    )
    session = _default_session() if session is None else session
    api_base, authorization, session_id, cookies = runtime_config(environ)
    try:
        response = session.post(
            f"{api_base}/adg/v1/agent/invoke",
            json={
                "authorization": authorization,
                "agent_session_id": session_id,
                "data_source_type": DATA_SOURCE_TYPE,
                "platform": PLATFORM,
                "function_code": FUNCTION_CODE,
                "business_params": business_params,
                "shop_id": str(shop_id).strip(),
            },
            headers=HEADERS,
            cookies=cookies,
            timeout=timeout_sec,
        )
    except Exception as exc:
        raise RpaAdapterError(
            f"unable to fetch RPA material diagnosis: {exc}"
        ) from exc
    _raise_for_status(response, "fetch RPA material diagnosis")
    gateway_result = unpack_gateway_result(response.json())
    if isinstance(gateway_result, list):
        records = gateway_result
        diagnosis_csv_path = None
    else:
        if diagnosis_csv_dir is None:
            raise RpaAdapterError(
                "diagnosis_csv_dir is required for RPA files responses"
            )
        target = Path(diagnosis_csv_dir) / f"{business_params['material_id']}.csv"
        download_diagnosis_csv(gateway_result.file_url, target, session=session)
        try:
            records = parse_diagnosis_csv(target)
        except RpaCsvError as exc:
            raise RpaAdapterError(str(exc)) from exc
        diagnosis_csv_path = str(target)

    if return_metadata:
        return records, diagnosis_csv_path
    return records


def find_material_video_url(records, material_id=None):
    wanted_material_id = str(material_id or "").strip()
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("recordType") != "material_info":
            continue
        record_material_id = str(record.get("materialId") or "").strip()
        if wanted_material_id and record_material_id != wanted_material_id:
            continue
        video_url = str(record.get("videoUrl") or "").strip()
        if video_url:
            return video_url
    raise RpaAdapterError("RPA records do not contain material_info.videoUrl")


def _preflight_result(material_id, available, failure_reason=None, video_url=None):
    return {
        "materialId": str(material_id or "").strip(),
        "rpaAvailable": available,
        "failureReason": failure_reason,
        "videoUrl": video_url,
    }


def preflight_material_availability(records, material_id):
    wanted_material_id = str(material_id or "").strip()
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("recordType") != "material_info":
            continue
        record_material_id = str(record.get("materialId") or "").strip()
        if wanted_material_id and record_material_id != wanted_material_id:
            continue
        video_url = str(record.get("videoUrl") or "").strip()
        if not video_url:
            return _preflight_result(
                wanted_material_id, False, "missing_video_url"
            )
        return _preflight_result(wanted_material_id, True, video_url=video_url)
    return _preflight_result(wanted_material_id, False, "missing_material_info")


def _preflight_failure_reason(error):
    message = str(error)
    payload_markers = (
        "payload",
        "record list",
        "files response",
        "diagnostic CSV",
    )
    if any(marker in message for marker in payload_markers):
        return "rpa_payload_invalid"
    return "rpa_request_failed"


def fetch_material_preflight(
    *,
    shop_id,
    material_id,
    date_range_type,
    custom_start_date=None,
    custom_end_date=None,
    diagnosis_csv_dir=None,
    environ=None,
    session=None,
    timeout_sec=300,
):
    try:
        records, diagnosis_csv_path = fetch_material_diagnosis(
            shop_id=shop_id,
            material_id=material_id,
            date_range_type=date_range_type,
            custom_start_date=custom_start_date,
            custom_end_date=custom_end_date,
            diagnosis_csv_dir=diagnosis_csv_dir,
            environ=environ,
            session=session,
            timeout_sec=timeout_sec,
            return_metadata=True,
        )
    except RpaAdapterError as exc:
        return _preflight_result(
            material_id, False, _preflight_failure_reason(exc)
        )
    except Exception:
        return _preflight_result(material_id, False, "unknown_preflight_failure")
    try:
        result = preflight_material_availability(records, material_id)
    except Exception:
        return _preflight_result(material_id, False, "rpa_payload_invalid")
    if result["rpaAvailable"]:
        result["records"] = records
        result["diagnosisCsvPath"] = diagnosis_csv_path
    return result


def download_video(video_url, target_path, session=None, timeout=300):
    if not str(video_url or "").strip():
        raise RpaAdapterError("videoUrl is required")
    session = _default_session() if session is None else session
    target = Path(target_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpaAdapterError(
            f"unable to create RPA video directory {target.parent}: {exc}"
        ) from exc

    staging = target.with_name(
        f".{target.stem}.{uuid.uuid4().hex}{target.suffix or '.mp4'}"
    )
    try:
        response = session.get(video_url, stream=True, timeout=timeout)
        _raise_for_status(response, "download RPA video")
        with staging.open("wb") as video_file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    video_file.write(chunk)
        if staging.stat().st_size == 0:
            raise RpaAdapterError("downloaded RPA video is empty")
        os.replace(staging, target)
    except RpaAdapterError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise RpaAdapterError(f"unable to download RPA video: {exc}") from exc
    return target
