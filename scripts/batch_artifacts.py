"""Persistence helpers for RPA batch artifacts.

This module deliberately contains no CLI or RPA policy.  It owns only the
filesystem contract for normalized records and JSON batch artifacts.
"""

import json
import os
from pathlib import Path
import uuid

from path_safety import artifact_path


def write_diagnosis_records(records, records_dir, material_id):
    target_dir = Path(records_dir)
    target = artifact_path(target_dir, material_id, ".json")
    target_dir.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError):
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def read_diagnosis_records(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json_atomically(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError):
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target
