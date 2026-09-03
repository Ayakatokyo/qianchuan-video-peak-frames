from datetime import datetime
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
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
DEFAULT_URL_PROBE_BYTES = 1024 * 1024
DEFAULT_URL_PROBE_TIMEOUT_SEC = 30
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 3600
RUNNING_TASK_STATUSES = {"pending", "running"}
SUCCESS_TASK_STATUSES = {"completed", "partial_success"}


@dataclass(frozen=True)
class UrlCapabilities:
    """Bounded HTTP observations for a video URL.

    The signed URL itself is deliberately not retained in this value.  It is
    needed only as an in-process FFmpeg/request argument and must not become a
    log or report field.
    """

    url_host: str
    status_code: int
    content_length: int | None
    accept_ranges: bool
    moov_offset: int | None
    moov_position: str
    probe_method: str
    bytes_read: int
    elapsed_sec: float
    response_headers: dict[str, str]
    head_status_code: int | None = None
    range_status_code: int | None = None
    content_type: str | None = None

    @property
    def supports_range(self):
        return self.accept_ranges or self.range_status_code == 206

    @property
    def moov_at_head(self):
        return self.moov_position == "head"

    @property
    def headers(self):
        """Compatibility alias exposing only the selected safe headers."""
        return dict(self.response_headers)

    def as_dict(self):
        return {
            "urlHost": self.url_host,
            "statusCode": self.status_code,
            "contentLength": self.content_length,
            "acceptRanges": self.accept_ranges,
            "supportsRange": self.supports_range,
            "moovOffset": self.moov_offset,
            "moovPosition": self.moov_position,
            "moovAtHead": self.moov_at_head,
            "probeMethod": self.probe_method,
            "bytesRead": self.bytes_read,
            "elapsedSec": self.elapsed_sec,
            "responseHeaders": dict(self.response_headers),
            "headStatusCode": self.head_status_code,
            "rangeStatusCode": self.range_status_code,
            "contentType": self.content_type,
        }

    to_dict = as_dict


def _default_session():
    try:
        import requests
    except ImportError as exc:
        raise RpaAdapterError(
            "requests must be installed to call the Yuce RPA gateway"
        ) from exc
    return requests.Session()


def _video_url_host(video_url):
    parsed = urlsplit(str(video_url or "").strip())
    return parsed.hostname or "<invalid-host>"


def redact_url(video_url):
    """Return a host-only URL description suitable for diagnostics."""
    parsed = urlsplit(str(video_url or "").strip())
    scheme = parsed.scheme.lower() if parsed.scheme else "unknown"
    host = parsed.hostname or "<invalid-host>"
    return f"{scheme}://{host}/[redacted]"


_redact_video_url = redact_url


def _header_value(headers, name):
    wanted = str(name).lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return value
    return None


def _safe_header_snapshot(headers):
    allowed = {
        "accept-ranges",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
    }
    snapshot = {}
    for key, value in (headers or {}).items():
        normalized_key = str(key).lower()
        if normalized_key in allowed:
            snapshot[normalized_key] = str(value)
    return snapshot


def _parse_content_length(headers):
    value = _header_value(headers, "Content-Length")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_content_range(headers):
    value = _header_value(headers, "Content-Range")
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*", str(value), re.IGNORECASE
    )
    if match is None:
        return None
    start, end, total = match.groups()
    return int(start), int(end), None if total == "*" else int(total)


