from datetime import datetime
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
import uuid

from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv
from path_safety import artifact_path, validate_material_id


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

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class RpaNotConfiguredError(RpaAdapterError):
    """Backward-compatible name for older callers."""


@dataclass(frozen=True)
class GatewayFile:
    file_url: str
    partial_errors: tuple = ()


DEFAULT_CSV_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_VIDEO_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_DOWNLOAD_DEADLINE_SEC = 600
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 3600
RUNNING_TASK_STATUSES = {"pending", "running"}
SUCCESS_TASK_STATUSES = {"completed", "partial_success"}


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
    try:
        material_id = validate_material_id(material_id)
    except ValueError as exc:
        raise RpaAdapterError(f"invalid material_id: {exc}") from exc
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
        raise RpaAdapterError(
            f"unable to {action}: {exc}",
            _gateway_response_diagnostics(response),
        ) from exc


def _redact_gateway_summary(value):
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value or "")
    text = re.sub(
        r"(?i)([?&](?:access_)?token|[?&]secret|[?&]authorization)=([^&\s]+)",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(
        r'(?i)("(?:access_)?token"\s*:\s*")[^"]*',
        r'\1[redacted]',
        text,
    )
    text = re.sub(
        r'(?i)("(?:authorization|secret|session(?:_id)?)"\s*:\s*")[^"]*',
        r'\1[redacted]',
        text,
    )
    return text[:1024]


def _gateway_response_diagnostics(response):
    diagnostics = {"gatewayPhase": "response"}
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        diagnostics["httpStatus"] = status
        if status == 429:
            diagnostics["rpaFailureClass"] = "gateway_rate_limited"
        elif 500 <= status <= 599:
            diagnostics["rpaFailureClass"] = "gateway_server_error"
        elif 400 <= status <= 499:
            diagnostics["rpaFailureClass"] = "gateway_client_error"
    headers = getattr(response, "headers", {})
    if isinstance(headers, dict):
        request_id = headers.get("X-Request-Id") or headers.get("x-request-id")
        if isinstance(request_id, str) and request_id.strip():
            diagnostics["requestId"] = request_id.strip()
    try:
        payload = response.json()
    except Exception:
        payload = getattr(response, "text", "")
    if isinstance(payload, dict) and "requestId" not in diagnostics:
        request_id = payload.get("requestId") or payload.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            diagnostics["requestId"] = request_id.strip()
    summary = _redact_gateway_summary(payload)
    if summary:
        diagnostics["responseSummary"] = summary
    return diagnostics


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
            content = data.get("result_content")
            if not isinstance(content, dict):
                content = data
            errors = content.get("errors", []) if isinstance(content, dict) else []
            return GatewayFile(
                _files_response_url(result),
                tuple(errors) if isinstance(errors, list) else (),
            )
        if isinstance(data, dict) and data.get("result_type") == "payload":
            return normalize_gateway_records(data)
        if isinstance(data, dict) and "payload" in data:
            return normalize_gateway_records(data)
    return normalize_gateway_records(result)


def _files_response_url(result):
    if result.get("success") is False or (
        "code" in result and result.get("code") not in (200, "200")
    ):
        raise RpaAdapterError("RPA gateway files response was not successful")

    data = result.get("data")
    content = data.get("result_content") if isinstance(data, dict) else None
    if content is None and isinstance(data, dict):
        content = data
    if not isinstance(content, dict):
        raise RpaAdapterError("RPA gateway files response is missing result_content")
    if content.get("status") not in (None, "COMPLETED", "completed"):
        raise RpaAdapterError("RPA gateway files task did not complete successfully")
    if content.get("failedInstances", 0) != 0:
        raise RpaAdapterError("RPA gateway files task did not complete successfully")

    files = content.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        raise RpaAdapterError("RPA gateway files response has no diagnostic CSV")
    file_url = files[0].get("fileUrl")
    if not isinstance(file_url, str) or not file_url.strip():
        raise RpaAdapterError("RPA gateway diagnostic CSV URL is missing")
    return file_url.strip()


def _stream_download(
    url,
    target_path,
    *,
    session,
    timeout,
    max_bytes,
    deadline_sec,
    action,
):
    target = Path(target_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpaAdapterError(
            f"unable to create RPA {action} directory {target.parent}: {exc}"
        ) from exc

    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}{target.suffix}")
    response = None
    started = time.monotonic()
    try:
        response = session.get(url, stream=True, timeout=timeout)
        response.raise_for_status()
        content_type = str(getattr(response, "headers", {}).get("Content-Type") or "").lower()
        if action == "video" and content_type and not (
            content_type.startswith("video/")
            or content_type.startswith("application/octet-stream")
            or content_type.startswith("binary/octet-stream")
        ):
            raise RpaAdapterError("downloaded RPA video content type is invalid")
        content_length = getattr(response, "headers", {}).get("Content-Length")
        try:
            declared_size = int(content_length) if content_length is not None else None
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > max_bytes:
            raise RpaAdapterError(f"downloaded RPA {action} exceeds maximum size")

        written = 0
        with staging.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if time.monotonic() - started >= deadline_sec:
                    raise RpaAdapterError(f"RPA {action} download exceeded total deadline")
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise RpaAdapterError(f"downloaded RPA {action} exceeds maximum size")
                output.write(chunk)
        if written == 0:
            raise RpaAdapterError(f"downloaded RPA {action} is empty")
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
        raise RpaAdapterError(f"unable to download RPA {action}") from exc
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    return target


def download_diagnosis_csv(
    file_url,
    target_path,
    session=None,
    timeout=300,
    max_bytes=DEFAULT_CSV_MAX_BYTES,
    deadline_sec=DEFAULT_DOWNLOAD_DEADLINE_SEC,
):
    session = _default_session() if session is None else session
    return _stream_download(
        file_url,
        target_path,
        session=session,
        timeout=timeout,
        max_bytes=max_bytes,
        deadline_sec=deadline_sec,
        action="diagnostic CSV",
    )


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
    task_group_id=None,
    on_task_submitted=None,
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
    submit_payload = {
        "authorization": authorization,
        "agent_session_id": session_id,
        "data_source_type": DATA_SOURCE_TYPE,
        "platform": PLATFORM,
        "function_code": FUNCTION_CODE,
        "business_params": business_params,
        "shop_id": str(shop_id).strip(),
    }
    remote_task_group_id = str(task_group_id or "").strip() or None
    if remote_task_group_id is None:
        try:
            response = session.post(
                f"{api_base}/adg/v1/agent/fetch/tasks",
                json=submit_payload,
                headers=HEADERS,
                cookies=cookies,
                timeout=timeout_sec,
            )
            _raise_for_status(response, "submit RPA material diagnosis")
            submit_result = response.json()
        except RpaAdapterError:
            raise
        except Exception as exc:
            raise RpaAdapterError(
                f"unable to submit RPA material diagnosis: {exc}",
                {
                    "gatewayPhase": "submit",
                    "rpaFailureClass": "transport_error",
                    "transportErrorType": type(exc).__name__,
                },
            ) from exc
        if not isinstance(submit_result, dict) or submit_result.get("success") is False:
            raise RpaAdapterError("RPA task submission was not successful")
        submit_data = submit_result.get("data")
        if isinstance(submit_data, dict):
            remote_task_group_id = submit_data.get("task_group_id")
        if not isinstance(remote_task_group_id, str) or not remote_task_group_id.strip():
            raise RpaAdapterError("RPA task submission did not return task_group_id")
        remote_task_group_id = remote_task_group_id.strip()
        if on_task_submitted is not None:
            on_task_submitted(remote_task_group_id)

    deadline = time.monotonic() + min(float(timeout_sec), POLL_TIMEOUT_SECONDS)
    gateway_result = None
    while True:
        if time.monotonic() >= deadline:
            raise RpaAdapterError(
                "RPA material diagnosis task polling timed out",
                {"remoteTaskGroupId": remote_task_group_id},
            )
        try:
            response = session.post(
                f"{api_base}/adg/v1/agent/fetch/tasks/status",
                json={"task_group_id": remote_task_group_id},
                headers=HEADERS,
                cookies=cookies,
                timeout=timeout_sec,
            )
            _raise_for_status(response, "poll RPA material diagnosis")
            status_result = response.json()
        except RpaAdapterError as exc:
            diagnostics = dict(exc.diagnostics)
            diagnostics["remoteTaskGroupId"] = remote_task_group_id
            raise RpaAdapterError(str(exc), diagnostics) from exc
        except Exception as exc:
            raise RpaAdapterError(
                f"unable to poll RPA material diagnosis: {exc}",
                {
                    "gatewayPhase": "poll",
                    "rpaFailureClass": "transport_error",
                    "transportErrorType": type(exc).__name__,
                    "remoteTaskGroupId": remote_task_group_id,
                },
            ) from exc

        status_data = status_result.get("data") if isinstance(status_result, dict) else None
        status = status_data.get("status") if isinstance(status_data, dict) else None
        if status is None and isinstance(status_data, dict):
            result_content = status_data.get("result_content")
            if isinstance(result_content, dict):
                status = result_content.get("status")
        status = str(status or "").strip().lower()
        if status in RUNNING_TASK_STATUSES:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if status not in SUCCESS_TASK_STATUSES:
            diagnostics = {"remoteTaskGroupId": remote_task_group_id}
            if isinstance(status_data, dict):
                errors = status_data.get("errors")
                if not isinstance(errors, list):
                    result_content = status_data.get("result_content")
                    if isinstance(result_content, dict):
                        errors = result_content.get("errors")
                if isinstance(errors, list):
                    diagnostics["rpaErrors"] = errors
            raise RpaAdapterError(
                f"RPA material diagnosis task has unsupported status: {status or 'missing'}",
                diagnostics,
            )
        gateway_result = status_result
        break

    if isinstance(gateway_result.get("data"), dict):
        task_data = gateway_result["data"]
        if "result_type" not in task_data and isinstance(task_data.get("result"), dict):
            gateway_result = dict(gateway_result)
            gateway_result["data"] = task_data["result"]
    gateway_result["remoteTaskGroupId"] = remote_task_group_id
    try:
        gateway_result = unpack_gateway_result(gateway_result)
    except RpaAdapterError as exc:
        diagnostics = dict(exc.diagnostics)
        diagnostics["remoteTaskGroupId"] = remote_task_group_id
        if isinstance(status_data, dict):
            errors = status_data.get("errors")
            if not isinstance(errors, list):
                result_content = status_data.get("result_content")
                if isinstance(result_content, dict):
                    errors = result_content.get("errors")
            if isinstance(errors, list):
                diagnostics["rpaErrors"] = errors
        raise RpaAdapterError(str(exc), diagnostics) from exc
    metadata = {"remoteTaskGroupId": remote_task_group_id}
    if isinstance(gateway_result, GatewayFile) and gateway_result.partial_errors:
        metadata["rpaPartialErrors"] = list(gateway_result.partial_errors)
    if isinstance(gateway_result, list):
        records = gateway_result
        diagnosis_csv_path = None
    else:
        if diagnosis_csv_dir is None:
            raise RpaAdapterError(
                "diagnosis_csv_dir is required for RPA files responses"
            )
        try:
            target = artifact_path(diagnosis_csv_dir, business_params["material_id"], ".csv")
        except ValueError as exc:
            raise RpaAdapterError(f"invalid material_id: {exc}") from exc
        try:
            download_diagnosis_csv(gateway_result.file_url, target, session=session)
        except RpaAdapterError as exc:
            diagnostics = dict(exc.diagnostics)
            diagnostics["remoteTaskGroupId"] = remote_task_group_id
            raise RpaAdapterError(str(exc), diagnostics) from exc
        try:
            records = parse_diagnosis_csv(target)
        except RpaCsvError as exc:
            raise RpaAdapterError(str(exc)) from exc
        diagnosis_csv_path = str(target)

    if return_metadata:
        return records, diagnosis_csv_path, metadata
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


def _preflight_failure_result(material_id, reason, error=None):
    result = _preflight_result(material_id, False, reason)
    diagnostics = getattr(error, "diagnostics", {}) if error is not None else {}
    retryable = reason == "rpa_request_failed" and diagnostics.get(
        "rpaFailureClass"
    ) != "gateway_client_error"
    result.update(
        {
            "rpaResultStatus": (
                "pending" if reason == "rpa_request_failed" else "final_failed"
            ),
            "retryable": retryable,
            "errorType": type(error).__name__ if error is not None else None,
            "errorMessage": str(error) if error is not None else None,
        }
    )
    result.update(diagnostics)
    return result


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
    task_group_id=None,
    on_task_submitted=None,
):
    try:
        records, diagnosis_csv_path, metadata = fetch_material_diagnosis(
            shop_id=shop_id,
            material_id=material_id,
            date_range_type=date_range_type,
            custom_start_date=custom_start_date,
            custom_end_date=custom_end_date,
            diagnosis_csv_dir=diagnosis_csv_dir,
            environ=environ,
            session=session,
            timeout_sec=timeout_sec,
            task_group_id=task_group_id,
            on_task_submitted=on_task_submitted,
            return_metadata=True,
        )
    except RpaAdapterError as exc:
        return _preflight_failure_result(
            material_id, _preflight_failure_reason(exc), exc
        )
    except Exception as exc:
        return _preflight_failure_result(
            material_id, "unknown_preflight_failure", exc
        )
    try:
        result = preflight_material_availability(records, material_id)
    except Exception as exc:
        return _preflight_failure_result(material_id, "rpa_payload_invalid", exc)
    # A structurally valid CSV is durable evidence even when its video URL is
    # empty; persist the records first and let downstream URL resolution decide
    # whether post-processing can proceed.
    result["records"] = records
    result["diagnosisCsvPath"] = diagnosis_csv_path
    result.update(metadata)
    return result


