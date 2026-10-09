"""Durable host-owned analysis segments; no model calls or filesystem writes.

The caller holds the analysis-session lock and publishes the updated state.
Host IDs are supplied by the host, never invented here. A soft time budget is
an experimental checkpoint policy, not a claim about a platform deadline.
"""
import math
import re
import time


DEFAULT_MATERIAL_LIMIT = 3
DEFAULT_SOFT_TIME_BUDGET_SECONDS = 480
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_STATUSES = {"active", "yielded", "interrupted", "complete"}


def _identifier(value, field):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a safe nonempty identifier of at most 128 characters")
    return value


def _positive_number(value, field):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{field} must be a positive finite number")
    return value


def _material_limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("material_limit must be a positive integer")
    return value


def _timestamp(now):
    value = time.time() if now is None else now
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError("segment time must be a nonnegative finite number")
    return value


def _cursor(state, task_ids):
    completed = state.get("completed")
    if (not isinstance(completed, list) or len(completed) > len(task_ids)
            or any(not isinstance(entry, dict) for entry in completed)
            or [entry.get("taskId") for entry in completed] != list(task_ids[:len(completed)])):
        raise ValueError("analysis segment task coverage mismatch")
    return len(completed)


def _kind(task_id):
    return "materials" if task_id.startswith("material-") else "chapter"


def _history(state, task_ids):
    cursor = _cursor(state, task_ids)
    history = state.get("segmentHistory", [])
    if not isinstance(history, list):
        raise ValueError("segmentHistory must be a list")
    if not history:
        if state.get("activeSegmentId") is not None:
            raise ValueError("activeSegmentId has no segment history")
        return history, None, cursor
    ids = set()
    host_ids = set()
    previous_start = 0
    for index, segment in enumerate(history):
        if not isinstance(segment, dict):
            raise ValueError("invalid analysis segment record")
        segment_id = _identifier(segment.get("segmentId"), "segment_id")
        host_id = _identifier(segment.get("hostRunId"), "host_run_id")
        if segment_id in ids:
            raise ValueError("analysis segment IDs must be unique")
        ids.add(segment_id)
        if host_id in host_ids:
            raise ValueError("analysis host run IDs must be unique across segment history")
        host_ids.add(host_id)
        start = segment.get("startCursor")
        if (isinstance(start, bool) or not isinstance(start, int)
                or not previous_start <= start <= cursor or start >= len(task_ids)):
            raise ValueError("analysis segment cursor mismatch")
        previous_start = start
        if segment.get("startedAtSec") is None:
            raise ValueError("analysis segment is missing its start time")
        _timestamp(segment.get("startedAtSec"))
        _material_limit(segment.get("materialLimit"))
        _positive_number(segment.get("softTimeBudgetSeconds"), "soft_time_budget_seconds")
        if segment.get("kind") not in {"chapter", "materials"}:
            raise ValueError("invalid analysis segment kind")
        if segment.get("status") not in _STATUSES:
            raise ValueError("invalid analysis segment status")
        if index < len(history) - 1 and segment["status"] not in {"yielded", "interrupted", "complete"}:
            raise ValueError("analysis segment history contains an active predecessor")
        if segment["kind"] != _kind(task_ids[start]):
            raise ValueError("analysis segment kind does not match its starting task")
        if index:
            predecessor = history[index - 1]
            if (segment.get("previousSegmentId") != predecessor["segmentId"]
                    or segment["hostRunId"] == predecessor["hostRunId"]):
                raise ValueError("analysis segment predecessor identity mismatch")
    active = history[-1]
    if state.get("activeSegmentId") != active["segmentId"]:
        raise ValueError("activeSegmentId must reference the latest segment")
    return history, active, cursor


def _check_policy(segment, material_limit, soft_time_budget_seconds):
    if material_limit is not None and _material_limit(material_limit) != segment["materialLimit"]:
        raise ValueError("cannot change an existing segment material limit")
    if (soft_time_budget_seconds is not None
            and _positive_number(soft_time_budget_seconds, "soft_time_budget_seconds")
            != segment["softTimeBudgetSeconds"]):
        raise ValueError("cannot change an existing segment soft time budget")


