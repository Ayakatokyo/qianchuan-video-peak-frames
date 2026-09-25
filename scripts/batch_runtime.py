"""Shared batch lifecycle state and runtime protection helpers."""

import fcntl
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

from schemas import SchemaError, validate_manifest, validate_report_data


class BatchLockError(RuntimeError):
    def __init__(self, message, *, stale=False, payload=None):
        super().__init__(message)
        self.stale = stale
        self.payload = payload or {}


def write_json_atomically(path, payload):
    """Write formatted JSON through a same-directory temporary file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return target


def _resolved_artifact_path(raw_path, base_dir=None):
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    return path


def _records_artifact_is_valid(entry, base_dir=None):
    material_id = str(entry.get("materialId") or "").strip()
    records_path = _resolved_artifact_path(entry.get("diagnosisRecordsPath"), base_dir)
    if not material_id or records_path is None or not records_path.is_file():
        return False
    try:
        payload = json.loads(records_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, list):
        return False
    return any(
        isinstance(record, dict)
        and str(record.get("materialId") or "").strip() == material_id
        for record in payload
    )


def stage_counts(materials, report_ready=False, base_dir=None):
    """Return explicit lifecycle counts without collapsing status meanings."""
    counts = {
        "rpaRequestCount": 0,
        "rpaAcceptedCount": 0,
        "rpaSelectedCount": 0,
        "recordsPersistedCount": 0,
        "postprocessSucceededCount": 0,
        "reportReadyCount": 0,
    }
    for entry in materials or ():
        if not isinstance(entry, dict):
            continue
        if entry.get("rpaStatus") == "selected":
            counts["rpaSelectedCount"] += 1
        if entry.get("rpaStatus") in {"submitted", "selected", "rejected"}:
            counts["rpaRequestCount"] += 1
        records_persisted = entry.get("recordsPersisted")
        if records_persisted is False:
            # This is an explicit negative result, not an unverifiable legacy entry.
            pass
        elif records_persisted is True and _records_artifact_is_valid(
            entry, base_dir=base_dir
        ):
            counts["recordsPersistedCount"] += 1
        else:
            # Missing/invalid legacy fields must not be inferred from a path string.
            counts["recordsPersistedUnknownCount"] = (
                counts.get("recordsPersistedUnknownCount", 0) + 1
            )
        if (
            entry.get("rpaStatus") == "selected"
            and entry.get("rpaCsvVerified") is True
            and entry.get("recordsPersisted") is True
            and _records_artifact_is_valid(entry, base_dir=base_dir)
        ):
            counts["rpaAcceptedCount"] += 1
        if entry.get("postprocessSucceeded") is True or (
            "postprocessSucceeded" not in entry
            and entry.get("postprocessStatus") == "succeeded"
        ):
            counts["postprocessSucceededCount"] += 1
    if unknown_count := counts.pop("recordsPersistedUnknownCount", 0):
        counts["recordsPersistedUnknownCount"] = unknown_count
    counts["reportReadyCount"] = 1 if report_ready else 0
    return counts


def mark_report_ready(manifest_path, report_data_path, report_html_path):
    manifest_file = Path(manifest_path)
    data_file = Path(report_data_path)
    html_file = Path(report_html_path)
    if not manifest_file.is_file() or not data_file.is_file() or not html_file.is_file():
        return False
    try:
        html_text = html_file.read_text(encoding="utf-8")
        report_data = json.loads(data_file.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    try:
        normalized_manifest = validate_manifest(manifest, require_run_id=True)
        validate_report_data(report_data, run_id=normalized_manifest["runId"])
    except SchemaError:
        return False
    if not _report_matches_manifest(report_data, normalized_manifest):
        return False
    del report_data  # Do not retain the large disk snapshot while parsing HTML data.
    embedded_data = _embedded_report_data(html_text)
    if embedded_data is None:
        return False
    try:
        validate_report_data(embedded_data, run_id=normalized_manifest["runId"])
    except SchemaError:
        return False
    if not _report_matches_manifest(embedded_data, normalized_manifest):
        return False
    if not _embedded_assets_exist(embedded_data, html_file.parent):
        return False
    counts = manifest.get("stageCounts")
    if not isinstance(counts, dict):
        counts = stage_counts(
            manifest.get("submittedMaterials", []), base_dir=manifest_file.parent
        )
    counts["reportReadyCount"] = 1
    manifest["stageCounts"] = counts
    manifest["reportReady"] = {
        "status": "ready",
        "runId": manifest["runId"],
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


class _ReportDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._in_report_script = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self._in_report_script = (
            tag.lower() == "script" and attributes.get("id") == "report-data"
        )

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            self._in_report_script = False

    def handle_data(self, data):
        if self._in_report_script:
            self.parts.append(data)


def _embedded_report_data(html_text):
    if "__REPORT_DATA_JSON__" in html_text:
        return None
    lowered = html_text.lower()
    if 'id="report-data"' not in lowered and "id='report-data'" not in lowered:
        return None
    parser = _ReportDataParser()
    try:
        parser.feed(html_text)
        parser.close()
        if not parser.parts:
            return None
        payload = json.loads("".join(parser.parts))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _manifest_material_ids(manifest):
    try:
        manifest = validate_manifest(manifest)
    except SchemaError:
        return set()
    selected = manifest.get("selectedMaterials")
    if isinstance(selected, list):
        return {
            str(item.get("materialId") or "").strip()
            for item in selected
            if isinstance(item, dict)
            and item.get("postprocessStatus") == "succeeded"
            and str(item.get("materialId") or "").strip()
        }
    materials = manifest.get("materials")
    if isinstance(materials, dict):
        return {
            str(material_id).strip()
            for material_id, item in materials.items()
            if isinstance(item, dict) and item.get("status") == "succeeded"
        }
    if isinstance(materials, list):
        return {
            str(item.get("materialId") or "").strip()
            for item in materials
            if isinstance(item, dict) and item.get("status") == "succeeded"
        }
    return set()


def _report_matches_manifest(report_data, manifest):
    if not isinstance(report_data, dict) or not isinstance(manifest, dict):
        return False
    try:
        validate_report_data(report_data, run_id=manifest.get("runId"))
    except SchemaError:
        return False
    run_id = str(manifest.get("runId") or "").strip()
    pipeline_summary = report_data.get("pipelineSummary")
    if not isinstance(pipeline_summary, dict):
        return False
    report_run_id = str(report_data.get("runId") or "").strip()
    pipeline_run_id = str(pipeline_summary.get("runId") or "").strip()
    if not run_id or report_run_id != run_id or pipeline_run_id != run_id:
        return False
    materials = report_data.get("materials")
    if not isinstance(materials, list):
        return False
    report_ids = {
        str(item.get("materialId") or "").strip()
        for item in materials
        if isinstance(item, dict) and str(item.get("materialId") or "").strip()
    }
    manifest_ids = _manifest_material_ids(manifest)
    return bool(report_ids) and report_ids == manifest_ids


def _embedded_assets_exist(report_data, html_dir):
    export_href = report_data.get("highlightExportHref")
    if export_href:
        if not isinstance(export_href, str):
            return False
        target = (Path(html_dir) / export_href).resolve()
        if not target.is_relative_to(Path(html_dir).resolve()) or not target.is_file():
            return False
    for material in report_data.get("materials", []):
        for frame in material.get("highlightFrames", []):
            if not isinstance(frame, dict):
                return False
            source = frame.get("framePath")
            if not isinstance(source, str) or not source.strip():
                return False
            if source.startswith("data:"):
                continue
            if "://" in source:
                return False
            asset_path = (Path(html_dir) / source).resolve()
            try:
                asset_path.relative_to(Path(html_dir).resolve())
            except ValueError:
                return False
            if not asset_path.is_file():
                return False
    return True


def new_run_id():
    return uuid.uuid4().hex


def lock_payload(run_id, manifest_path):
    return {
        "lockProtocol": "flock-v1",
        "ownerToken": uuid.uuid4().hex,
        "runId": run_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "processStart": _process_start(os.getpid()),
        "startedAt": time.time(),
        "manifestPath": str(manifest_path),
    }


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _process_start(pid):
    """Return Linux's stable process-start token, when the platform exposes it."""
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_value <= 0:
        return None
    try:
        stat = Path(f"/proc/{pid_value}/stat").read_text(encoding="utf-8")
        _prefix, suffix = stat.rsplit(")", 1)
        return suffix.split()[19]
    except (OSError, IndexError, ValueError):
        return None