def download_video(
    video_url,
    target_path,
    session=None,
    timeout=300,
    max_bytes=DEFAULT_VIDEO_MAX_BYTES,
    deadline_sec=DEFAULT_DOWNLOAD_DEADLINE_SEC,
    retry_attempts=3,
    retry_backoff_sec=1.0,
):
    if not str(video_url or "").strip():
        raise RpaAdapterError("videoUrl is required")
    if type(retry_attempts) is not int or retry_attempts <= 0:
        raise ValueError("retry_attempts must be a positive integer")
    if retry_backoff_sec < 0:
        raise ValueError("retry_backoff_sec must be non-negative")
    session = _default_session() if session is None else session
    for attempt_index in range(retry_attempts):
        try:
            return _stream_download(
                video_url,
                target_path,
                session=session,
                timeout=timeout,
                max_bytes=max_bytes,
                deadline_sec=deadline_sec,
                action="video",
            )
        except RpaAdapterError as exc:
            if (
                attempt_index + 1 == retry_attempts
                or not _retryable_video_download_error(exc)
            ):
                raise
            time.sleep(retry_backoff_sec * (2**attempt_index))


def _retryable_video_download_error(error):
    message = str(error)
    return not any(
        fragment in message
        for fragment in (
            "exceeds maximum size",
            "exceeded total deadline",
            "videoUrl is required",
        )
    )
