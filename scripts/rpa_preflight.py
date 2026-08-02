"""Bounded RPA candidate preflight and backfill scheduler."""

from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, ThreadPoolExecutor, wait
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
    rpa_concurrency = getattr(args, "rpa_concurrency", args.concurrency)

    def run_one(index, wave_index=None, wave_size=None, wave_started_at=None):
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
        timings = {
            "queuedAtSec": queued_at,
            "startedAtSec": queued_at,
            "finishedAtSec": finished_at,
            "totalSec": elapsed(request_started),
        }
        if wave_index is not None:
            timings.update(
                {
                    "waveIndex": wave_index,
                    "waveSize": wave_size,
                    "waveStartedAtSec": wave_started_at,
                }
            )
        return outcome, timings

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

    if rpa_concurrency == 1:
        for index in range(len(materials)):
            wave_started_at = elapsed(started_at)
            outcome, timings = run_one(
                index,
                wave_index=index,
                wave_size=1,
                wave_started_at=wave_started_at,
            )
            timings["waveFinishedAtSec"] = elapsed(started_at)
            complete(index, outcome, timings)
            persist()
            if selected_count() >= target_top_n:
                next_index = index + 1
                break
        else:
            next_index = len(materials)
    else:
        with ThreadPoolExecutor(max_workers=rpa_concurrency) as executor:
            if getattr(args, "rpa_wave_barrier", True):
                wave_index = 0
                while next_index < len(materials) and selected_count() < target_top_n:
                    wave = {}
                    wave_started_at = elapsed(started_at)
                    while (
                        next_index < len(materials)
                        and len(wave) < rpa_concurrency
                        and selected_count() + len(wave) < target_top_n
                    ):
                        wave[executor.submit(
                            run_one,
                            next_index,
                            wave_index,
                            None,
                            wave_started_at,
                        )] = next_index
                        next_index += 1
                    wave_size = len(wave)
                    wait(set(wave), return_when=ALL_COMPLETED)
                    wave_finished_at = elapsed(started_at)
                    for future, index in sorted(wave.items(), key=lambda item: item[1]):
                        outcome, timings = future.result()
                        timings.update(
                            {
                                "waveSize": wave_size,
                                "waveStartedAtSec": wave_started_at,
                                "waveFinishedAtSec": wave_finished_at,
                            }
                        )
                        complete(index, outcome, timings)
                    persist()
                    wave_index += 1
            else:
                while (
                    next_index < len(materials)
                    and selected_count() + len(futures) < target_top_n
                    and len(futures) < rpa_concurrency
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
                        and len(futures) < rpa_concurrency
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
