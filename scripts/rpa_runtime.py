"""Cross-platform process isolation for bounded RPA preflight calls."""

import multiprocessing
import os
import queue as queue_module


def multiprocessing_context_name():
    return "spawn" if os.name == "nt" else "fork"


def _worker_entry(params, worker, failure_factory, result_queue, persist_records=None):
    try:
        outcome = worker(params)
        if not isinstance(outcome, dict):
            result_queue.put(
                failure_factory(params.get("material_id"), "unknown_preflight_failure")
            )
            return
        records = outcome.get("records")
        records_dir = params.get("diagnosis_records_dir")
        material_id = params.get("material_id")
        if (
            outcome.get("rpaAvailable")
            and isinstance(records, list)
            and records_dir
            and persist_records is not None
        ):
            records_path = persist_records(records, records_dir, material_id)
            outcome = dict(outcome)
            outcome.pop("records", None)
            outcome["diagnosisRecordsPath"] = str(records_path)
        result_queue.put(outcome)
    except Exception:
        result_queue.put(
            failure_factory(params.get("material_id"), "unknown_preflight_failure")
        )


def run_isolated_preflight(
    params,
    timeout_sec,
    worker,
    failure_factory,
    persist_records=None,
    soft_timeout_sec=None,
    late_artifact_grace_sec=0,
):
    context = multiprocessing.get_context(multiprocessing_context_name())
    result_queue = context.Queue()
    process = context.Process(
        target=_worker_entry,
        args=(params, worker, failure_factory, result_queue, persist_records),
    )
    process.start()
    soft_timeout_exceeded = False
    try:
        hard_timeout = float(timeout_sec)
        soft_timeout = hard_timeout if soft_timeout_sec is None else float(soft_timeout_sec)
        soft_timeout = min(soft_timeout, hard_timeout)
        process.join(soft_timeout)
        if process.is_alive() and soft_timeout < hard_timeout:
            soft_timeout_exceeded = True
            process.join(hard_timeout - soft_timeout)
        if process.is_alive() and late_artifact_grace_sec:
            process.join(max(0.0, float(late_artifact_grace_sec)))
        if process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(1)
            failure = failure_factory(params.get("material_id"), "rpa_request_failed")
            failure["hardTimeoutExceeded"] = True
            if soft_timeout_sec is not None:
                failure["softTimeoutExceeded"] = soft_timeout_exceeded
            return failure
        try:
            outcome = result_queue.get(timeout=1)
            if soft_timeout_sec is not None and isinstance(outcome, dict):
                outcome = dict(outcome)
                outcome["softTimeoutExceeded"] = soft_timeout_exceeded
            return outcome
        except queue_module.Empty:
            pass
        failure_reason = (
            "unknown_preflight_failure" if process.exitcode == 0 else "rpa_request_failed"
        )
        failure = failure_factory(params.get("material_id"), failure_reason)
        if soft_timeout_sec is not None:
            failure["softTimeoutExceeded"] = soft_timeout_exceeded
        return failure
    except BaseException:
        if process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(1)
        raise
