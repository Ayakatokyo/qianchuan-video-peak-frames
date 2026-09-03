from datetime import datetime
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import struct
import threading
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


DEFAULT_CSV_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_VIDEO_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_DOWNLOAD_DEADLINE_SEC = 600
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 3600
RUNNING_TASK_STATUSES = {"pending", "running"}
SUCCESS_TASK_STATUSES = {"completed", "partial_success"}
HTTP_PIPE_CHUNK_SIZE = 1024 * 1024
MP4_METADATA_PROBE_BYTES = 64 * 1024
MAX_MP4_MOOV_BYTES = 16 * 1024 * 1024


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


def _raise_for_status(response, action, *, include_body=True):
    try:
        response.raise_for_status()
    except Exception:
        diagnostics = _gateway_response_diagnostics(
            response, include_body=include_body
        )
        status = diagnostics.get("httpStatus")
        detail = (
            f"gateway response HTTP {status}"
            if status is not None
            else "gateway response error"
        )
        raise RpaAdapterError(
            f"unable to {action}: {detail}",
            diagnostics,
        ) from None


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
        r'(?i)("(?:authorization|secret|cookie|cookies|session(?:_id|id)?|agent_session_id)"\s*:\s*")[^"]*',
        r'\1[redacted]',
        text,
    )
    text = re.sub(
        r'(?i)("(?:fileUrl|file_url)"\s*:\s*")[^"]*',
        r'\1[redacted]',
        text,
    )
    return text[:1024]


def _gateway_response_diagnostics(response, *, include_body=True):
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
    if hasattr(headers, "get"):
        request_id = headers.get("X-Request-Id") or headers.get("x-request-id")
        if isinstance(request_id, str) and request_id.strip():
            diagnostics["requestId"] = request_id.strip()
    if not include_body:
        return diagnostics
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
            return GatewayFile(_files_response_url(result))
    return normalize_gateway_records(result)


