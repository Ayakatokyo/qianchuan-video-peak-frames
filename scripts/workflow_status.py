"""Lightweight, read-only status inspection for a standard run."""

import json
from pathlib import Path

from batch_runtime import read_json, run_lock_state


def standard_run_status(output_dir):
    root = Path(output_dir)
    summary_path = root / "run-summary.json"
    peak_frames = root / "peak-frames"
    summary = read_json(summary_path)
    if not isinstance(summary, dict):
        summary = {}
    manifest = read_json(peak_frames / "batch-manifest.json")
    stage_counts = manifest.get("stageCounts", {}) if isinstance(manifest, dict) else {}
    if not isinstance(stage_counts, dict):
        stage_counts = {}

    lock = None
    workflow_lock_state = "missing"
    resolved_summary = str(summary_path.resolve())
    lock_root = root.parent / ".qianchuan-workflow-locks"
    if lock_root.is_dir():
        for lock_path in lock_root.glob("*.lock"):
            payload = read_json(lock_path)
            if not isinstance(payload, dict):
                continue
            manifest_path = payload.get("manifestPath")
            if manifest_path and str(Path(manifest_path).resolve()) == resolved_summary:
                lock = payload
                workflow_lock_state, _payload = run_lock_state(lock_path)
                break

    batch_lock_state, batch_lock = run_lock_state(peak_frames / ".batch-run.lock")
    observed_lock_state = (
        "active"
        if "active" in {workflow_lock_state, batch_lock_state}
        else workflow_lock_state
        if workflow_lock_state != "missing"
        else batch_lock_state
    )
    persisted_status = summary.get("status", "not_started")
    workflow_status = (
        "interrupted"
        if persisted_status == "running" and observed_lock_state != "active"
        else persisted_status
    )
    progress_path = peak_frames / "batch-progress.jsonl"
    last_progress = None
    if progress_path.is_file():
        try:
            lines = [
                line
                for line in progress_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if lines:
                last_progress = json.loads(lines[-1])
        except (OSError, UnicodeError, json.JSONDecodeError):
            last_progress = None

    return {
        "workflowStatus": workflow_status,
        "persistedWorkflowStatus": persisted_status,
        "activeStage": summary.get("activeStage"),
        "lock": lock or batch_lock,
        "lockState": observed_lock_state,
        "workflowLockState": workflow_lock_state,
        "batchLockState": batch_lock_state,
        "recoveryRequired": workflow_status == "interrupted",
        "progressLog": str(progress_path) if progress_path.is_file() else None,
        "lastProgress": last_progress,
        "rpaRequestCount": stage_counts.get("rpaRequestCount", 0),
        "rpaAcceptedCount": stage_counts.get("rpaAcceptedCount", 0),
        "rpaSelectedCount": stage_counts.get("rpaSelectedCount", 0),
        "recordsPersistedCount": stage_counts.get("recordsPersistedCount", 0),
        "postprocessSucceededCount": stage_counts.get("postprocessSucceededCount", 0),
        "reportReadyCount": stage_counts.get("reportReadyCount", 0),
    }
