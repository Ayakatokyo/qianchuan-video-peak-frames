"""Cross-platform process isolation for bounded RPA preflight calls."""

import multiprocessing
import os
import queue as queue_module
import signal
import threading
import time
import traceback

from batch_runtime import inherit_run_locks


_WORKER_TIMEOUT_EXIT = 124
_WORKER_ORPHAN_EXIT = 125


def _watch_worker_lifetime(owner_pid, deadline):
    while True:
        if os.getppid() != owner_pid:
            os._exit(_WORKER_ORPHAN_EXIT)
        remaining = deadline - time.monotonic() if deadline is not None else .1
        if remaining <= 0:
            os._exit(_WORKER_TIMEOUT_EXIT)
        time.sleep(min(.1, remaining))


def _start_worker_watchdog(owner_pid, timeout_sec):
    # Check synchronously before calling worker: the parent can die between
    # fork and the first instruction in this entrypoint.
    if os.getppid() != owner_pid:
        os._exit(_WORKER_ORPHAN_EXIT)
    deadline = (
        time.monotonic() + max(0., float(timeout_sec))
        if timeout_sec is not None else None
    )
    threading.Thread(
        target=_watch_worker_lifetime, args=(owner_pid, deadline),
        name="rpa-worker-watchdog", daemon=True,
    ).start()
    # Keep the daemon alive after _worker_entry returns: multiprocessing's
    # Queue feeder may still be waiting for a parent that will never read it.


def multiprocessing_context_name():
    return "spawn" if os.name == "nt" else "fork"


def _worker_entry(
    params,
    worker,
    failure_factory,
    result_queue,
    persist_records=None,
    publish_receipt=None,
    owner_pid=None,
    timeout_sec=None,
):
    if owner_pid is not None:
        _start_worker_watchdog(owner_pid, timeout_sec)
    stage = "worker"
    try:
        outcome = worker(params)
        stage = "validate_worker_result"
        if not isinstance(outcome, dict):
            failure = failure_factory(params.get("material_id"), "unknown_preflight_failure")
            failure.update({"workerStage": stage, "errorType": "InvalidWorkerResult"})
            result_queue.put(failure)
            return
        records = outcome.get("records")
        records_dir = params.get("diagnosis_records_dir")
        material_id = params.get("material_id")
        if isinstance(records, list) and records_dir and persist_records is not None:
            stage = "persist_records"
            if publish_receipt is not None and outcome.get("diagnosisCsvPath"):
                stage = "publish_artifact_receipt"
                publish_receipt(params, outcome, "artifact_downloaded")
                stage = "persist_records"
            records_path = persist_records(records, records_dir, material_id)
            if publish_receipt is not None and outcome.get("diagnosisCsvPath"):
                stage = "publish_records_receipt"
                publish_receipt(
                    params, outcome, "records_persisted", records_path=str(records_path)
                )
            outcome = dict(outcome)
            outcome.pop("records", None)
            outcome["diagnosisRecordsPath"] = str(records_path)
        stage = "result_queue_put"
        result_queue.put(outcome)
    except Exception as exc:
        failure = failure_factory(params.get("material_id"), "unknown_preflight_failure")
        failure.update(
            {
                "workerStage": stage,
                "errorType": type(exc).__name__,
                "errorMessage": str(exc),
                "errorTraceback": traceback.format_exc(limit=12),
            }
        )
        result_queue.put(failure)


def run_isolated_preflight(
    params,
    timeout_sec,
    worker,
    failure_factory,
    persist_records=None,
    publish_receipt=None,
):
    timeout_sec = float(timeout_sec)
    context = multiprocessing.get_context(multiprocessing_context_name())
    result_queue = context.Queue()
    process = context.Process(
        target=_worker_entry,
        args=(
            params,
            worker,
            failure_factory,
            result_queue,
            persist_records,
            publish_receipt,
            os.getpid(),
            timeout_sec,
        ),
    )
    with inherit_run_locks():
        process.start()
    try:
        process.join(timeout_sec)
        if process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(1)
            failure = failure_factory(params.get("material_id"), "rpa_request_timeout")
            failure["timeoutExceeded"] = True
            failure["timeoutSec"] = timeout_sec
            failure["workerStage"] = "timeout"
            failure["childExitCode"] = process.exitcode
            return failure
        if process.exitcode == _WORKER_TIMEOUT_EXIT:
            failure = failure_factory(params.get("material_id"), "rpa_request_timeout")
            failure["timeoutExceeded"] = True
            failure["timeoutSec"] = timeout_sec
            failure["workerStage"] = "timeout"
            failure["childExitCode"] = process.exitcode
            return failure
        try:
            outcome = result_queue.get(timeout=1)
            return outcome
        except queue_module.Empty:
            pass
        failure_reason = (
            "unknown_preflight_failure" if process.exitcode == 0 else "rpa_request_failed"
        )
        failure = failure_factory(params.get("material_id"), failure_reason)
        failure["childExitCode"] = process.exitcode
        failure["childSignal"] = (
            signal.Signals(-process.exitcode).name
            if process.exitcode is not None and process.exitcode < 0
            else None
        )
        failure["workerStage"] = "result_queue_read"
        failure["resultQueueEmpty"] = True
        return failure
    except BaseException:
        if process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(1)
        raise
