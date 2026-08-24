"""Streaming RPA batch orchestration.

The runner owns candidate submission, bounded concurrency, selection/backfill,
postprocess handoff, and manifest checkpoints. External effects are supplied
as hooks so the CLI remains an adapter rather than the scheduler's dependency.
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import json
from pathlib import Path
import time


class BatchInterrupted(RuntimeError):
    """Raised after a requested stop has been checkpointed safely."""

    def __init__(self, manifest):
        super().__init__("wave batch interruption checkpointed")
        self.manifest = manifest


def wave_entry(material, rank, empty_timings):
    entry = dict(material)
    entry.update(
        {
            "rank": rank,
            "materialId": str(material.get("materialId") or "").strip(),
            "rpaStatus": "pending",
            "postprocessStatus": "pending",
            "status": "pending",
            "failureReason": None,
            "errorType": None,
            "errorMessage": None,
            "diagnosisRecordsPath": None,
            "diagnosisCsvPath": None,
            "recordsPersisted": False,
            "postprocessSucceeded": False,
            "postprocessAttemptCount": 0,
            "postprocessPhase": None,
            "postprocessAttempts": [],
            "compensationStatus": "not_needed",
            "backfillReason": None,
            "summaryJson": None,
            "summaryMd": None,
            "timingsSec": empty_timings(),
        }
    )
    return entry


def public_entry(entry):
    return {key: value for key, value in entry.items() if key != "records"}


def ordered(entries, public_entry_fn=public_entry):
    return [public_entry_fn(entries[index]) for index in sorted(entries)]


def accounted_indices(*entry_maps):
    accounted = set()
    for entry_map in entry_maps:
        accounted.update(entry_map)
    return accounted


def has_verified_rpa_csv(entry):
    return bool(
        entry.get("rpaResultStatus") == "available"
        and entry.get("rpaCsvVerified") is True
        and str(entry.get("diagnosisCsvPath") or "").strip()
    )


def has_terminal_rpa_csv(entry):
    return entry.get("rpaResultStatus") == "final_failed" or has_verified_rpa_csv(
        entry
    )


def has_accepted_record(entry, *, records_base_dir=None):
    if not (
        entry.get("rpaStatus") == "selected"
        and entry.get("rpaCsvVerified") is True
        and entry.get("recordsPersisted") is True
    ):
        return False
    material_id = str(entry.get("materialId") or "").strip()
    records = entry.get("records")
    if isinstance(records, list):
        return any(
            isinstance(record, dict) and record.get("materialId") == material_id
            for record in records
        )
    records_path = entry.get("diagnosisRecordsPath")
    if not isinstance(records_path, str) or not records_path.strip():
        return False
    path = Path(records_path)
    if not path.is_absolute() and records_base_dir is not None:
        path = Path(records_base_dir) / path
    try:
        retained_records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(retained_records, list) and any(
        isinstance(record, dict) and record.get("materialId") == material_id
        for record in retained_records
    )


def run_wave_batch(
    args,
    candidate_payload,
    *,
    wave_entry,
    resume_state,
    attempt_rpa,
    attempt_postprocess,
    merge_timings,
    new_manifest,
    write_json,
    manifest_path,
    ready_payload,
    ready_path,
    entry_result,
    elapsed,
    accounted_indices,
    ordered,
    clock=time.perf_counter,
    poll_interval_sec=0.1,
    is_interrupted=None,
):
    candidate_limit = args.candidate_top_n or args.target_top_n * 3
    candidates = candidate_payload["materials"][:candidate_limit]
    entries = {
        index: wave_entry(material, index + 1)
        for index, material in enumerate(candidates)
    }
    submitted, selected, rejected, cancelled, pending_postprocess = resume_state(
        args, candidates, entries
    )
    rpa_futures = {}
    postprocess_futures = {}
    next_index = 0
    batch_started_at = clock()
    records_base_dir = Path(getattr(args, "output_dir", ".")).parent
    interruption = None

    def persist():
        manifest = new_manifest(
            args, candidates, entries, submitted, selected, rejected, cancelled
        )
        # Diagnosis records are retained in per-material JSON files and may be
        # hundreds of KB per material.  Keep them in worker memory for
        # post-processing, but never duplicate them across manifest lists:
        # serializing the growing manifest otherwise blocks real-time progress
        # updates after an RPA result has already arrived.
        for collection_name in (
            "materials",
            "submittedMaterials",
            "selectedMaterials",
            "rejectedMaterials",
            "cancelledMaterials",
        ):
            collection = manifest.get(collection_name)
            if isinstance(collection, list):
                for item in collection:
                    if isinstance(item, dict):
                        item.pop("records", None)
        manifest["timingsSec"]["totalSec"] = elapsed(batch_started_at)
        accepted_count = len(accepted_entries())
        manifest["rpaCollection"] = {
            "status": (
                "complete" if accepted_count >= args.target_top_n
                else "backfilling" if any(
                    entry.get("backfillReason") == "rpa_collection_failure"
                    for entry in entries.values()
                )
                else "collecting"
            ),
            "targetCount": args.target_top_n,
            "acceptedCount": accepted_count,
        }
        if interruption is not None:
            manifest["interruption"] = interruption
        write_json(manifest_path, manifest)
        return manifest

    def checkpoint_interruption():
        nonlocal interruption
        if interruption is not None or not callable(is_interrupted) or not is_interrupted():
            return False
        interruption = {
            "reason": "signal",
            "interruptedAtSec": elapsed(batch_started_at),
            "pendingMaterialIds": [
                entries[index]["materialId"]
                for _future, index in sorted(
                    rpa_futures.items(), key=lambda item: item[1]
                )
            ],
        }
        persist()
        return True

    def submit_rpa(
        executor,
        index,
        wave_index=None,
        wave_size=None,
        wave_started_at=None,
        backfill_reason=None,
    ):
        entry = entries[index]
        state_changed_at = elapsed(batch_started_at)
        entry.update(
            {
                "rpaStatus": "submitted",
                "status": "running",
                "rpaStartedAtSec": state_changed_at,
                "lastStateChangedAtSec": state_changed_at,
                "lastProgressAtSec": state_changed_at,
            }
        )
        if backfill_reason is not None:
            entry["backfillReason"] = backfill_reason
        if wave_index is not None:
            entry.update(
                {
                    "waveIndex": wave_index,
                    "waveSize": wave_size,
                    "waveStartedAtSec": wave_started_at,
                }
            )
        submitted[index] = entry
        future = executor.submit(attempt_rpa, args, dict(entry))
        rpa_futures[future] = index
        return future

    def complete_rpa(future, index, postprocess_executor):
        scheduled_entry = entries[index]
        try:
            entry = future.result()
        except Exception as exc:
            entry = dict(scheduled_entry)
            entry.update(
                {
                    "rpaStatus": "rejected",
                    "status": "failed",
                    "failureReason": "rpa_request_failed",
                    "rpaResultStatus": "final_failed",
                    "rpaRecoveryAttempts": [],
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc),
                }
            )
        else:
            entry = dict(entry)
            for field in (
                "waveIndex",
                "waveSize",
                "waveStartedAtSec",
                "waveFinishedAtSec",
            ):
                if field in scheduled_entry:
                    entry[field] = scheduled_entry[field]
        state_changed_at = elapsed(batch_started_at)
        entry["rpaFinishedAtSec"] = state_changed_at
        entry["lastStateChangedAtSec"] = state_changed_at
        entry["lastProgressAtSec"] = state_changed_at
        entries[index] = entry
        submitted[index] = entry
        if not has_terminal_rpa_csv(entry) or (
            entry.get("rpaStatus") == "selected" and not has_verified_rpa_csv(entry)
        ):
            entry.update(
                {
                    "rpaStatus": "rejected",
                    "status": "failed",
                    "failureReason": "rpa_csv_artifact_unavailable",
                    "rpaResultStatus": "final_failed",
                    "errorType": "RpaCsvArtifactContractError",
                    "errorMessage": (
                        "RPA completed without a verified current CSV artifact"
                    ),
                }
            )
            entries[index] = entry
            submitted[index] = entry
        if entry["rpaStatus"] == "selected" and has_accepted_record(
            entry, records_base_dir=records_base_dir
        ):
            selected[index] = entry
            submit_postprocess(postprocess_executor, index)
        else:
            if entry["rpaStatus"] == "selected":
                entry.update(
                    {
                        "rpaStatus": "rejected",
                        "status": "failed",
                        "failureReason": "rpa_records_artifact_unavailable",
                        "rpaResultStatus": "final_failed",
                        "errorType": "RpaRecordsArtifactContractError",
                        "errorMessage": "RPA completed without retained diagnosis records",
                    }
                )
            rejected[index] = entry

    def submit_postprocess(executor, index, *, phase="parallel", retrying=False):
        entry = entries[index]
        state_changed_at = elapsed(batch_started_at)
        entry.setdefault("postprocessAttempts", [])
        entry.update(
            {
                "postprocessAttemptCount": entry.get("postprocessAttemptCount", 0) + 1,
                "postprocessPhase": phase,
                "postprocessStatus": "retrying" if retrying else "running",
                "status": "running",
                "postprocessStartedAtSec": state_changed_at,
                "lastStateChangedAtSec": state_changed_at,
                "lastProgressAtSec": state_changed_at,
            }
        )
        entry["postprocessAttempts"].append(
            {"phase": phase, "startedAtSec": state_changed_at, "status": "running"}
        )
        future = executor.submit(attempt_postprocess, args, dict(entry))
        postprocess_futures[future] = index

    def complete_postprocess(future, index, postprocess_executor):
        try:
            attempt = future.result()
        except Exception as exc:
            attempt = {"result": None, "error": exc, "timingsSec": {}}
        entry = entries[index]
        entry["timingsSec"] = merge_timings(
            entry["timingsSec"], attempt.get("timingsSec", {})
        )
        state_changed_at = elapsed(batch_started_at)
        attempt_entry = entry.setdefault("postprocessAttempts", [])[-1]
        attempt_entry.update(
            {
                "finishedAtSec": state_changed_at,
                "durationSec": max(
                    0.0, state_changed_at - attempt_entry.get("startedAtSec", state_changed_at)
                ),
                "substage": attempt.get("substage", "postprocess"),
            }
        )
        if attempt["error"] is not None:
            attempt_entry.update(
                {
                    "status": "failed",
                    "errorType": type(attempt["error"]).__name__,
                    "errorMessage": str(attempt["error"]),
                }
            )
            if (
                entry.get("postprocessPhase") == "parallel"
                and entry.get("postprocessAttemptCount", 0) < 2
            ):
                submit_postprocess(postprocess_executor, index, phase="parallel", retrying=True)
                return
            entry.update(
                {
                    "postprocessStatus": "failed",
                    "postprocessSucceeded": False,
                    "status": "failed",
                    "errorType": type(attempt["error"]).__name__,
                    "errorMessage": str(attempt["error"]),
                    "postprocessFinishedAtSec": state_changed_at,
                    "lastStateChangedAtSec": state_changed_at,
                    "lastProgressAtSec": state_changed_at,
                }
            )
            if entry.get("postprocessPhase") == "serial_compensation":
                entry["compensationStatus"] = "failed"
        else:
            _result_dir, summary_json, summary_md = attempt["result"]
            attempt_entry["status"] = "succeeded"
            entry.update(
                {
                    "postprocessStatus": "succeeded",
                    "postprocessSucceeded": True,
                    "status": "succeeded",
                    "summaryJson": str(summary_json),
                    "summaryMd": str(summary_md),
                    "postprocessFinishedAtSec": state_changed_at,
                    "lastStateChangedAtSec": state_changed_at,
                    "lastProgressAtSec": state_changed_at,
                }
            )
            if entry.get("postprocessPhase") == "serial_compensation":
                entry["compensationStatus"] = "succeeded"

    def next_unaccounted_index(index):
        accounted = accounted_indices(selected, rejected, cancelled)
        while index < len(candidates) and index in accounted:
            index += 1
        return index

    def successful_entries():
        return {
            index: entry
            for index, entry in selected.items()
            if entry.get("postprocessStatus") == "succeeded"
        }

    def accepted_entries():
        return {
            index: entry
            for index, entry in selected.items()
            if has_accepted_record(entry, records_base_dir=records_base_dir)
        }

    def primary_collection_slots(include_rpa=0):
        return max(
            0,
            args.target_top_n
            - len(accepted_entries())
            - include_rpa,
        )

    def collect_done(done, postprocess_executor):
        changed = False
        for future in sorted(done & set(rpa_futures), key=lambda item: rpa_futures[item]):
            index = rpa_futures.pop(future)
            complete_rpa(future, index, postprocess_executor)
            changed = True
        for future in sorted(done & set(postprocess_futures), key=lambda item: postprocess_futures[item]):
            index = postprocess_futures.pop(future)
            complete_postprocess(future, index, postprocess_executor)
            changed = True
        return changed

    def wave_postprocess_pending(wave):
        """Return whether this wave still has an active postprocess attempt."""
        if not wave:
            return False
        wave_indices = set(wave.values())
        return any(
            index in wave_indices
            for index in postprocess_futures.values()
        )

    with ThreadPoolExecutor(
        max_workers=args.rpa_concurrency
    ) as rpa_executor, ThreadPoolExecutor(
        max_workers=args.postprocess_concurrency
    ) as postprocess_executor:
        for index in pending_postprocess:
            submit_postprocess(postprocess_executor, index)
        next_index = next_unaccounted_index(next_index)
        if getattr(args, "rpa_wave_barrier", True):
            wave_index = 0
            pending_wave = set()
            wave = {}
            while True:
                checkpoint_interruption()
                if (
                    not pending_wave
                    and not wave
                    and not postprocess_futures
                    and interruption is None
                ):
                    capacity = min(args.rpa_concurrency, primary_collection_slots())
                    if capacity and next_index < len(candidates):
                        wave_started_at = elapsed(batch_started_at)
                        wave = {}
                        wave_indices = []
                        while next_index < len(candidates) and len(wave_indices) < capacity:
                            wave_indices.append(next_index)
                            next_index = next_unaccounted_index(next_index + 1)
                        wave_size = len(wave_indices)
                        for index in wave_indices:
                            future = submit_rpa(
                                rpa_executor,
                                index,
                                wave_index,
                                wave_size,
                                wave_started_at,
                                backfill_reason=(
                                    "rpa_collection_failure"
                                    if index >= args.target_top_n else None
                                ),
                            )
                            wave[future] = index
                        pending_wave = set(wave)
                        persist()
                    elif not postprocess_futures:
                        break
                elif not pending_wave and not postprocess_futures:
                    break
                wait_for = pending_wave | set(postprocess_futures)
                if not wait_for:
                    continue
                done, _pending = wait(
                    wait_for, timeout=poll_interval_sec, return_when=FIRST_COMPLETED
                )
                changed = collect_done(done, postprocess_executor)
                checkpoint_interruption()
                if pending_wave:
                    pending_wave -= done
                if not pending_wave and wave and not wave_postprocess_pending(wave):
                    wave_finished_at = elapsed(batch_started_at)
                    for index in wave.values():
                        entries[index]["waveFinishedAtSec"] = wave_finished_at
                        entries[index]["waveCsvArtifactFinishedAtSec"] = (
                            wave_finished_at
                        )
                    wave = {}
                    wave_index += 1
                    changed = True
                if changed:
                    persist()
        else:
            while True:
                while (
                    next_index < len(candidates)
                    and len(rpa_futures) < args.rpa_concurrency
                    and primary_collection_slots(len(rpa_futures))
                ):
                    submit_rpa(
                        rpa_executor,
                        next_index,
                        backfill_reason=(
                            "rpa_collection_failure"
                            if next_index >= args.target_top_n else None
                        ),
                    )
                    next_index = next_unaccounted_index(next_index + 1)
                wait_for = set(rpa_futures) | set(postprocess_futures)
                if not wait_for:
                    break
                done, _pending = wait(
                    wait_for, timeout=poll_interval_sec, return_when=FIRST_COMPLETED
                )
                changed = collect_done(done, postprocess_executor)
                if changed:
                    persist()

    if interruption is not None:
        raise BatchInterrupted(interruption)

    def compensate_terminal_failures():
        compensation_queue = sorted(
            (
                index
                for index, entry in selected.items()
                if (
                    has_accepted_record(entry, records_base_dir=records_base_dir)
                    and entry.get("postprocessStatus") == "failed"
                    and entry.get("compensationStatus") not in {"succeeded", "failed"}
                )
            ),
            key=lambda index: entries[index].get("rank", index),
        )
        if not compensation_queue:
            return
        with ThreadPoolExecutor(max_workers=1) as serial_executor:
            for index in compensation_queue:
                entries[index]["compensationStatus"] = "queued"
                persist()
                submit_postprocess(
                    serial_executor, index, phase="serial_compensation"
                )
                future = next(
                    future
                    for future, future_index in postprocess_futures.items()
                    if future_index == index
                )
                postprocess_futures.pop(future)
                complete_postprocess(future, index, serial_executor)
                persist()

    def terminal_backfill_indices(deficit):
        nonlocal next_index
        indices = []
        while next_index < len(candidates) and len(indices) < deficit:
            next_index = next_unaccounted_index(next_index)
            if next_index >= len(candidates):
                break
            indices.append(next_index)
            next_index += 1
        return indices

    def run_terminal_backfill_wave(indices):
        with ThreadPoolExecutor(
            max_workers=min(args.rpa_concurrency, len(indices))
        ) as rpa_executor, ThreadPoolExecutor(
            max_workers=args.postprocess_concurrency
        ) as postprocess_executor:
            for index in indices:
                submit_rpa(
                    rpa_executor,
                    index,
                    backfill_reason="postprocess_failed_after_retries",
                )
            persist()
            while rpa_futures or postprocess_futures:
                done, _pending = wait(
                    set(rpa_futures) | set(postprocess_futures),
                    timeout=poll_interval_sec,
                    return_when=FIRST_COMPLETED,
                )
                changed = collect_done(done, postprocess_executor)
                if changed:
                    persist()

    compensate_terminal_failures()
    while True:
        final_successes = successful_entries()
        deficit = args.target_top_n - len(final_successes)
        if deficit <= 0:
            break
        indices = terminal_backfill_indices(deficit)
        if not indices:
            break
        run_terminal_backfill_wave(indices)
        compensate_terminal_failures()

    final_successes = successful_entries()
    if len(final_successes) >= args.target_top_n:
        for index in range(next_index, len(candidates)):
            if index in accounted_indices(selected, rejected, cancelled):
                continue
            entry = entries[index]
            entry.update(
                {
                    "rpaStatus": "cancelled",
                    "postprocessStatus": "not_started",
                    "status": "cancelled",
                    "failureReason": "target_satisfied",
                }
            )
            cancelled[index] = entry
    manifest = persist()
    failures = len(final_successes) < args.target_top_n
    if not failures:
        ready = ready_payload(
            candidate_payload, ordered(final_successes)[: args.target_top_n]
        )
        write_json(ready_path, ready)
    else:
        try:
            Path(ready_path).unlink(missing_ok=True)
        except OSError:
            pass
    last_result = None
    for index in sorted(final_successes, reverse=True):
        last_result = entry_result(final_successes[index])
        break
    return manifest, failures, last_result