def _find_mp4_moov_offset(payload, base_offset=0):
    """Find a top-level MP4 moov atom without scanning beyond *payload*."""
    data = bytes(payload or b"")
    offset = 0
    data_length = len(data)
    while offset + 8 <= data_length:
        size = int.from_bytes(data[offset : offset + 4], "big")
        box_type = data[offset + 4 : offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > data_length:
                break
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header_size = 16
        elif size == 0:
            size = data_length - offset
        if size < header_size or offset + size > data_length:
            break
        if box_type == b"moov":
            return base_offset + offset
        offset += size

    # A tail range can start inside the moov header.  The marker still gives
    # a useful diagnostic offset, while the bounded scan above remains the
    # authority whenever a complete top-level atom is present.
    marker = data.find(b"moov")
    if marker >= 0:
        return max(0, base_offset + marker - 4)
    return None


def _read_probe_bytes(response, limit):
    collected = bytearray()
    iterator = response.iter_content(chunk_size=min(64 * 1024, limit))
    for chunk in iterator:
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode()
        remaining = limit - len(collected)
        if remaining <= 0:
            break
        collected.extend(chunk[:remaining])
        if len(collected) >= limit:
            break
    return bytes(collected)


def _close_response(response):
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _response_status(response):
    value = getattr(response, "status_code", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _raise_url_response_error(response, host):
    status = _response_status(response)
    if status >= 400:
        raise RpaAdapterError(
            f"video URL probe on host {host} returned HTTP {status}"
        )
    try:
        response.raise_for_status()
    except Exception:
        raise RpaAdapterError(
            f"video URL probe on host {host} returned HTTP {status or 'error'}"
        )


def probe_url_capabilities(
    video_url,
    *,
    session=None,
    timeout=DEFAULT_URL_PROBE_TIMEOUT_SEC,
    probe_bytes=DEFAULT_URL_PROBE_BYTES,
    logger=None,
):
    """Probe a signed video URL using bounded HEAD and Range GET requests.

    The GET body is capped and closed as soon as enough bytes have been
    inspected, so a server that ignores Range cannot make Python buffer the
    complete MP4.  The URL is passed to the HTTP client only in-process; all
    diagnostics use its host instead.
    """
    url = str(video_url or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RpaAdapterError("videoUrl must be an absolute HTTP(S) URL")
    try:
        timeout_value = float(timeout)
        probe_limit = int(probe_bytes)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timeout and probe_bytes must be numeric") from exc
    if timeout_value <= 0 or probe_limit <= 0:
        raise ValueError("timeout and probe_bytes must be greater than zero")

    session = _default_session() if session is None else session
    logger = logging.getLogger(__name__) if logger is None else logger
    host = _video_url_host(url)
    started = time.monotonic()
    head_status = None
    head_headers = {}
    head_ok = False
    head = getattr(session, "head", None)
    if callable(head):
        response = None
        try:
            response = head(url, allow_redirects=True, timeout=timeout_value)
            head_status = _response_status(response)
            head_headers = dict(getattr(response, "headers", {}) or {})
            head_ok = 200 <= head_status < 400
        except Exception:
            # A number of object stores reject HEAD even though GET works.
            head_ok = False
        finally:
            if response is not None:
                _close_response(response)

    range_end = probe_limit - 1
    response = None
    try:
        response = session.get(
            url,
            headers={"Range": f"bytes=0-{range_end}"},
            stream=True,
            timeout=timeout_value,
        )
        _raise_url_response_error(response, host)
        get_status = _response_status(response)
        get_headers = dict(getattr(response, "headers", {}) or {})
        prefix = _read_probe_bytes(response, probe_limit)
        get_range = _parse_content_range(get_headers)
    except RpaAdapterError:
        logger.error(
            "video URL capability probe failed host=%s phase=range-get",
            host,
        )
        raise
    except Exception as exc:
        logger.error(
            "video URL capability probe failed host=%s phase=range-get", host
        )
        raise RpaAdapterError(
            f"unable to probe video URL on host {host}"
        ) from None
    finally:
        if response is not None:
            _close_response(response)

    total_length = None
    if get_range is not None and get_range[2] is not None:
        total_length = get_range[2]
    if total_length is None and head_ok:
        total_length = _parse_content_length(head_headers)
    if total_length is None and get_status != 206:
        total_length = _parse_content_length(get_headers)

    accept_ranges_header = _header_value(get_headers, "Accept-Ranges")
    accept_ranges = str(accept_ranges_header or "").strip().lower() == "bytes"
    range_status = get_status if get_status == 206 else None
    supports_range = accept_ranges or range_status == 206

    prefix_moov_offset = _find_mp4_moov_offset(prefix, 0)
    moov_offset = prefix_moov_offset
    moov_position = None
    if prefix_moov_offset is not None:
        moov_position = "head"

    bytes_read = len(prefix)
    if (
        moov_position is None
        and supports_range
        and total_length is not None
        and total_length > probe_limit
    ):
        tail_start = max(0, total_length - probe_limit)
        tail_response = None
        try:
            tail_response = session.get(
                url,
                headers={"Range": f"bytes={tail_start}-{total_length - 1}"},
                stream=True,
                timeout=timeout_value,
            )
            _raise_url_response_error(tail_response, host)
            tail = _read_probe_bytes(tail_response, probe_limit)
            bytes_read += len(tail)
            tail_moov_offset = _find_mp4_moov_offset(tail, tail_start)
            if tail_moov_offset is not None:
                moov_offset = tail_moov_offset
                moov_position = "tail"
        except RpaAdapterError:
            raise
        except Exception:
            # The prefix capability remains useful even when a tail probe is
            # unavailable; direct FFmpeg will decide whether sequential read
            # can still decode the source.
            logger.warning(
                "video URL tail probe unavailable host=%s", host
            )
        finally:
            if tail_response is not None:
                _close_response(tail_response)

    if moov_position is None:
        moov_position = "unknown"

    effective_headers = _safe_header_snapshot(head_headers)
    effective_headers.update(_safe_header_snapshot(get_headers))
    elapsed_sec = round(max(0.0, time.monotonic() - started), 6)
    capabilities = UrlCapabilities(
        url_host=host,
        status_code=get_status,
        content_length=total_length,
        accept_ranges=accept_ranges,
        moov_offset=moov_offset,
        moov_position=moov_position,
        probe_method="HEAD+GET" if head_ok else "GET",
        bytes_read=bytes_read,
        elapsed_sec=elapsed_sec,
        response_headers=effective_headers,
        head_status_code=head_status,
        range_status_code=range_status,
        content_type=_header_value(get_headers, "Content-Type")
        or _header_value(head_headers, "Content-Type"),
    )
    logger.info(
        "video URL capability probe host=%s method=%s status=%s "
        "content_length=%s accept_ranges=%s moov_position=%s "
        "bytes_read=%s elapsed_sec=%.6f",
        capabilities.url_host,
        capabilities.probe_method,
        capabilities.status_code,
        capabilities.content_length,
        capabilities.accept_ranges,
        capabilities.moov_position,
        capabilities.bytes_read,
        capabilities.elapsed_sec,
    )
    return capabilities


def probe_video_url(video_url, **kwargs):
    """Backward-compatible descriptive alias for URL capability probing."""
    return probe_url_capabilities(video_url, **kwargs)


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
    text = re.sub(
        r'(?i)("file[_-]?url"\s*:\s*")[^"]*',
        r'\1[redacted-url]',
        text,
    )
    text = re.sub(r"https?://[^\s\"']+", "[redacted-url]", text)
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


def _task_response_payload(result):
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    if isinstance(data, dict):
        content = data.get("result_content")
        if isinstance(content, dict):
            return content
        return data
    return result


def _task_group_id(result):
    if not isinstance(result, dict):
        return None
    for container in (result, result.get("data")):
        if isinstance(container, dict):
            value = container.get("task_group_id") or container.get("taskGroupId")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _task_failure(message, task_group_id=None, **diagnostics):
    if task_group_id:
        diagnostics["remoteTaskGroupId"] = task_group_id
    return RpaAdapterError(message, diagnostics)


def _first_file_url(files):
    if not isinstance(files, list):
        return None
    for item in files:
        if not isinstance(item, dict):
            continue
        value = item.get("fileUrl")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


_SENSITIVE_TASK_ERROR_KEYS = {
    "token",
    "accesstoken",
    "fileurl",
    "authorization",
    "session",
    "sessionid",
    "cookie",
    "cookies",
    "secret",
}


def _safe_task_errors(errors):
    if not isinstance(errors, list):
        return None

    def sanitize(value):
        if isinstance(value, dict):
            return {
                key: sanitize(item)
                for key, item in value.items()
                if str(key).lower().replace("_", "") not in _SENSITIVE_TASK_ERROR_KEYS
            }
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"https?://[^\s]+", "[redacted-url]", value)
        return value

    return sanitize(errors)


def _unpack_task_result(result, task_group_id):
    payload = _task_response_payload(result)
    status = str(payload.get("status") or "").strip().lower()
    if payload.get("success") is False or payload.get("code") not in (None, 200, "200"):
        raise _task_failure(
            "RPA fetch task response was not successful", task_group_id, gatewayPhase="poll"
        )
    if status not in SUCCESS_TASK_STATUSES:
        if status in RUNNING_TASK_STATUSES:
            return status, None, None
        errors = payload.get("errors")
        raise _task_failure(
            f"RPA fetch task ended with invalid status: {status or '<missing>'}",
            task_group_id,
            gatewayPhase="poll",
            rpaErrors=_safe_task_errors(errors),
        )

    file_url = _first_file_url(payload.get("files"))
    if file_url:
        if payload.get("failedInstances") not in (None, 0):
            raise _task_failure(
                "RPA fetch task files contain failed instances",
                task_group_id,
                gatewayPhase="poll",
            )
        metadata = {"remoteTaskGroupId": task_group_id}
        if status == "partial_success" and isinstance(payload.get("errors"), list):
            metadata["rpaPartialErrors"] = _safe_task_errors(payload["errors"])
        return status, GatewayFile(file_url), metadata

    records_payload = payload.get("payload")
    if records_payload is not None:
        try:
            records = normalize_gateway_records({"payload": records_payload})
        except RpaAdapterError as exc:
            raise _task_failure(
                str(exc), task_group_id, gatewayPhase="poll"
            ) from exc
        return status, records, {"remoteTaskGroupId": task_group_id}

    errors = payload.get("errors")
    raise _task_failure(
        "RPA fetch task completed without a usable file or payload",
        task_group_id,
        gatewayPhase="poll",
        rpaErrors=_safe_task_errors(errors),
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
    request_payload = {
        "authorization": authorization,
        "agent_session_id": session_id,
        "data_source_type": DATA_SOURCE_TYPE,
        "platform": PLATFORM,
        "function_code": FUNCTION_CODE,
        "business_params": business_params,
        "shop_id": str(shop_id).strip(),
    }
    if task_group_id is None:
        try:
            response = session.post(
                f"{api_base}/adg/v1/agent/fetch/tasks",
                json=request_payload,
                headers=HEADERS,
                cookies=cookies,
                timeout=timeout_sec,
            )
        except Exception as exc:
            raise RpaAdapterError(
                f"unable to submit RPA material diagnosis: {exc}",
                {
                    "gatewayPhase": "submit",
                    "rpaFailureClass": "transport_error",
                    "transportErrorType": type(exc).__name__,
                },
            ) from exc
        _raise_for_status(response, "submit RPA material diagnosis")
        try:
            task_group_id = _task_group_id(response.json())
        except Exception as exc:
            raise _task_failure(
                "RPA submit response is not valid JSON", gatewayPhase="submit"
            ) from exc
        if not task_group_id:
            raise _task_failure(
                "RPA submit response is missing task_group_id", gatewayPhase="submit"
            )
        if callable(on_task_submitted):
            on_task_submitted(task_group_id)
    elif not isinstance(task_group_id, str) or not task_group_id.strip():
        raise RpaAdapterError("task_group_id must be a non-empty string")
    else:
        task_group_id = task_group_id.strip()

    poll_deadline = time.monotonic() + min(float(timeout_sec), POLL_TIMEOUT_SECONDS)
    while True:
        try:
            response = session.post(
                f"{api_base}/adg/v1/agent/fetch/tasks/status",
                json={**request_payload, "task_group_id": task_group_id},
                headers=HEADERS,
                cookies=cookies,
                timeout=timeout_sec,
            )
        except Exception as exc:
            raise _task_failure(
                f"unable to poll RPA material diagnosis: {exc}",
                task_group_id,
                gatewayPhase="poll",
                rpaFailureClass="transport_error",
                transportErrorType=type(exc).__name__,
            ) from exc
        try:
            _raise_for_status(response, "poll RPA material diagnosis")
            status, gateway_result, metadata = _unpack_task_result(
                response.json(), task_group_id
            )
        except RpaAdapterError as exc:
            exc.diagnostics.setdefault("remoteTaskGroupId", task_group_id)
            raise
        if status in SUCCESS_TASK_STATUSES:
            break
        if time.monotonic() >= poll_deadline:
            raise _task_failure(
                "RPA material diagnosis polling timed out",
                task_group_id,
                gatewayPhase="poll",
                rpaFailureClass="poll_timeout",
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    if isinstance(gateway_result, list):
        records = gateway_result
        diagnosis_csv_path = None
    else:
        if diagnosis_csv_dir is None:
            raise _task_failure(
                "diagnosis_csv_dir is required for RPA files responses", task_group_id
            )
        try:
            target = artifact_path(diagnosis_csv_dir, business_params["material_id"], ".csv")
        except ValueError as exc:
            raise _task_failure(str(exc), task_group_id) from exc
        try:
            download_diagnosis_csv(gateway_result.file_url, target, session=session)
            records = parse_diagnosis_csv(target)
        except RpaCsvError as exc:
            raise _task_failure(str(exc), task_group_id, gatewayPhase="download") from exc
        except RpaAdapterError as exc:
            exc.diagnostics.setdefault("remoteTaskGroupId", task_group_id)
            raise
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
