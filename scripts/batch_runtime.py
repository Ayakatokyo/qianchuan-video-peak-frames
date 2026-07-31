"""Shared batch lifecycle state and runtime protection helpers."""

import json
import os
import socket
import time
import uuid
from pathlib import Path


class BatchLockError(RuntimeError):
    def __init__(self, message, *, stale=False, payload=None):
        super().__init__(message)
        self.stale = stale
        self.payload = payload or {}


def stage_counts(materials, report_ready=False):
    """Return explicit lifecycle counts without collapsing status meanings."""
    counts = {
        "rpaSelectedCount": 0,
        "recordsPersistedCount": 0,
        "postprocessSucceededCount": 0,
        "reportReadyCount": 0,
    }
    for entry in materials or ():
        if not isinstance(entry, dict):
            continue
        material_id = str(entry.get("materialId") or "").strip()
        if entry.get("rpaStatus") == "selected":
            counts["rpaSelectedCount"] += 1
        records_path = entry.get("diagnosisRecordsPath")
        if entry.get("recordsPersisted") is True or (
            "recordsPersisted" not in entry
            and isinstance(records_path, str)
            and records_path.strip()
        ):
            counts["recordsPersistedCount"] += 1
        if entry.get("postprocessSucceeded") is True or (
            "postprocessSucceeded" not in entry
            and entry.get("postprocessStatus") == "succeeded"
        ):
            counts["postprocessSucceededCount"] += 1
    counts["reportReadyCount"] = 1 if report_ready else 0
    return counts


def mark_report_ready(manifest_path, report_data_path, report_html_path):
    manifest_file = Path(manifest_path)
    data_file = Path(report_data_path)
    html_file = Path(report_html_path)
    if not data_file.is_file() or not html_file.is_file():
        return False
    try:
        report_data = json.loads(data_file.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(report_data, dict) or report_data.get("status") != "complete":
        return False
    if not isinstance(manifest, dict):
        return False
    counts = manifest.get("stageCounts")
    if not isinstance(counts, dict):
        counts = stage_counts(manifest.get("submittedMaterials", []))
    counts["reportReadyCount"] = 1
    manifest["stageCounts"] = counts
    manifest["reportReady"] = {
        "status": "ready",
        "dataPath": str(data_file),
        "htmlPath": str(html_file),
    }
    staging = manifest_file.with_name(f".{manifest_file.name}.report-ready.tmp")
    try:
        staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, manifest_file)
    except (OSError, TypeError, ValueError):
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def new_run_id():
    return uuid.uuid4().hex


def lock_payload(run_id, manifest_path):
    return {
        "runId": run_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "startedAt": time.time(),
        "manifestPath": str(manifest_path),
    }


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _pid_is_alive(pid, hostname):
    if hostname and hostname != socket.gethostname():
        return False
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_value <= 0:
        return False
    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class BatchRunLock:
    def __init__(self, path, payload):
        self.path = Path(path)
        self.payload = payload
        self._released = False

    def release(self):
        if self._released:
            return
        current = read_json(self.path)
        if isinstance(current, dict) and current.get("runId") == self.payload.get("runId"):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._released = True

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.release()


def acquire_run_lock(path, manifest_path, run_id=None):
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = lock_payload(run_id or new_run_id(), manifest_path)
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except FileExistsError as exc:
        existing = read_json(lock_path) or {}
        stale = not _pid_is_alive(existing.get("pid"), existing.get("hostname"))
        state = "stale" if stale else "active"
        raise BatchLockError(
            f"batch run lock is {state}: {lock_path}",
            stale=stale,
            payload=existing,
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
    except Exception:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return BatchRunLock(lock_path, payload)