def prepare_segment(state, task_ids, *, begin_segment=False, segment_id=None,
                    host_run_id=None, after_segment=None, previous_host_ended=False,
                    material_limit=None, soft_time_budget_seconds=None, now=None):
    """Validate the segment owner, or begin one explicit, serialized successor.

    Unsegmented sessions stay legacy until begin_segment is requested. No host
    liveness is inferred: previous_host_ended records the host's confirmation.
    The returned object belongs to state; the caller publishes state atomically.
    """
    if not isinstance(begin_segment, bool) or not isinstance(previous_host_ended, bool):
        raise ValueError("segment begin and host-ended confirmation must be booleans")
    history, active, cursor = _history(state, task_ids)
    if not begin_segment:
        if after_segment is not None or previous_host_ended:
            raise ValueError("segment predecessor options require begin_segment")
        if active is None:
            if any(value is not None for value in (segment_id, host_run_id,
                                                   material_limit, soft_time_budget_seconds)):
                raise ValueError("begin_segment is required to enable analysis segments")
            return None
        if segment_id != active["segmentId"] or host_run_id != active["hostRunId"]:
            raise ValueError("analysis advance requires the active segment_id and host_run_id")
        _check_policy(active, material_limit, soft_time_budget_seconds)
        return active

    segment_id = _identifier(segment_id, "segment_id")
    host_run_id = _identifier(host_run_id, "host_run_id")
    if active is not None and segment_id == active["segmentId"]:
        if host_run_id != active["hostRunId"]:
            raise ValueError("an existing segment belongs to a different host run")
        _check_policy(active, material_limit, soft_time_budget_seconds)
        if after_segment is not None and after_segment != active.get("previousSegmentId"):
            raise ValueError("segment predecessor mismatch")
        return active
    if any(segment["segmentId"] == segment_id for segment in history):
        raise ValueError("a historical analysis segment cannot be reactivated")
    if any(segment["hostRunId"] == host_run_id for segment in history):
        raise ValueError("a historical host run cannot be reused for a new analysis segment")
    if cursor == len(task_ids):
        raise ValueError("cannot begin a segment after all analysis tasks are accepted")
    if active is None:
        if after_segment is not None or previous_host_ended:
            raise ValueError("the first segment has no predecessor")
    else:
        if after_segment != active["segmentId"]:
            raise ValueError("after_segment must match the latest analysis segment")
        if host_run_id == active["hostRunId"]:
            raise ValueError("a successor segment requires a different host run")
        if active["status"] != "yielded" and not previous_host_ended:
            raise ValueError("confirm that the previous host ended before replacing an unyielded segment")

    timestamp = _timestamp(now)
    limit = _material_limit(DEFAULT_MATERIAL_LIMIT if material_limit is None else material_limit)
    budget = _positive_number(DEFAULT_SOFT_TIME_BUDGET_SECONDS if soft_time_budget_seconds is None
                              else soft_time_budget_seconds, "soft_time_budget_seconds")
    segment = {"segmentId": segment_id, "hostRunId": host_run_id,
               "startCursor": cursor, "startedAtSec": timestamp,
               "kind": _kind(task_ids[cursor]), "materialLimit": limit,
               "softTimeBudgetSeconds": budget, "status": "active"}
    if active is not None:
        segment["previousSegmentId"] = active["segmentId"]
        if active["status"] != "yielded":
            active["status"] = "interrupted"
        active.setdefault("endedAtSec", timestamp)
        active.setdefault("endCursor", cursor)
    state.setdefault("segmentHistory", []).append(segment)
    state["activeSegmentId"] = segment_id
    return segment


def segment_projection(state, task_ids, *, now=None):
    """Return a read-only segment budget and checkpoint decision.

    An issued task may finish or stash progress after the soft budget expires.
    The caller tracks issuedTaskId and sets status='yielded' only at a durable
    checkpoint; this projection itself never accepts work or edits state.
    """
    _, active, cursor = _history(state, task_ids)
    if active is None:
        return None
    timestamp = _timestamp(now)
    accepted = list(task_ids[active["startCursor"]:cursor])
    next_task = task_ids[cursor] if cursor < len(task_ids) else None
    materials = sum(task_id.startswith("material-") for task_id in accepted)
    elapsed = max(0, timestamp - active["startedAtSec"])
    expired = elapsed >= active["softTimeBudgetSeconds"]
    reason = None
    if next_task is not None:
        if active["kind"] == "chapter" and accepted:
            reason = "chapter_complete"
        elif accepted and _kind(next_task) != active["kind"]:
            reason = "kind_changed"
        elif materials >= active["materialLimit"]:
            reason = "material_limit"
        elif accepted and expired and active.get("issuedTaskId") != next_task:
            reason = "soft_time_budget"
    if active["status"] == "yielded":
        reason = active.get("yieldReason") or reason
    return {"segmentId": active["segmentId"], "hostRunId": active["hostRunId"],
            "status": active["status"], "kind": active["kind"],
            "startCursor": active["startCursor"], "startedAtSec": active["startedAtSec"],
            "materialLimit": active["materialLimit"],
            "softTimeBudgetSeconds": active["softTimeBudgetSeconds"],
            "acceptedTaskCount": len(accepted), "acceptedMaterialsInSegment": materials,
            "lastAcceptedTaskId": task_ids[cursor - 1] if cursor else None,
            "lastAcceptedTaskIdInSegment": accepted[-1] if accepted else None,
            "nextTaskId": next_task, "allTasksComplete": next_task is None,
            "elapsedSeconds": round(elapsed, 3), "softTimeExpired": expired,
            "yieldReason": reason}
