"""Streaming RPA batch orchestration.

The runner owns candidate submission, bounded concurrency, selection/backfill,
postprocess handoff, and manifest checkpoints. External effects are supplied
as hooks so the CLI remains an adapter rather than the scheduler's dependency.
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time


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

    def submit_rpa(executor, index):
        entry = entries[index]
        entry.update({"rpaStatus": "submitted", "status": "running"})
        submitted[index] = entry
        rpa_futures[executor.submit(attempt_rpa, args, entry)] = index

    with ThreadPoolExecutor(
        max_workers=args.rpa_concurrency
    ) as rpa_executor, ThreadPoolExecutor(
        max_workers=args.postprocess_concurrency
    ) as postprocess_executor:
        for index in pending_postprocess:
            postprocess_futures[
                postprocess_executor.submit(attempt_postprocess, args, entries[index])
            ] = index
        accounted = accounted_indices(selected, rejected, cancelled)
        while next_index < len(candidates) and next_index in accounted:
            next_index += 1
        while (
            next_index < len(candidates)
            and len(selected) + len(rpa_futures) < args.target_top_n
            and len(rpa_futures) < args.rpa_concurrency
        ):
            submit_rpa(rpa_executor, next_index)
            next_index += 1
            accounted = accounted_indices(selected, rejected, cancelled)
            while next_index < len(candidates) and next_index in accounted:
                next_index += 1
        persist()

        while rpa_futures or postprocess_futures:
            done, _pending = wait(
                set(rpa_futures) | set(postprocess_futures),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                if future in rpa_futures:
                    index = rpa_futures.pop(future)
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
                else:
                    index = postprocess_futures.pop(future)
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

            while (
                next_index < len(candidates)
                and len(selected) + len(rpa_futures) < args.target_top_n
                and len(rpa_futures) < args.rpa_concurrency
            ):
                submit_rpa(rpa_executor, next_index)
                next_index += 1
                accounted = accounted_indices(selected, rejected, cancelled)
                while next_index < len(candidates) and next_index in accounted:
                    next_index += 1
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
