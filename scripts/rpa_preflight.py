"""Bounded RPA candidate preflight and backfill scheduler."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time


def run_preflight(
    args,
    candidate_payload,
    *,
    fetch_material,
    materialize_records,
    material_entry,
    failure_entry,
    write_artifacts,
    elapsed,
):
    candidate_limit = args.candidate_top_n or len(candidate_payload["materials"])
    materials = candidate_payload["materials"][:candidate_limit]
    target_top_n = args.target_top_n or len(materials)
    started_at = time.perf_counter()
    completed = {}
    next_index = 0
    futures = {}
    stopped = False

    def run_one(index):
        material = materials[index]
        material_id = str(material.get("materialId") or "").strip()
        queued_at = elapsed(started_at)
        request_started = time.perf_counter()
        try:
            outcome = fetch_material(args, material_id)
        except Exception as exc:
            reason = (
                "rpa_request_failed"
                if exc.__class__.__name__ in {"RpaPreflightDeadlineExceeded", "RpaAdapterError"}
                else "unknown_preflight_failure"
            )
            outcome = failure_entry(material_id, reason)
        finished_at = elapsed(started_at)
        return outcome, {
            "queuedAtSec": queued_at,
            "startedAtSec": queued_at,
            "finishedAtSec": finished_at,
            "totalSec": elapsed(request_started),
        }

    def selected_count():
        return sum(
            entry["preflightStatus"] == "selected" for entry in completed.values()
        )

    def complete(index, outcome, timings):
        material = materials[index]
        if outcome.get("rpaAvailable"):
            try:
                _records, records_path = materialize_records(
                    args, outcome, outcome["materialId"]
                )
                entry = material_entry(material, outcome, "selected", timings)
                entry["diagnosisRecordsPath"] = records_path
                entry["diagnosisCsvPath"] = outcome.get("diagnosisCsvPath")
                completed[index] = entry
                return
            except Exception:
                outcome = failure_entry(outcome.get("materialId"), "rpa_request_failed")
        completed[index] = material_entry(material, outcome, "rejected", timings)

    def persist():
        selected = [
            completed[index]
            for index in sorted(completed)
            if completed[index]["preflightStatus"] == "selected"
        ][:target_top_n]
        rejected = [
            completed[index]
            for index in sorted(completed)
            if completed[index]["preflightStatus"] == "rejected"
        ]
        write_artifacts(args, candidate_payload, materials, selected, rejected, [], started_at)

    if args.concurrency == 1:
        for index in range(len(materials)):
            outcome, timings = run_one(index)
            complete(index, outcome, timings)
            persist()
            if selected_count() >= target_top_n:
                next_index = index + 1
                break
        else:
            next_index = len(materials)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            while (
                next_index < len(materials)
                and selected_count() + len(futures) < target_top_n
                and len(futures) < args.concurrency
            ):
                futures[executor.submit(run_one, next_index)] = next_index
                next_index += 1
            while futures:
                done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    index = futures.pop(future)
                    outcome, timings = future.result()
                    complete(index, outcome, timings)
                if selected_count() >= target_top_n:
                    stopped = True
                while (
                    not stopped
                    and next_index < len(materials)
                    and selected_count() + len(futures) < target_top_n
                    and len(futures) < args.concurrency
                ):
                    futures[executor.submit(run_one, next_index)] = next_index
                    next_index += 1
                persist()

    selected = [
        completed[index]
        for index in sorted(completed)
        if completed[index]["preflightStatus"] == "selected"
    ][:target_top_n]
    rejected = [
        completed[index]
        for index in sorted(completed)
        if completed[index]["preflightStatus"] == "rejected"
    ]
    cancelled = [
        material_entry(
            materials[index], failure_entry(materials[index].get("materialId"), None), "cancelled"
        )
        for index in range(next_index, len(materials))
    ]
    return write_artifacts(args, candidate_payload, materials, selected, rejected, cancelled, started_at)