def _pid_is_alive(pid, hostname, process_start=None):
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
    # kill(pid, 0) also succeeds for zombies. They cannot finish or release a
    # legacy lock, so treating them as active permanently blocks recovery.
    try:
        stat = Path(f"/proc/{pid_value}/stat").read_text(encoding="utf-8")
        state = stat.rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError, ValueError):
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid_value), "-o", "stat="],
                capture_output=True, text=True, timeout=2, check=False,
            )
            state = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            state = ""
    if state.startswith(("Z", "X")):
        return False
    if process_start is not None:
        observed_start = _process_start(pid_value)
        if observed_start is not None and str(observed_start) != str(process_start):
            return False
    return True


def _read_lock_descriptor(descriptor):
    os.lseek(descriptor, 0, os.SEEK_SET)
    with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
        try:
            raw = stream.read()
            payload = json.loads(raw) if raw else None
        except (UnicodeError, json.JSONDecodeError):
            return False, None
    return not raw, payload if isinstance(payload, dict) else None


def _unlocked_lock_state(empty, payload):
    if empty:
        return "missing", None
    if not isinstance(payload, dict):
        return "invalid", None
    # For new locks the kernel lock is authoritative, including after SIGKILL
    # and PID reuse. Only legacy payloads need a process-liveness fallback.
    if payload.get("lockProtocol") == "flock-v1":
        return "stale", payload
    alive = _pid_is_alive(
        payload.get("pid"),
        payload.get("hostname"),
        payload.get("processStart"),
    )
    return ("active" if alive else "stale"), payload