def _task_group_id_from_submit(result):
    if not isinstance(result, dict):
        raise RpaAdapterError("RPA task submission response must be an object")
    if result.get("success") is False:
        raise RpaAdapterError("RPA task submission was not successful")
    data = result.get("data")
    candidates = [
        data.get("task_group_id") if isinstance(data, dict) else None,
        data.get("taskGroupId") if isinstance(data, dict) else None,
        result.get("task_group_id"),
        result.get("taskGroupId"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise RpaAdapterError("RPA task submission response is missing task_group_id")


def _normalize_task_group_id(value):
    if not isinstance(value, str) or not value.strip():
        raise RpaAdapterError("task_group_id must be a non-empty string")
    return value.strip()


def _first_file_url(payload):
    candidates = []
    if isinstance(payload, dict):
        content = payload.get("result_content")
        if isinstance(content, dict):
            candidates.extend(content.get("files") or [])
        candidates.extend(payload.get("files") or [])
    for item in candidates:
        if isinstance(item, dict):
            file_url = item.get("fileUrl")
            if isinstance(file_url, str) and file_url.strip():
                return GatewayFile(file_url.strip())
    raise RpaAdapterError("RPA task result has no diagnostic CSV")


def _poll_status_payload(result):
    if not isinstance(result, dict):
        raise RpaAdapterError("RPA task status response must be an object")
    data = result.get("data")
    if not isinstance(data, dict):
        data = result
    nested = data.get("result")
    result_payload = nested if isinstance(nested, dict) else data
    status = data.get("status") or result_payload.get("status")
    if not isinstance(status, str) or not status.strip():
        raise RpaAdapterError("RPA task status is missing")
    status = status.strip().lower()
    errors = data.get("errors")
    if errors is None and isinstance(result_payload, dict):
        errors = result_payload.get("errors")
    if status not in SUCCESS_TASK_STATUSES:
        return {"status": status, "errors": errors}

    result_type = (
        result_payload.get("result_type")
        or result_payload.get("resultType")
        or data.get("result_type")
        or data.get("resultType")
    )
    if result_type == "files" or "files" in result_payload or "result_content" in result_payload:
        content = result_payload.get("result_content")
        if isinstance(content, dict):
            content_status = content.get("status")
            if content_status is not None and str(content_status).upper() not in {
                "COMPLETED",
                "PARTIAL_SUCCESS",
            }:
                raise RpaAdapterError("RPA task files result is not completed")
            failed_instances = content.get("failedInstances")
            if failed_instances not in (None, 0):
                raise RpaAdapterError("RPA task files result has failed instances")
        return {
            "status": status,
            "resultType": "files",
            "result": _first_file_url(result_payload),
            "errors": errors,
        }
    if result_type == "payload" or "payload" in result_payload:
        return {
            "status": status,
            "resultType": "payload",
            "result": result_payload,
            "errors": errors,
        }
    return {"status": status, "resultType": None, "result": None, "errors": errors}


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
    remote_task_group_id = None
    if task_group_id is not None:
        remote_task_group_id = _normalize_task_group_id(task_group_id)
    if remote_task_group_id is None:
        try:
            response = session.post(
                f"{api_base}/adg/v1/agent/fetch/tasks",
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
            _raise_for_status(response, "submit RPA material diagnosis")
            remote_task_group_id = _normalize_task_group_id(
                _task_group_id_from_submit(response.json())
            )
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
        if callable(on_task_submitted):
            on_task_submitted(remote_task_group_id)

    deadline = time.monotonic() + min(float(timeout_sec), POLL_TIMEOUT_SECONDS)
    terminal = None
    while True:
        try:
            response = session.post(
                f"{api_base}/adg/v1/agent/fetch/tasks/status",
                json={
                    "authorization": authorization,
                    "agent_session_id": session_id,
                    "task_group_id": remote_task_group_id,
                },
                headers=HEADERS,
                cookies=cookies,
                timeout=timeout_sec,
            )
            _raise_for_status(response, "poll RPA material diagnosis", include_body=True)
            terminal = _poll_status_payload(response.json())
        except RpaAdapterError as exc:
            if not exc.diagnostics.get("remoteTaskGroupId"):
                exc.diagnostics["remoteTaskGroupId"] = remote_task_group_id
            raise
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

        status = terminal["status"]
        if status in SUCCESS_TASK_STATUSES:
            break
        if status not in RUNNING_TASK_STATUSES:
            raise RpaAdapterError(
                f"RPA material diagnosis task ended with status {status}",
                {
                    "gatewayPhase": "poll",
                    "remoteTaskGroupId": remote_task_group_id,
                    "rpaTaskStatus": status,
                    "rpaErrors": terminal.get("errors"),
                },
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RpaAdapterError(
                "RPA material diagnosis polling exceeded deadline",
                {
                    "gatewayPhase": "poll",
                    "remoteTaskGroupId": remote_task_group_id,
                    "rpaTaskStatus": status,
                    "rpaTimeoutExceeded": True,
                },
            )
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    metadata = {"remoteTaskGroupId": remote_task_group_id}
    if terminal.get("errors"):
        metadata["rpaPartialErrors"] = terminal["errors"]
    result = terminal.get("result")
    if terminal.get("resultType") == "payload":
        try:
            records = normalize_gateway_records(result)
        except RpaAdapterError as exc:
            exc.diagnostics.setdefault("remoteTaskGroupId", remote_task_group_id)
            raise
        diagnosis_csv_path = None
    elif terminal.get("resultType") == "files":
        if diagnosis_csv_dir is None:
            raise RpaAdapterError(
                "diagnosis_csv_dir is required for RPA files responses",
                {"remoteTaskGroupId": remote_task_group_id},
            )
        try:
            target = artifact_path(diagnosis_csv_dir, business_params["material_id"], ".csv")
        except ValueError as exc:
            raise RpaAdapterError(f"invalid material_id: {exc}") from exc
        try:
            download_diagnosis_csv(result.file_url, target, session=session)
            records = parse_diagnosis_csv(target)
        except RpaCsvError as exc:
            raise RpaAdapterError(
                str(exc), {"remoteTaskGroupId": remote_task_group_id}
            ) from exc
        except RpaAdapterError as exc:
            exc.diagnostics.setdefault("remoteTaskGroupId", remote_task_group_id)
            raise
        diagnosis_csv_path = str(target)
    else:
        raise RpaAdapterError(
            "RPA material diagnosis task has no usable result",
            {
                "remoteTaskGroupId": remote_task_group_id,
                "rpaErrors": terminal.get("errors"),
            },
        )

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
        "files result",
        "task result",
        "diagnostic CSV",
    )
    if any(marker in message for marker in payload_markers):
        return "rpa_payload_invalid"
    return "rpa_request_failed"


def _preflight_failure_result(material_id, reason, error=None):
    result = _preflight_result(material_id, False, reason)
    diagnostics = getattr(error, "diagnostics", {}) if error is not None else {}
    terminal_remote_failure = bool(
        diagnostics.get("rpaTimeoutExceeded")
        or diagnostics.get("rpaTaskStatus") not in (None, *RUNNING_TASK_STATUSES)
    )
    retryable = (
        reason == "rpa_request_failed"
        and not terminal_remote_failure
        and diagnostics.get("rpaFailureClass") != "gateway_client_error"
    )
    result.update(
        {
            "rpaResultStatus": (
                "pending"
                if reason == "rpa_request_failed" and not terminal_remote_failure
                else "final_failed"
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
    if result["rpaAvailable"]:
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


def _close_ffmpeg_stdin(ffmpeg_process):
    stdin = getattr(ffmpeg_process, "stdin", None)
    close = getattr(stdin, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _close_response(response):
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _request_ffmpeg_termination(ffmpeg_process):
    terminate = getattr(ffmpeg_process, "terminate", None)
    if callable(terminate):
        try:
            terminate()
        except Exception:
            pass


def _wait_for_ffmpeg_termination(ffmpeg_process):
    wait = getattr(ffmpeg_process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=5)
            return
        except Exception:
            pass

    kill = getattr(ffmpeg_process, "kill", None)
    if callable(kill):
        try:
            kill()
        except Exception:
            pass
        if callable(wait):
            try:
                wait(timeout=5)
            except Exception:
                pass


def _terminate_ffmpeg_process(ffmpeg_process):
    _request_ffmpeg_termination(ffmpeg_process)
    _wait_for_ffmpeg_termination(ffmpeg_process)


def _terminate_ffmpeg_process_in_background(ffmpeg_process):
    _request_ffmpeg_termination(ffmpeg_process)
    cleanup = threading.Thread(
        target=_wait_for_ffmpeg_termination,
        args=(ffmpeg_process,),
        daemon=True,
    )
    cleanup.start()


class _VideoStreamState:
    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.response = None
        self.result = None
        self.error = None
        self.deadline_exceeded = False


def _bounded_stream_timeout(timeout, remaining):
    if timeout is None:
        return remaining
    if isinstance(timeout, tuple):
        return tuple(_bounded_stream_timeout(value, remaining) for value in timeout)
    if isinstance(timeout, (int, float)):
        return min(float(timeout), remaining)
    return timeout


def _close_stream_response(state):
    with state.lock:
        response = state.response
    _close_response(response)


def _set_stream_response(state, response):
    with state.lock:
        state.response = response
        cancelled = state.cancelled.is_set()
    return cancelled


def _response_header(response, name):
    headers = getattr(response, "headers", {}) or {}
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    if value is None:
        value = headers.get(name.title())
    return value


def _parse_content_range(value):
    match = re.fullmatch(r"\s*bytes\s+(\d+)-(\d+)/(\d+)\s*", str(value or ""))
    if match is None:
        return None
    start, end, total = (int(item) for item in match.groups())
    if end < start or total <= end:
        return None
    return start, end, total


def _iter_response_chunks(response):
    try:
        for chunk in response.iter_content(chunk_size=HTTP_PIPE_CHUNK_SIZE):
            if not chunk:
                continue
            for offset in range(0, len(chunk), HTTP_PIPE_CHUNK_SIZE):
                yield chunk[offset : offset + HTTP_PIPE_CHUNK_SIZE]
    finally:
        _close_response(response)


def _read_response_prefix(response, limit):
    chunks = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=HTTP_PIPE_CHUNK_SIZE):
            if not chunk:
                continue
            remaining = limit - size
            if remaining <= 0:
                break
            piece = chunk[:remaining]
            chunks.append(piece)
            size += len(piece)
            if size >= limit:
                break
    finally:
        _close_response(response)
    return b"".join(chunks)


def _mp4_box_header(data, offset):
    if offset < 0 or offset + 8 > len(data):
        return None
    size = struct.unpack_from(">I", data, offset)[0]
    kind = bytes(data[offset + 4 : offset + 8])
    header_size = 8
    if size == 1:
        if offset + 16 > len(data):
            return None
        size = struct.unpack_from(">Q", data, offset + 8)[0]
        header_size = 16
    elif size == 0:
        size = len(data) - offset
    if size < header_size:
        return None
    return kind, size, header_size


def _find_mp4_mdat(data):
    offset = 0
    while offset + 8 <= len(data):
        header = _mp4_box_header(data, offset)
        if header is None:
            return None
        kind, size, header_size = header
        if kind == b"mdat":
            return offset, size, header_size
        if size > len(data) - offset:
            return None
        offset += size
    return None


_MP4_CONTAINER_BOXES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"edts",
    b"dinf",
    b"mvex",
    b"moof",
    b"traf",
    b"mfra",
    b"udta",
    b"meta",
    b"ilst",
}


def _patch_mp4_chunk_offsets(moov, shift):
    patched = bytearray(moov)

    def walk(start, end):
        offset = start
        while offset + 8 <= end:
            header = _mp4_box_header(patched, offset)
            if header is None:
                raise RpaAdapterError("invalid MP4 metadata box")
            kind, size, header_size = header
            box_end = offset + size
            if box_end > end or box_end > len(patched):
                raise RpaAdapterError("truncated MP4 metadata box")
            payload = offset + header_size
            if kind == b"stco":
                if payload + 8 > box_end:
                    raise RpaAdapterError("invalid MP4 stco box")
                count = struct.unpack_from(">I", patched, payload + 4)[0]
                table_end = payload + 8 + count * 4
                if table_end > box_end:
                    raise RpaAdapterError("truncated MP4 stco table")
                for index in range(count):
                    entry_offset = payload + 8 + index * 4
                    value = struct.unpack_from(">I", patched, entry_offset)[0] + shift
                    if value > 0xFFFFFFFF:
                        raise RpaAdapterError("MP4 chunk offset exceeds stco range")
                    struct.pack_into(">I", patched, entry_offset, value)
            elif kind == b"co64":
                if payload + 8 > box_end:
                    raise RpaAdapterError("invalid MP4 co64 box")
                count = struct.unpack_from(">I", patched, payload + 4)[0]
                table_end = payload + 8 + count * 8
                if table_end > box_end:
                    raise RpaAdapterError("truncated MP4 co64 table")
                for index in range(count):
                    entry_offset = payload + 8 + index * 8
                    value = struct.unpack_from(">Q", patched, entry_offset)[0] + shift
                    struct.pack_into(">Q", patched, entry_offset, value)
            elif kind in _MP4_CONTAINER_BOXES:
                child_start = payload + 4 if kind == b"meta" else payload
                if child_start <= box_end:
                    walk(child_start, box_end)
            offset = box_end

    walk(0, len(patched))
    return bytes(patched)


def _range_request(session, video_url, byte_range, timeout, deadline_at, state):
    remaining = max(0.0, deadline_at - time.monotonic())
    if remaining <= 0:
        raise RpaAdapterError("video stream exceeded total deadline")
    response = session.get(
        video_url,
        headers={"Range": f"bytes={byte_range}"},
        stream=True,
        timeout=_bounded_stream_timeout(timeout, remaining),
    )
    if _set_stream_response(state, response):
        _close_response(response)
        return None
    return response


def _iter_reordered_mp4(video_url, session, timeout, deadline_at, max_bytes, state):
    """Yield an EOF-moov MP4 as faststart bytes using bounded HTTP ranges.

    A non-seekable FFmpeg input cannot consume the common ``mdat`` then
    ``moov`` layout. Range requests let us hold only the small metadata box,
    relocate it before the media payload, and keep the payload streamed.
    """
    probe = _range_request(
        session,
        video_url,
        f"0-{MP4_METADATA_PROBE_BYTES - 1}",
        timeout,
        deadline_at,
        state,
    )
    if probe is None:
        return
    try:
        _raise_for_status(probe, "stream video to FFmpeg", include_body=False)
        content_range = _parse_content_range(_response_header(probe, "Content-Range"))
        if getattr(probe, "status_code", None) != 206 or content_range is None:
            yield from _iter_response_chunks(probe)
            return
        _start, _end, total_size = content_range
        if total_size > max_bytes:
            raise RpaAdapterError("streamed video exceeds maximum size")
        prefix_probe = _read_response_prefix(probe, MP4_METADATA_PROBE_BYTES)
        mdat = _find_mp4_mdat(prefix_probe)
        if mdat is None:
            response = _range_request(
                session, video_url, "0-", timeout, deadline_at, state
            )
            if response is None:
                return
            try:
                _raise_for_status(response, "stream video to FFmpeg", include_body=False)
                declared_size = _response_header(response, "Content-Length")
                try:
                    if declared_size is not None and int(declared_size) > max_bytes:
                        raise RpaAdapterError("streamed video exceeds maximum size")
                except (TypeError, ValueError):
                    pass
                yield from _iter_response_chunks(response)
            finally:
                _close_response(response)
            return

        mdat_offset, mdat_size, _mdat_header_size = mdat
        if mdat_size == 0 or mdat_offset + mdat_size > total_size:
            raise RpaAdapterError("streamed MP4 has an invalid mdat range")
        moov_offset = mdat_offset + mdat_size
        moov_size = total_size - moov_offset
        if moov_size <= 8 or moov_size > MAX_MP4_MOOV_BYTES:
            raise RpaAdapterError("streamed MP4 metadata exceeds bounded range")

        moov_response = _range_request(
            session,
            video_url,
            f"{moov_offset}-{total_size - 1}",
            timeout,
            deadline_at,
            state,
        )
        if moov_response is None:
            return
        try:
            _raise_for_status(
                moov_response, "stream video to FFmpeg", include_body=False
            )
            moov = _read_response_prefix(moov_response, moov_size)
        finally:
            _close_response(moov_response)
        moov_header = _mp4_box_header(moov, 0)
        if (
            len(moov) != moov_size
            or moov_header is None
            or moov_header[0] != b"moov"
            or moov_header[1] != moov_size
        ):
            raise RpaAdapterError("streamed MP4 moov range is invalid")
        relocated_moov = _patch_mp4_chunk_offsets(moov, moov_size)

        yield prefix_probe[:mdat_offset]
        yield relocated_moov

        mdat_response = _range_request(
            session,
            video_url,
            f"{mdat_offset}-{moov_offset - 1}",
            timeout,
            deadline_at,
            state,
        )
        if mdat_response is None:
            return
        mdat_bytes = 0
        try:
            _raise_for_status(
                mdat_response, "stream video to FFmpeg", include_body=False
            )
            for chunk in _iter_response_chunks(mdat_response):
                mdat_bytes += len(chunk)
                if mdat_bytes > mdat_size:
                    raise RpaAdapterError("streamed MP4 mdat range is too large")
                yield chunk
        finally:
            _close_response(mdat_response)
        if mdat_bytes != mdat_size:
            raise RpaAdapterError("streamed MP4 mdat range is truncated")
    finally:
        _close_response(probe)


def _stream_video_worker(
    video_url,
    ffmpeg_process,
    session,
    timeout,
    max_bytes,
    started,
    deadline_at,
    state,
    normalize_mp4,
):
    response = None
    try:
        if state.cancelled.is_set():
            return
        remaining = max(0.0, deadline_at - time.monotonic())
        if remaining <= 0:
            raise RpaAdapterError("video stream exceeded total deadline")
        written = 0
        if normalize_mp4:
            chunks = _iter_reordered_mp4(
                video_url,
                session,
                timeout,
                deadline_at,
                max_bytes,
                state,
            )
        else:
            response = session.get(
                video_url,
                stream=True,
                timeout=_bounded_stream_timeout(timeout, remaining),
            )
            if _set_stream_response(state, response):
                return
            _raise_for_status(
                response, "stream video to FFmpeg", include_body=False
            )
            content_length = _response_header(response, "Content-Length")
            try:
                declared_size = int(content_length) if content_length is not None else None
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                raise RpaAdapterError("streamed video exceeds maximum size")
            chunks = _iter_response_chunks(response)

        for chunk in chunks:
            if state.cancelled.is_set():
                return
            if time.monotonic() >= deadline_at:
                raise RpaAdapterError("video stream exceeded total deadline")
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                raise RpaAdapterError("streamed video exceeds maximum size")
            ffmpeg_process.stdin.write(chunk)
            if state.cancelled.is_set():
                return
            if time.monotonic() >= deadline_at:
                raise RpaAdapterError("video stream exceeded total deadline")
        if state.cancelled.is_set():
            return
        if time.monotonic() >= deadline_at:
            raise RpaAdapterError("video stream exceeded total deadline")
        ffmpeg_process.stdin.close()
        if state.cancelled.is_set():
            return
        state.result = {
            "bytes": written,
            "networkSec": round(time.monotonic() - started, 6),
        }
    except RpaAdapterError as exc:
        if not state.cancelled.is_set():
            state.error = exc
            state.deadline_exceeded = str(exc) == (
                "video stream exceeded total deadline"
            )
    except Exception as exc:
        if not state.cancelled.is_set():
            try:
                raise RpaAdapterError(
                    "unable to stream video to FFmpeg"
                ) from exc
            except RpaAdapterError as wrapped:
                state.error = wrapped
    finally:
        _close_response(response)


def _abort_video_stream(state, ffmpeg_process, *, background_cleanup=False):
    state.cancelled.set()
    _close_stream_response(state)
    _close_ffmpeg_stdin(ffmpeg_process)
    if background_cleanup:
        _terminate_ffmpeg_process_in_background(ffmpeg_process)
    else:
        _terminate_ffmpeg_process(ffmpeg_process)


def stream_video_to_ffmpeg(
    video_url,
    ffmpeg_process,
    session=None,
    timeout=300,
    max_bytes=DEFAULT_VIDEO_MAX_BYTES,
    deadline_sec=DEFAULT_DOWNLOAD_DEADLINE_SEC,
    normalize_mp4=False,
):
    if not str(video_url or "").strip():
        raise RpaAdapterError("videoUrl is required")
    session = _default_session() if session is None else session
    started = time.monotonic()
    deadline_at = started + max(0.0, float(deadline_sec))
    remaining = max(0.0, deadline_at - time.monotonic())
    state = _VideoStreamState()
    worker = threading.Thread(
        target=_stream_video_worker,
        args=(
            video_url,
            ffmpeg_process,
            session,
            timeout,
            max_bytes,
            started,
            deadline_at,
            state,
            normalize_mp4,
        ),
        daemon=True,
    )
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        _abort_video_stream(
            state, ffmpeg_process, background_cleanup=True
        )
        raise RpaAdapterError("video stream exceeded total deadline")
    if state.error is not None:
        _abort_video_stream(
            state,
            ffmpeg_process,
            background_cleanup=state.deadline_exceeded,
        )
        raise state.error
    if state.result is None:
        _abort_video_stream(state, ffmpeg_process)
        raise RpaAdapterError("unable to stream video to FFmpeg")
    return state.result


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
