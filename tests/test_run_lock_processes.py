"""Exercise run locks across actual process boundaries, including abrupt exits."""

import json
import os
from pathlib import Path
import selectors
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
    BatchLockError,
    BatchRunLock,
    acquire_run_lock,
    run_lock_state,
)


OWNER = r'''
import fcntl, json, linecache, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from batch_runtime import acquire_run_lock, BatchLockError
path, mode = Path(sys.argv[2]), sys.argv[3]
if mode == "incomplete":
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.write(fd, b'{"runId":')
    print(json.dumps({"acquired": True}), flush=True)
    sys.stdin.readline()
    os.close(fd)
else:
    if mode == "contender":
        sys.stdin.readline()
    if mode == "paused-owner":
        def pause_before_write(frame, event, arg):
            if event == "line" and frame.f_code.co_name == "acquire_run_lock":
                line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
                if "stream.write(encoded)" in line or "os.write(descriptor, encoded)" in line:
                    sys.settrace(None)
                    print(json.dumps({"paused": True}), flush=True)
                    sys.stdin.readline()
                    return None
            return pause_before_write
        sys.settrace(pause_before_write)
    try:
        lock = acquire_run_lock(path, path.parent / "manifest.json",
                                run_id="same-run", reclaim_stale=True)
    except BatchLockError as exc:
        print(json.dumps({"acquired": False, "stale": exc.stale}), flush=True)
    else:
        if mode == "fork-owner":
            ready_read, ready_write = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(ready_read)
                os.write(ready_write, b"ready")
                os.close(ready_write)
                time.sleep(30)
                os._exit(0)
            os.close(ready_write)
            os.read(ready_read, 5)
            os.close(ready_read)
        print(json.dumps({"acquired": True,
                          "childPid": child if mode == "fork-owner" else None}), flush=True)
        sys.stdin.readline()
        lock.release()
'''


class RunLockProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / ".batch-run.lock"
        self.manifest = self.root / "manifest.json"

    def _owner(self, mode="owner"):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", OWNER, str(ROOT / "scripts"),
             str(self.path), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

        self.addCleanup(cleanup)
        return process

    def _result(self, process):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=10), "lock process did not respond")
        line = process.stdout.readline()
        self.assertTrue(line, "lock process exited before returning its result")
        result = json.loads(line)
        if result.get("childPid"):
            def cleanup_child():
                try:
                    os.kill(result["childPid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.addCleanup(cleanup_child)
        return result

    def test_incomplete_payload_cannot_be_reclaimed_while_os_lock_is_held(self):
        owner = self._owner("incomplete")
        self.assertTrue(self._result(owner)["acquired"])
        original = self.path.stat().st_ino
        self.assertEqual(run_lock_state(self.path)[0], "active")
        with self.assertRaises(BatchLockError) as raised:
            acquire_run_lock(self.path, self.manifest, reclaim_stale=True)
        self.assertFalse(raised.exception.stale)
        self.assertEqual(self.path.stat().st_ino, original)

    def test_acquire_creation_to_write_window_remains_exclusive(self):
        # Trace only controls scheduling; both contenders execute the real
        # acquisition, JSON persistence and kernel locking implementation.
        owner = self._owner("paused-owner")
        self.assertTrue(self._result(owner)["paused"])
        with self.assertRaises(BatchLockError) as raised:
            acquire_run_lock(self.path, self.manifest, reclaim_stale=True)
        self.assertFalse(raised.exception.stale)
        self.assertEqual(run_lock_state(self.path)[0], "active")
        owner.stdin.write("continue\n")
        owner.stdin.flush()
        self.assertTrue(self._result(owner)["acquired"])

    def test_simultaneous_stale_reclaim_has_exactly_one_owner(self):
        self.path.write_text(json.dumps({
            "runId": "old-run", "pid": 99999999,
            "hostname": socket.gethostname(),
        }), encoding="utf-8")
        original = self.path.stat().st_ino
        contenders = [self._owner("contender") for _ in range(8)]
        for contender in contenders:
            contender.stdin.write("start\n")
            contender.stdin.flush()
        results = [self._result(contender) for contender in contenders]
        self.assertEqual(sum(result["acquired"] for result in results), 1)
        self.assertEqual(self.path.stat().st_ino, original)

    def test_sigkill_requires_explicit_recovery_then_reacquires_same_inode(self):
        owner = self._owner()
        self.assertTrue(self._result(owner)["acquired"])
        original = self.path.stat().st_ino
        self.assertEqual(run_lock_state(self.path)[0], "active")
        owner.send_signal(signal.SIGKILL)
        owner.wait(timeout=5)
        self.assertEqual(run_lock_state(self.path)[0], "stale")
        with self.assertRaises(BatchLockError) as raised:
            acquire_run_lock(self.path, self.manifest)
        self.assertTrue(raised.exception.stale)
        with acquire_run_lock(self.path, self.manifest, reclaim_stale=True):
            self.assertEqual(self.path.stat().st_ino, original)
            self.assertEqual(run_lock_state(self.path)[0], "active")
        self.assertEqual(run_lock_state(self.path), ("missing", None))

    def test_releasing_old_handle_cannot_release_same_run_successor(self):
        with acquire_run_lock(self.path, self.manifest, run_id="same-run") as owner:
            obsolete = BatchRunLock(self.path, {"runId": "same-run", "ownerToken": "old"})
            obsolete.release()
            self.assertEqual(run_lock_state(self.path)[0], "active")
            self.assertEqual(json.loads(self.path.read_text()), owner.payload)

    def test_forked_child_does_not_keep_killed_owners_lock_alive(self):
        owner = self._owner("fork-owner")
        self.assertTrue(self._result(owner)["acquired"])
        owner.send_signal(signal.SIGKILL)
        owner.wait(timeout=5)
        self.assertEqual(run_lock_state(self.path)[0], "stale")
        with acquire_run_lock(self.path, self.manifest, reclaim_stale=True):
            self.assertEqual(run_lock_state(self.path)[0], "active")

    def test_forked_child_release_does_not_unlock_parent(self):
        with acquire_run_lock(self.path, self.manifest) as owner:
            child = os.fork()
            if child == 0:
                owner.release()
                os._exit(0)
            os.waitpid(child, 0)
            self.assertEqual(run_lock_state(self.path)[0], "active")

    def test_normal_release_preserves_inode_and_allows_acquisition_without_reclaim(self):
        with acquire_run_lock(self.path, self.manifest):
            original = self.path.stat().st_ino
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.stat().st_ino, original)
        self.assertEqual(run_lock_state(self.path), ("missing", None))
        with acquire_run_lock(self.path, self.manifest):
            self.assertEqual(self.path.stat().st_ino, original)

    def test_status_does_not_create_or_modify_lock_files(self):
        self.assertEqual(run_lock_state(self.path), ("missing", None))
        self.assertFalse(self.path.exists())
        with acquire_run_lock(self.path, self.manifest):
            before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
            self.assertEqual(run_lock_state(self.path)[0], "active")
            self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_unlocked_new_payload_is_stale_even_when_its_pid_still_exists(self):
        with acquire_run_lock(self.path, self.manifest) as owner:
            payload = owner.payload
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(run_lock_state(self.path)[0], "stale")
        with acquire_run_lock(self.path, self.manifest, reclaim_stale=True):
            self.assertEqual(run_lock_state(self.path)[0], "active")

    def test_live_legacy_payload_cannot_be_reclaimed(self):
        self.path.write_text(json.dumps({
            "runId": "legacy-run", "pid": os.getpid(),
            "hostname": socket.gethostname(),
        }), encoding="utf-8")
        with self.assertRaises(BatchLockError) as raised:
            acquire_run_lock(self.path, self.manifest, reclaim_stale=True)
        self.assertFalse(raised.exception.stale)
        self.assertEqual(json.loads(self.path.read_text())["runId"], "legacy-run")

    def test_legacy_zombie_pid_does_not_count_as_a_live_owner(self):
        process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
        self.addCleanup(process.wait, timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            # Do not poll/wait the child until after the assertion: it must stay a zombie.
            observed = subprocess.run(
                ["ps", "-p", str(process.pid), "-o", "stat="],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            if observed.startswith("Z"):
                break
            time.sleep(0.01)
        self.assertTrue(observed.startswith("Z"), "child did not become a zombie")
        self.path.write_text(json.dumps({
            "runId": "dead-run", "pid": process.pid,
            "hostname": socket.gethostname(),
        }), encoding="utf-8")
        self.assertEqual(run_lock_state(self.path)[0], "stale")
        with acquire_run_lock(self.path, self.manifest, reclaim_stale=True):
            self.assertEqual(run_lock_state(self.path)[0], "active")


if __name__ == "__main__":
    unittest.main()
