"""Durable, per-material receipts for locally retained RPA artifacts."""

from datetime import datetime, timezone
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

from path_safety import artifact_path, validate_material_id


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_STATES = ("submitted", "artifact_downloaded", "records_persisted")


class RpaArtifactReceiptError(ValueError):
    """Raised when a local RPA artifact receipt cannot be trusted."""


def receipt_path(receipts_dir, material_id):
    return artifact_path(receipts_dir, material_id, ".json")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _regular_file(path, *, label):
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise RpaArtifactReceiptError(f"RPA receipt {label} is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise RpaArtifactReceiptError(
            f"RPA receipt {label} must be a non-empty regular file"
        )
    return candidate, metadata


def _csv_metadata(path):
    csv_path, metadata = _regular_file(path, label="CSV")
    digest = hashlib.sha256()
    try:
        with csv_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RpaArtifactReceiptError("RPA receipt CSV cannot be read") from exc
    return csv_path, metadata.st_size, digest.hexdigest()


def _load_existing(path):
    if not path.exists():
        return None
    try:
        _regular_file(path, label="file")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RpaArtifactReceiptError) as exc:
        raise RpaArtifactReceiptError("RPA receipt file is unreadable") from exc
    if not isinstance(payload, dict):
        raise RpaArtifactReceiptError("RPA receipt file must contain an object")
    return payload


def _write_atomically(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, path)
    except (OSError, TypeError, ValueError):
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def publish_receipt(
    receipts_dir,
    *,
    material_id,
    client_attempt_id,
    state,
    csv_path=None,
    records_path=None,
):
    """Atomically publish one lifecycle state after its artifact is visible."""
    try:
        material_id = validate_material_id(material_id)
    except ValueError as exc:
        raise RpaArtifactReceiptError(str(exc)) from exc
    client_attempt_id = str(client_attempt_id or "").strip()
    if not client_attempt_id:
        raise RpaArtifactReceiptError("RPA receipt client attempt ID is required")
    if state not in RECEIPT_STATES:
        raise RpaArtifactReceiptError("RPA receipt state is invalid")
    if state == "records_persisted" and not records_path:
        raise RpaArtifactReceiptError("RPA records receipt requires records path")
    if state in {"artifact_downloaded", "records_persisted"} and not csv_path:
        raise RpaArtifactReceiptError("RPA artifact receipt requires CSV path")

    target = receipt_path(receipts_dir, material_id)
    previous = _load_existing(target)
    if previous is not None and previous.get("clientAttemptId") != client_attempt_id:
        previous = None
    now = _now()
    receipt = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "materialId": material_id,
        "clientAttemptId": client_attempt_id,
        "state": state,
        "submittedAt": (previous or {}).get("submittedAt") or now,
        "artifactDownloadedAt": (previous or {}).get("artifactDownloadedAt"),
        "recordsPersistedAt": (previous or {}).get("recordsPersistedAt"),
        "updatedAt": now,
        "csvPath": None,
        "recordsPath": None,
        "csvSizeBytes": None,
        "csvSha256": None,
    }
    if state in {"artifact_downloaded", "records_persisted"}:
        resolved_csv, size, digest = _csv_metadata(csv_path)
        receipt.update(
            {
                "csvPath": str(resolved_csv),
                "csvSizeBytes": size,
                "csvSha256": digest,
                "artifactDownloadedAt": receipt["artifactDownloadedAt"] or now,
            }
        )
    if state == "records_persisted":
        resolved_records, _metadata = _regular_file(records_path, label="records")
        receipt.update(
            {
                "recordsPath": str(resolved_records),
                "recordsPersistedAt": now,
            }
        )
    _write_atomically(target, receipt)
    return receipt


def _require_text(payload, field):
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RpaArtifactReceiptError(f"RPA receipt {field} is required")
    return value


def _resolve_artifact(raw_path, base_dir, label):
    path = Path(raw_path)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    return _regular_file(path, label=label)[0]


def load_records_persisted_receipt(receipts_dir, *, material_id, base_dir=None):
    """Validate and return a completed receipt and the retained artifacts."""
    try:
        material_id = validate_material_id(material_id)
    except ValueError as exc:
        raise RpaArtifactReceiptError(str(exc)) from exc
    payload = _load_existing(receipt_path(receipts_dir, material_id))
    if payload is None:
        raise RpaArtifactReceiptError("RPA receipt file is missing")
    if payload.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        raise RpaArtifactReceiptError("RPA receipt schema version is invalid")
    if payload.get("materialId") != material_id:
        raise RpaArtifactReceiptError("RPA receipt material ID does not match")
    _require_text(payload, "clientAttemptId")
    if payload.get("state") != "records_persisted":
        raise RpaArtifactReceiptError("RPA receipt is not records_persisted")
    csv_path = _resolve_artifact(_require_text(payload, "csvPath"), base_dir, "CSV")
    records_path = _resolve_artifact(
        _require_text(payload, "recordsPath"), base_dir, "records"
    )
    _csv_path, actual_size, actual_digest = _csv_metadata(csv_path)
    if payload.get("csvSizeBytes") != actual_size:
        raise RpaArtifactReceiptError("RPA receipt CSV size does not match")
    if payload.get("csvSha256") != actual_digest:
        raise RpaArtifactReceiptError("RPA receipt CSV digest does not match")
    try:
        records = json.loads(records_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaArtifactReceiptError("RPA receipt records are unreadable") from exc
    if not isinstance(records, list) or not any(
        isinstance(record, dict) and record.get("materialId") == material_id
        for record in records
    ):
        raise RpaArtifactReceiptError(
            "RPA receipt records do not contain the expected material"
        )
    return {
        "receipt": payload,
        "csvPath": csv_path,
        "recordsPath": records_path,
        "records": records,
    }
