"""A surviving RPA writer must not overlap a replacement run after SIGKILL."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from batch_runtime import (  # noqa: E402
    BatchLockError, _pid_is_alive, acquire_run_lock, run_lock_state,
)


PARENT = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from batch_runtime import acquire_run_lock
from rpa_runtime import run_isolated_preflight
from extract_peak_frames import _write_diagnosis_records, _publish_worker_rpa_receipt, _preflight_failure
root = Path(sys.argv[2])
mode = sys.argv[3]
def worker(params):
    (root / "child-pid").write_text(str(os.getpid()))
    if mode == "queue":
        return {"largePayload": "x" * 5000000}
    while not (root / "release-child").exists():
        time.sleep(.01)
    csv = root / "m-1.csv"
    csv.write_text("material_info")
    return {"records": [{"materialId": "m-1", "generation": "old-worker"}],
            "diagnosisCsvPath": str(csv), "remoteTaskGroupId": "remote-1"}
params = {"material_id": "m-1", "diagnosis_records_dir": str(root / "records"),
          "rpa_receipts_dir": str(root / "receipts"), "rpa_client_attempt_id": "attempt-1"}
with acquire_run_lock(root / ".batch-run.lock", root / "manifest.json"):
    result = run_isolated_preflight(params, float(sys.argv[4]), worker, _preflight_failure,
                                   _write_diagnosis_records, _publish_worker_rpa_receipt)
    (root / "outcome.json").write_text(json.dumps(result))
'''


class RpaWorkerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock_path = self.root / ".batch-run.lock"

    def _wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("isolated worker did not reach the expected state before the deadline")

    def _start(self, mode="writer", timeout=20):
        parent = subprocess.Popen(
            [sys.executable, "-B", "-c", PARENT, str(ROOT / "scripts"),
             str(self.root), mode, str(timeout)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )

        def cleanup_parent():
            if parent.poll() is None:
                parent.kill()
            parent.communicate(timeout=5)

        self.addCleanup(cleanup_parent)
        self._wait_until(lambda: (self.root / "child-pid").exists())
        child_pid = int((self.root / "child-pid").read_text())

        def cleanup_child():
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.addCleanup(cleanup_child)
        return parent, child_pid

    def _assert_child_stopped(self, pid):
        self._wait_until(lambda: not _pid_is_alive(pid, socket.gethostname()))

    def test_live_orphan_writer_keeps_lease_until_it_exits(self):
        parent, child_pid = self._start()
        # Freeze the child before killing its parent so this checks exclusion
        # independently of how quickly the child's watchdog gets CPU time.
        os.kill(child_pid, signal.SIGSTOP)
        parent.kill()
        parent.wait(timeout=5)
        self.assertEqual(run_lock_state(self.lock_path)[0], "active")
        with self.assertRaises(BatchLockError) as raised:
            acquire_run_lock(self.lock_path, self.root / "manifest.json", reclaim_stale=True)
        self.assertFalse(raised.exception.stale)
        os.kill(child_pid, signal.SIGCONT)
        self._assert_child_stopped(child_pid)
        with acquire_run_lock(self.lock_path, self.root / "manifest.json", reclaim_stale=True):
            records = self.root / "records" / "m-1.json"
            records.parent.mkdir(exist_ok=True)
            records.write_text('[{"generation":"new-owner"}]')
            (self.root / "release-child").touch()
            self.assertEqual(json.loads(records.read_text())[0]["generation"], "new-owner")
            self.assertFalse((self.root / "m-1.csv").exists())
            self.assertFalse((self.root / "receipts" / "m-1.json").exists())

    def test_orphan_queue_feeder_exits_without_parent_reader(self):
        parent, child_pid = self._start(mode="queue")
        parent.kill()
        parent.wait(timeout=5)
        self._assert_child_stopped(child_pid)
        self.assertEqual(run_lock_state(self.lock_path)[0], "stale")

    def test_worker_deadline_survives_a_stopped_parent(self):
        parent, child_pid = self._start(timeout=.5)
        os.kill(parent.pid, signal.SIGSTOP)
        self._assert_child_stopped(child_pid)
        os.kill(parent.pid, signal.SIGCONT)
        parent.wait(timeout=5)
        result = json.loads((self.root / "outcome.json").read_text())
        self.assertEqual(result["failureReason"], "rpa_request_timeout")
        self.assertTrue(result["timeoutExceeded"])
        self.assertEqual(result["workerStage"], "timeout")
        self.assertFalse((self.root / "m-1.csv").exists())


if __name__ == "__main__":
    unittest.main()
