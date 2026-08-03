"""Streaming RPA batch orchestration.

The runner owns candidate submission, bounded concurrency, selection/backfill,
postprocess handoff, and manifest checkpoints. External effects are supplied
as hooks so the CLI remains an adapter rather than the scheduler's dependency.
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import time


def stream_entry(material, rank, empty_timings):
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
            "rpaSoftTimeoutExceeded": False,
            "rpaHardTimeoutExceeded": False,
            "postprocessAttemptCount": 0,
            "postprocessPhase": None,
            "postprocessAttempts": [],
            "compensationStatus": "not_needed",
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


def run_streaming_batch(
    args,
    candidate_payload,
    *,
    stream_entry,
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
):
    candidate_limit = args.candidate_top_n or args.target_top_n * 3
    candidates = candidate_payload["materials"][:candidate_limit]
    entries = {
        index: stream_entry(material, index + 1)
        for index, material in enumerate(candidates)
    }
    submitted, selected, rejected, cancelled, pending_postprocess = resume_state(
        args, candidates, entries
    )
    rpa_futures = {}
    postprocess_futures = {}
    next_index = 0
    batch_started_at = clock()
    rpa_started_at = {}

    def persist():
        manifest = new_manifest(
            args, candidates, entries, submitted, selected, rejected, cancelled
        )
        manifest["timingsSec"]["totalSec"] = elapsed(batch_started_at)
        write_json(manifest_path, manifest)
        return manifest

    def submit_rpa(executor, index, wave_index=None, wave_size=None, wave_started_at=None):
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
        if wave_index is not None:
            entry.update(
                {
                    "waveIndex": wave_index,
                    "waveSize": wave_size,
                    "waveStartedAtSec": wave_started_at,
                    "waveFinishedAtSec": None,
                }
            )
        submitted[index] = entry
        future = executor.submit(attempt_rpa, args, dict(entry))
        rpa_futures[future] = index
        rpa_started_at[future] = clock()
        return future

    def complete_rpa(future, index, postprocess_executor):
        try:
            entry = future.result()
        except Exception as exc:
            entry = dict(entries[index])
            entry.update(
                {
                    "rpaStatus": "rejected",
                    "status": "failed",
                    "failureReason": "rpa_request_failed",
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc),
                }
            )
        state_changed_at = elapsed(batch_started_at)
        entry["rpaFinishedAtSec"] = state_changed_at
        entry["lastStateChangedAtSec"] = state_changed_at
        entry["lastProgressAtSec"] = state_changed_at
        entries[index] = entry
        submitted[index] = entry
        if entry["rpaStatus"] == "selected":
            selected[index] = entry
            submit_postprocess(postprocess_executor, index)
        else:
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

    def remaining_success_slots(include_rpa=0):
        return max(
            0,
            args.target_top_n
            - len(successful_entries())
            - len(postprocess_futures)
            - include_rpa,
        )

    def mark_soft_timeouts(futures):
        now = clock()
        changed = False
        threshold = getattr(args, "rpa_preflight_soft_timeout_sec", None)
        if threshold is None:
            return changed
        for future in futures:
            index = rpa_futures[future]
            entry = entries[index]
            if (
                not entry.get("rpaSoftTimeoutExceeded")
                and now - rpa_started_at[future] >= threshold
            ):
                state_changed_at = elapsed(batch_started_at)
                entry.update(
                    {
                        "rpaSoftTimeoutExceeded": True,
                        "rpaSoftTimeoutAtSec": state_changed_at,
                        "lastStateChangedAtSec": state_changed_at,
                        "lastProgressAtSec": state_changed_at,
                    }
                )
                changed = True
        return changed

    def collect_done(done, postprocess_executor):
        changed = False
        for future in sorted(done & set(rpa_futures), key=lambda item: rpa_futures[item]):
            index = rpa_futures.pop(future)
            rpa_started_at.pop(future, None)
            complete_rpa(future, index, postprocess_executor)
            changed = True
        for future in sorted(done & set(postprocess_futures), key=lambda item: postprocess_futures[item]):
            index = postprocess_futures.pop(future)
            complete_postprocess(future, index, postprocess_executor)
            changed = True
        return changed

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
                if len(successful_entries()) >= args.target_top_n:
                    break
                if not pending_wave:
                    capacity = min(args.rpa_concurrency, remaining_success_slots())
                    if capacity and next_index < len(candidates):
                        wave_started_at = elapsed(batch_started_at)
                        wave = {}
                        while next_index < len(candidates) and len(wave) < capacity:
                            future = submit_rpa(
                                rpa_executor,
                                next_index,
                                wave_index,
                                None,
                                wave_started_at,
                            )
                            wave[future] = next_index
                            next_index = next_unaccounted_index(next_index + 1)
                        wave_size = len(wave)
                        for future in wave:
                            entries[wave[future]]["waveSize"] = wave_size
                        pending_wave = set(wave)
                        persist()
                    elif not postprocess_futures:
                        break
                wait_for = pending_wave | set(postprocess_futures)
                done, _pending = wait(
                    wait_for, timeout=poll_interval_sec, return_when=FIRST_COMPLETED
                )
                changed = collect_done(done, postprocess_executor)
                if pending_wave:
                    pending_wave -= done
                    changed = mark_soft_timeouts(pending_wave) or changed
                if not pending_wave and wave:
                    wave_finished_at = elapsed(batch_started_at)
                    for index in wave.values():
                        entries[index]["waveFinishedAtSec"] = wave_finished_at
                    wave = {}
                    wave_index += 1
                    changed = True
                if changed:
                    persist()
        else:
            while True:
                if len(successful_entries()) >= args.target_top_n:
                    break
                while (
                    next_index < len(candidates)
                    and len(rpa_futures) < args.rpa_concurrency
                    and remaining_success_slots(len(rpa_futures))
                ):
                    submit_rpa(rpa_executor, next_index)
                    next_index = next_unaccounted_index(next_index + 1)
                wait_for = set(rpa_futures) | set(postprocess_futures)
                if not wait_for:
                    break
                done, _pending = wait(
                    wait_for, timeout=poll_interval_sec, return_when=FIRST_COMPLETED
                )
                changed = collect_done(done, postprocess_executor)
                changed = mark_soft_timeouts(set(rpa_futures)) or changed
                if changed:
                    persist()

    compensation_queue = sorted(
        (
            index
            for index, entry in selected.items()
            if entry.get("recordsPersisted")
            and entry.get("postprocessStatus") == "failed"
        ),
        key=lambda index: entries[index].get("rank", index),
    )
    if compensation_queue:
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
    ready = ready_payload(
        candidate_payload, ordered(final_successes)[: args.target_top_n]
    )
    write_json(ready_path, ready)
    failures = len(final_successes) < args.target_top_n
    last_result = None
    for index in sorted(final_successes, reverse=True):
        last_result = entry_result(final_successes[index])
        break
    return manifest, failures, last_result


def run_legacy_batch(
    args,
    material_ids,
    *,
    manifest_path,
    new_manifest,
    load_resume_manifest,
    iter_pending,
    start_attempt,
    attempt_material,
    apply_attempt,
    write_json,
    entry_result,
    elapsed,
):
    started_at = time.perf_counter()
    expected = new_manifest(args, material_ids)
    manifest = load_resume_manifest(manifest_path, expected) if args.resume else expected
    manifest["concurrency"] = args.concurrency
    manifest.setdefault("timingsSec", {})["totalSec"] = 0.0
    write_json(manifest_path, manifest)

    pending = list(iter_pending(args, manifest, material_ids))
    for material_id in pending:
        start_attempt(manifest["materials"][material_id])
    write_json(manifest_path, manifest)

    if args.concurrency == 1:
        for material_id in pending:
            apply_attempt(manifest, material_id, attempt_material(args, material_id))
            write_json(manifest_path, manifest)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(attempt_material, args, material_id): material_id
                for material_id in pending
            }
            for future in as_completed(futures):
                material_id = futures[future]
                apply_attempt(manifest, material_id, future.result())
                write_json(manifest_path, manifest)

    manifest["timingsSec"]["totalSec"] = elapsed(started_at)
    write_json(manifest_path, manifest)
    failures = [
        material_id
        for material_id in material_ids
        if manifest["materials"][material_id].get("status") == "failed"
    ]
    last_result = None
    for material_id in reversed(material_ids):
        entry = manifest["materials"][material_id]
        if entry.get("status") == "succeeded":
            last_result = entry_result(entry)
            break
    return manifest_path, last_result, failures