def run_lock_state(path):
    """Observe a lock without creating, replacing, truncating or deleting it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "invalid", None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _empty, payload = _read_lock_descriptor(descriptor)
            # The owner may still be publishing its initial JSON payload.
            return "active", payload
        return _unlocked_lock_state(*_read_lock_descriptor(descriptor))
    finally:
        os.close(descriptor)


_run_locks = weakref.WeakSet()
_run_lock_fork_state = threading.local()


@contextmanager
def inherit_run_locks():
    """Keep run leases in a forked writer until that child process exits.

    Only the thread starting a bounded artifact-writing worker opts in; other
    forks must not prolong the owner lock merely by inheriting its descriptor.
    """
    previous = getattr(_run_lock_fork_state, "inherit", False)
    _run_lock_fork_state.inherit = True
    try:
        yield
    finally:
        _run_lock_fork_state.inherit = previous


class BatchRunLock:
    def __init__(self, path, payload, descriptor=None):
        self.path = Path(path)
        self.payload = payload
        self._descriptor = descriptor
        self._owner_pid = os.getpid()
        self._released = False
        if descriptor is not None:
            _run_locks.add(self)

    def release(self):
        if self._released:
            return
        self._released = True
        _run_locks.discard(self)
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            if self._owner_pid != os.getpid():
                return
            _empty, current = _read_lock_descriptor(descriptor)
            if (
                isinstance(current, dict)
                and current.get("ownerToken") == self.payload.get("ownerToken")
            ):
                # Retain the inode: unlinking lets a competing opener acquire
                # a different inode while another process still holds this one.
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
        finally:
            # close (not LOCK_UN) also leaves a parent's lock intact if an
            # inherited handle is released in a forked child.
            os.close(descriptor)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.release()


def _close_inherited_run_locks():
    if getattr(_run_lock_fork_state, "inherit", False):
        # RPA workers publish artifacts themselves and remain writers even if
        # their parent dies. Their watchdog ends the lease by exiting the child.
        # Do not implicitly transfer it to any grandchildren they may create.
        _run_lock_fork_state.inherit = False
        return
    # A forked child must not prolong the parent's flock after the parent dies.
    # release() detects the PID change and only closes the inherited descriptor.
    for lock in list(_run_locks):
        lock.release()


os.register_at_fork(after_in_child=_close_inherited_run_locks)


def acquire_run_lock(path, manifest_path, run_id=None, *, reclaim_stale=False):
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            _empty, existing = _read_lock_descriptor(descriptor)
            raise BatchLockError(
                f"batch run lock is active: {lock_path}",
                payload=existing or {},
            ) from exc
        state, existing = _unlocked_lock_state(*_read_lock_descriptor(descriptor))
        stale = state in {"stale", "invalid"}
        if state == "active" or (stale and not reclaim_stale):
            raise BatchLockError(
                f"batch run lock is {state}: {lock_path}",
                stale=stale,
                payload=existing or {},
            )
        payload = lock_payload(run_id or new_run_id(), manifest_path)
        encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        while encoded:
            written = os.write(descriptor, encoded)
            if written <= 0:
                raise OSError("could not write batch run lock")
            encoded = encoded[written:]
        os.fsync(descriptor)
        return BatchRunLock(lock_path, payload, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
