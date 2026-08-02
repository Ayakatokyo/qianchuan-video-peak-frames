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
    batch_started_at = time.perf_counter()

    def persist():
        manifest = new_manifest(
            args, candidates, entries, submitted, selected, rejected, cancelled
        )
        manifest["timingsSec"]["totalSec"] = elapsed(batch_started_at)
        write_json(manifest_path, manifest)
        return manifest

    def submit_rpa(executor, index, wave_index=None, wave_size=None, wave_started_at=None):
        entry = entries[index]
        entry.update({"rpaStatus": "submitted", "status": "running"})
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
        future = executor.submit(attempt_rpa, args, entry)
        rpa_futures[future] = index
        return future

    def complete_rpa(future, index, postprocess_executor):
        entry = future.result()
        entries[index] = entry
        submitted[index] = entry
        if entry["rpaStatus"] == "selected" and len(selected) < args.target_top_n:
            selected[index] = entry
            postprocess_futures[
                postprocess_executor.submit(attempt_postprocess, args, entry)
            ] = index
        elif entry["rpaStatus"] == "selected":
            entry.update(
                {
                    "rpaStatus": "rejected",
                    "postprocessStatus": "not_started",
                    "status": "failed",
                    "failureReason": "top_n_already_selected",
                }
            )
            rejected[index] = entry
        else:
            rejected[index] = entry

    def complete_postprocess(future, index):
        attempt = future.result()
        entry = entries[index]
        entry["timingsSec"] = merge_timings(
            entry["timingsSec"], attempt["timingsSec"]
        )
        if attempt["error"] is not None:
            entry.update(
                {
                    "postprocessStatus": "failed",
                    "postprocessSucceeded": False,
                    "status": "failed",
                    "errorType": type(attempt["error"]).__name__,
                    "errorMessage": str(attempt["error"]),
                }
            )
        else:
            _result_dir, summary_json, summary_md = attempt["result"]
            entry.update(
                {
                    "postprocessStatus": "succeeded",
                    "postprocessSucceeded": True,
                    "status": "succeeded",
                    "summaryJson": str(summary_json),
                    "summaryMd": str(summary_md),
                }
            )

    def next_unaccounted_index(index):
        accounted = accounted_indices(selected, rejected, cancelled)
        while index < len(candidates) and index in accounted:
            index += 1
        return index

    with ThreadPoolExecutor(
        max_workers=args.rpa_concurrency
    ) as rpa_executor, ThreadPoolExecutor(
        max_workers=args.postprocess_concurrency
    ) as postprocess_executor:
        for index in pending_postprocess:
            postprocess_futures[
                postprocess_executor.submit(attempt_postprocess, args, entries[index])
            ] = index
        next_index = next_unaccounted_index(next_index)
        if getattr(args, "rpa_wave_barrier", True):
            wave_index = 0
            while next_index < len(candidates) and len(selected) < args.target_top_n:
                wave_started_at = elapsed(batch_started_at)
                wave = {}
                remaining = args.target_top_n - len(selected)
                while (
                    next_index < len(candidates)
                    and len(wave) < args.rpa_concurrency
                    and len(wave) < remaining
                ):
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
                persist()
                pending_wave = set(wave)
                while pending_wave:
                    done_wave, pending_wave = wait(
                        pending_wave, return_when=FIRST_COMPLETED
                    )
                    for future in sorted(done_wave, key=lambda item: wave[item]):
                        index = wave[future]
                        rpa_futures.pop(future)
                        complete_rpa(future, index, postprocess_executor)
                wave_finished_at = elapsed(batch_started_at)
                for index in wave.values():
                    entries[index]["waveFinishedAtSec"] = wave_finished_at
                done_postprocess, _pending = wait(
                    set(postprocess_futures), timeout=0
                ) if postprocess_futures else (set(), set())
                for future in done_postprocess:
                    index = postprocess_futures.pop(future)
                    complete_postprocess(future, index)
                persist()
                wave_index += 1
        else:
            while (
                next_index < len(candidates)
                and len(selected) + len(rpa_futures) < args.target_top_n
                and len(rpa_futures) < args.rpa_concurrency
            ):
                submit_rpa(rpa_executor, next_index)
                next_index = next_unaccounted_index(next_index + 1)
            persist()
            while rpa_futures:
                done, _pending = wait(rpa_futures, return_when=FIRST_COMPLETED)
                for future in done:
                    index = rpa_futures.pop(future)
                    complete_rpa(future, index, postprocess_executor)
                while (
                    next_index < len(candidates)
                    and len(selected) + len(rpa_futures) < args.target_top_n
                    and len(rpa_futures) < args.rpa_concurrency
                ):
                    submit_rpa(rpa_executor, next_index)
                    next_index = next_unaccounted_index(next_index + 1)
                persist()

        for future in as_completed(postprocess_futures):
            index = postprocess_futures[future]
            complete_postprocess(future, index)
            persist()

    if len(selected) >= args.target_top_n:
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
    ready = ready_payload(candidate_payload, ordered(selected))
    write_json(ready_path, ready)
    failures = len(selected) < args.target_top_n or any(
        entry["postprocessStatus"] != "succeeded" for entry in selected.values()
    )
    last_result = None
    for index in sorted(selected, reverse=True):
        entry = selected[index]
        if entry["postprocessStatus"] == "succeeded":
            last_result = entry_result(entry)
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
