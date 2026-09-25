"""Lightweight, read-only status inspection for a standard run."""

import json
import time
from pathlib import Path

from batch_runtime import read_json, run_lock_state


def _last_progress(path):
    """Bounded tail read; a killed append must not hide the prior checkpoint."""
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 65536)
            stream.seek(start)
            lines = stream.read().splitlines()
        if start and lines:
            lines = lines[1:]
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except (UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
    except OSError:
        pass
    return None


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

    workflow_lock_state, lock = run_lock_state(root / ".workflow-run.lock")
    resolved_summary = str(summary_path.resolve())
    lock_root = root.parent / ".qianchuan-workflow-locks"
    if workflow_lock_state != "active" and lock_root.is_dir():
        for lock_path in lock_root.glob("*.lock"):
            payload = read_json(lock_path)
            if not isinstance(payload, dict):
                continue
            manifest_path = payload.get("manifestPath")
            if manifest_path and str(Path(manifest_path).resolve()) == resolved_summary:
                state, _payload = run_lock_state(lock_path)
                if state == "active" or workflow_lock_state == "missing":
                    lock = payload
                    workflow_lock_state = state
                if state == "active":
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
    last_progress = _last_progress(progress_path)

    request = summary.get("request") or {}
    target = request.get("topN") if isinstance(request, dict) else None
    if target is None and isinstance(manifest, dict):
        target = manifest.get("targetTopN")
    if type(target) is not int or target <= 0:
        target = None
    accepted = stage_counts.get("rpaAcceptedCount")
    remaining = max(0, target - accepted) if target and type(accepted) is int else None
    active_stage = summary.get("activeStage")
    next_action = (
        "wait" if observed_lock_state == "active" else
        "finalize" if workflow_status == "interrupted" and active_stage == "report" else
        "resume" if workflow_status == "interrupted" else
        "analyze" if workflow_status == "analysis_pending" else
        "verify_delivery" if workflow_status == "complete" else "inspect"
    )
    recovery_stage = None
    if next_action in {"wait", "resume", "finalize"}:
        processed = stage_counts.get("postprocessSucceededCount", 0)
        if active_stage in {"selection", "metadata", "verification", "facts", "report"}:
            recovery_stage = active_stage
        elif remaining is None:
            recovery_stage = "inspect_artifacts"
        elif remaining:
            recovery_stage = "rpa_collection"
        elif type(processed) is int and processed >= target:
            recovery_stage = "verification"
        else:
            recovery_stage = "local_postprocess"

    return {
        "workflowStatus": workflow_status,
        "persistedWorkflowStatus": persisted_status,
        "activeStage": summary.get("activeStage"),
        "lock": lock or batch_lock,
        "lockState": observed_lock_state,
        "workflowLockState": workflow_lock_state,
        "batchLockState": batch_lock_state,
        "recoveryRequired": workflow_status == "interrupted",
        "nextAction": next_action,
        "recoveryStage": recovery_stage,
        "targetTopN": target,
        "rpaRemainingCount": remaining,
        "progressLog": str(progress_path) if progress_path.is_file() else None,
        "lastProgress": last_progress,
        "lastProgressAgeSec": (
            round(max(0, time.time() - last_progress["observedAtEpoch"]), 1)
            if isinstance(last_progress, dict) and type(last_progress.get("observedAtEpoch")) in {int, float}
            else None
        ),
        "runtime": summary.get("runtime"),
        "workerLimits": {key: manifest.get(key) for key in
                         ("rpaConcurrency", "downloadConcurrency", "postprocessConcurrency")}
                        if isinstance(manifest, dict) else {},
        "rpaRequestCount": stage_counts.get("rpaRequestCount", 0),
        "rpaAcceptedCount": stage_counts.get("rpaAcceptedCount", 0),
        "rpaSelectedCount": stage_counts.get("rpaSelectedCount", 0),
        "recordsPersistedCount": stage_counts.get("recordsPersistedCount", 0),
        "postprocessSucceededCount": stage_counts.get("postprocessSucceededCount", 0),
        "reportReadyCount": stage_counts.get("reportReadyCount", 0),
    }


def wait_standard_run_status(output_dir, *, wait_seconds=0):
    """Bounded observation; return promptly when work stops or needs an action."""
    if not 0 <= wait_seconds <= 60:
        raise ValueError("wait-seconds must be between 0 and 60")
    deadline = time.monotonic() + wait_seconds
    while True:
        result = standard_run_status(output_dir)
        remaining = deadline - time.monotonic()
        if result["nextAction"] != "wait" or remaining <= 0:
            return result
        time.sleep(min(5, remaining))
