from copy import deepcopy
import threading
import time
import unittest
from types import SimpleNamespace

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_batch_runner  # noqa: E402
from schemas import validate_manifest  # noqa: E402


class WaveBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.manifests = []
        self.ready_materials = []

    def _args(self, **overrides):
        values = {
            "target_top_n": 3,
            "candidate_top_n": None,
            "rpa_concurrency": 3,
            "download_concurrency": 2,
            "postprocess_concurrency": 1,
            "rpa_wave_barrier": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def _wave_entry(material, rank):
        return rpa_batch_runner.wave_entry(material, rank, lambda: {})

    @staticmethod
    def _merge_timings(previous, attempt):
        merged = dict(previous)
        merged.update(attempt)
        return merged

    @staticmethod
    def _success_postprocess(_args, entry):
        material_id = entry["materialId"]
        return {
            "result": ("/tmp", f"/tmp/{material_id}.json", f"/tmp/{material_id}.md"),
            "error": None,
            "timingsSec": {},
        }

    @staticmethod
    def _verified_rpa_success(entry):
        material_id = entry["materialId"]
        return {
            **entry,
            "rpaStatus": "selected",
            "rpaResultStatus": "available",
            "rpaCsvVerified": True,
            "diagnosisCsvPath": f"diagnosis-csv/{material_id}.csv",
            "recordsPersisted": True,
            "diagnosisRecordsPath": f"diagnosis-records/{material_id}.json",
            "records": [{"materialId": material_id}],
            "postprocessStatus": "queued",
        }

    def _new_manifest(self, _args, _candidates, entries, submitted, selected, rejected, cancelled):
        materials = rpa_batch_runner.ordered(entries)
        return {
            "materials": materials,
            "submittedMaterials": rpa_batch_runner.ordered(submitted),
            "selectedMaterials": rpa_batch_runner.ordered(selected),
            "rejectedMaterials": rpa_batch_runner.ordered(rejected),
            "cancelledMaterials": rpa_batch_runner.ordered(cancelled),
            "stageCounts": {
                "postprocessSucceededCount": sum(
                    item["postprocessStatus"] == "succeeded" for item in materials
                ),
            },
            "timingsSec": {},
        }

    def _write_json(self, path, payload):
        if path == "manifest":
            self.manifests.append(payload)
        else:
            self.ready_materials = [item["materialId"] for item in payload["materials"]]

    def run_batch(self, material_ids, attempt_rpa, attempt_postprocess, **overrides):
        args = self._args(**overrides.pop("args", {}))
        candidate_payload = {"materials": [{"materialId": item} for item in material_ids]}
        return rpa_batch_runner.run_wave_batch(
            args,
            candidate_payload,
            wave_entry=self._wave_entry,
            resume_state=overrides.pop("resume_state", lambda *_args: ({}, {}, {}, {}, [])),
            attempt_rpa=attempt_rpa,
            attempt_postprocess=attempt_postprocess,
            merge_timings=self._merge_timings,
            new_manifest=self._new_manifest,
            write_json=overrides.pop("write_json", self._write_json),
            manifest_path="manifest",
            ready_payload=lambda _payload, materials: {"materials": materials},
            ready_path="ready",
            entry_result=lambda entry: entry["summaryJson"],
            elapsed=lambda _started: 0.0,
            accounted_indices=rpa_batch_runner.accounted_indices,
            ordered=rpa_batch_runner.ordered,
            **overrides,
        )

    def test_postprocess_failure_is_retried_then_backfilled_to_final_target(self):
        rpa_calls = []
        postprocess_calls = []

        def attempt_rpa(_args, entry):
            rpa_calls.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            postprocess_calls.append((entry["materialId"], entry["postprocessAttemptCount"]))
            if entry["materialId"] == "m-2":
                return {"result": None, "error": RuntimeError("ffmpeg"), "timingsSec": {}}
            return self._success_postprocess(_args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"], attempt_rpa, attempt_postprocess
        )

        self.assertEqual(set(rpa_calls), {"m-1", "m-2", "m-3", "m-4"})
        self.assertEqual(postprocess_calls.count(("m-2", 1)), 1)
        self.assertEqual(postprocess_calls.count(("m-2", 2)), 1)
        self.assertFalse(failures)
        self.assertEqual(
            [
                item["materialId"]
                for item in manifest["materials"]
                if item["postprocessStatus"] == "succeeded"
            ],
            ["m-1", "m-3", "m-4"],
        )

    def test_unverified_csv_result_is_finalized_without_postprocess(self):
        postprocess_calls = []

        def attempt_rpa(_args, entry):
            return {
                **entry,
                "rpaStatus": "selected",
                "rpaResultStatus": "available",
                "rpaCsvVerified": False,
                "diagnosisCsvPath": None,
                "postprocessStatus": "queued",
            }

        def attempt_postprocess(_args, entry):
            postprocess_calls.append(entry["materialId"])
            return self._success_postprocess(_args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1"], attempt_rpa, attempt_postprocess, args={"target_top_n": 1}
        )

        self.assertTrue(failures)
        self.assertEqual(postprocess_calls, [])
        self.assertEqual(
            [
                (
                    item["rpaStatus"],
                    item["rpaResultStatus"],
                    item["failureReason"],
                )
                for item in manifest["rejectedMaterials"]
            ],
            [("rejected", "final_failed", "rpa_csv_artifact_unavailable")],
        )

    def test_final_rpa_failure_releases_next_wave(self):
        starts = []

        def attempt_rpa(_args, entry):
            starts.append(entry["materialId"])
            if entry["materialId"] == "m-2":
                return {
                    **entry,
                    "rpaStatus": "rejected",
                    "rpaResultStatus": "final_failed",
                    "postprocessStatus": "not_started",
                }
            return self._verified_rpa_success(entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3"],
            attempt_rpa,
            self._success_postprocess,
            args={"target_top_n": 2, "rpa_concurrency": 2},
        )

        self.assertFalse(failures)
        self.assertEqual(starts, ["m-1", "m-2", "m-3"])
        self.assertEqual(
            [item["materialId"] for item in manifest["selectedMaterials"]],
            ["m-1", "m-3"],
        )

    def test_timeout_releases_next_full_wave_after_first_wave_is_terminal(self):
        release_timeout = threading.Event()
        first_two_finished = threading.Event()
        timeout_ready = threading.Event()
        timeout_terminal = threading.Event()
        starts = []
        early_later_starts = []
        lock = threading.Lock()
        first_two = set()

        def attempt_rpa(_args, entry):
            material_id = entry["materialId"]
            with lock:
                starts.append(material_id)
                if material_id in {"m-4", "m-5", "m-6"} and not timeout_terminal.is_set():
                    early_later_starts.append(material_id)
            if material_id in {"m-2", "m-3"}:
                with lock:
                    first_two.add(material_id)
                    if first_two == {"m-2", "m-3"}:
                        first_two_finished.set()
                return self._verified_rpa_success(entry)
            if material_id == "m-1":
                first_two_finished.wait(timeout=1)
                timeout_ready.set()
                release_timeout.wait(timeout=1)
                timeout_terminal.set()
                return {
                    **entry,
                    "rpaStatus": "rejected",
                    "rpaResultStatus": "final_failed",
                    "failureReason": "rpa_request_timeout",
                    "rpaTimeoutExceeded": True,
                    "rpaTimeoutSec": 480,
                    "postprocessStatus": "not_started",
                }
            return self._verified_rpa_success(entry)

        outcome = []
        errors = []

        def run():
            try:
                outcome.append(
                    self.run_batch(
                        ["m-1", "m-2", "m-3", "m-4", "m-5", "m-6"],
                        attempt_rpa,
                        self._success_postprocess,
                        args={"target_top_n": 6, "rpa_concurrency": 3},
                        poll_interval_sec=0.01,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        runner = threading.Thread(target=run)
        runner.start()
        self.assertTrue(timeout_ready.wait(timeout=1))
        with lock:
            self.assertEqual(set(starts), {"m-1", "m-2", "m-3"})
        release_timeout.set()
        runner.join(timeout=2)

        self.assertFalse(runner.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(early_later_starts, [])
        self.assertEqual(set(starts[3:]), {"m-4", "m-5", "m-6"})
        manifest, failures, _ = outcome[0]
        self.assertTrue(failures)
        timeout_entry = next(
            item for item in manifest["rejectedMaterials"] if item["materialId"] == "m-1"
        )
        self.assertEqual(timeout_entry["rpaResultStatus"], "final_failed")
        self.assertTrue(timeout_entry["rpaTimeoutExceeded"])
        self.assertEqual(timeout_entry["rpaTimeoutSec"], 480)

    def test_nonterminal_first_rpa_result_pauses_before_next_wave(self):
        starts = []

        def attempt_rpa(_args, entry):
            starts.append(entry["materialId"])
            if entry["materialId"] == "m-2":
                return {
                    **entry,
                    "rpaStatus": "rejected",
                    "rpaResultStatus": "pending",
                    "postprocessStatus": "not_started",
                }
            return self._verified_rpa_success(entry)

        manifest, _failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3"],
            attempt_rpa,
            self._success_postprocess,
            args={"target_top_n": 2, "rpa_concurrency": 2},
        )

        self.assertEqual(starts, ["m-1", "m-2"])
        rejected = manifest["rejectedMaterials"][0]
        self.assertEqual(rejected["materialId"], "m-2")
        self.assertEqual(rejected["rpaResultStatus"], "pending")
        self.assertEqual(manifest["firstBatchCsvGate"]["status"], "unconfirmed")

    def test_selected_final_failure_does_not_enter_postprocess(self):
        postprocess_calls = []

        def attempt_rpa(_args, entry):
            return {
                **entry,
                "rpaStatus": "selected",
                "rpaResultStatus": "final_failed",
                "postprocessStatus": "queued",
            }

        def attempt_postprocess(_args, entry):
            postprocess_calls.append(entry["materialId"])
            return self._success_postprocess(_args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1"], attempt_rpa, attempt_postprocess, args={"target_top_n": 1}
        )

        self.assertTrue(failures)
        self.assertEqual(postprocess_calls, [])
        self.assertEqual(manifest["rejectedMaterials"][0]["rpaStatus"], "rejected")

    def test_postprocess_starts_while_later_rpa_is_collecting(self):
        rpa_finished = []
        observed_rpa_sets = []
        postprocess_started = threading.Event()
        overlap = []

        def attempt_rpa(_args, entry):
            if entry["materialId"] == "m-3":
                overlap.append(postprocess_started.wait(timeout=1))
            rpa_finished.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(args, entry):
            observed_rpa_sets.append(set(rpa_finished))
            postprocess_started.set()
            return self._success_postprocess(args, entry)

        self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"],
            attempt_rpa,
            attempt_postprocess,
            args={"target_top_n": 4, "rpa_concurrency": 2},
        )

        self.assertEqual(set(rpa_finished), {"m-1", "m-2", "m-3", "m-4"})
        self.assertTrue(observed_rpa_sets)
        self.assertEqual(overlap, [True])
        self.assertTrue(any("m-3" not in observed for observed in observed_rpa_sets))

    def test_each_manifest_checkpoint_emits_compact_progress(self):
        progress = []

        self.run_batch(
            ["m-1"],
            lambda _args, entry: self._verified_rpa_success(entry),
            self._success_postprocess,
            args={"target_top_n": 1},
            write_progress=lambda manifest: progress.append(
                dict(manifest["stageCounts"])
            ),
        )

        self.assertGreaterEqual(len(progress), 2)
        self.assertEqual(progress[-1]["postprocessSucceededCount"], 1)

    def test_wave_does_not_submit_next_candidate_until_current_wave_finishes(self):
        rpa_starts = []
        postprocess_started = threading.Event()
        release_postprocess = threading.Event()

        def attempt_rpa(_args, entry):
            rpa_starts.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            if entry["materialId"] == "m-1":
                postprocess_started.set()
                release_postprocess.wait(timeout=1)
            return self._success_postprocess(_args, entry)

        runner = threading.Thread(
            target=self.run_batch,
            args=(["m-1", "m-2", "m-3"], attempt_rpa, attempt_postprocess),
            kwargs={"args": {"target_top_n": 2, "rpa_concurrency": 2}},
        )
        runner.start()
        self.assertTrue(postprocess_started.wait(timeout=1))
        deadline = time.monotonic() + 1
        while len(rpa_starts) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(rpa_starts, ["m-1", "m-2"])
        release_postprocess.set()
        runner.join(timeout=2)

        self.assertFalse(runner.is_alive())

    def test_each_postprocess_has_its_own_persisted_records(self):
        rpa_starts = []
        observed_rpa_sets = []

        def attempt_rpa(_args, entry):
            rpa_starts.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            observed_rpa_sets.append(set(rpa_starts))
            self.assertTrue(entry["recordsPersisted"])
            self.assertTrue(entry["rpaCsvVerified"])
            self.assertEqual(entry["records"], [{"materialId": entry["materialId"]}])
            accepted = [item for item in self.manifests[-1]["selectedMaterials"]
                        if item["materialId"] == entry["materialId"]]
            self.assertEqual(len(accepted), 1)
            self.assertTrue(accepted[0]["recordsPersisted"])
            return self._success_postprocess(_args, entry)

        self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"],
            attempt_rpa,
            attempt_postprocess,
            args={"target_top_n": 4, "rpa_concurrency": 2},
        )
        self.assertEqual(rpa_starts[:2], ["m-1", "m-2"])
        self.assertEqual(set(rpa_starts[2:]), {"m-3", "m-4"})
        self.assertTrue(observed_rpa_sets)
        self.assertEqual(len(observed_rpa_sets), 4)

    def test_wave_backfills_only_after_terminal_postprocess_failure(self):
        rpa_starts = []

        def attempt_rpa(_args, entry):
            rpa_starts.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            if entry["materialId"] == "m-1":
                return {"result": None, "error": RuntimeError("ffmpeg"), "timingsSec": {}}
            return self._success_postprocess(_args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3"],
            attempt_rpa,
            attempt_postprocess,
            args={"target_top_n": 2, "rpa_concurrency": 2},
        )

        by_id = {entry["materialId"]: entry for entry in manifest["materials"]}
        self.assertFalse(failures)
        self.assertEqual(rpa_starts, ["m-1", "m-2", "m-3"])
        self.assertEqual(by_id["m-3"]["backfillReason"], "postprocess_failed_after_retries")

    def test_wave_manifest_preserves_wave_size_after_rpa_completion(self):
        def attempt_rpa(_args, entry):
            return self._verified_rpa_success(entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"],
            attempt_rpa,
            self._success_postprocess,
            args={"target_top_n": 4},
        )
        manifest.update({"schemaVersion": 2, "materialIds": ["m-1", "m-2", "m-3", "m-4"]})

        self.assertFalse(failures)
        validate_manifest(manifest)
        self.assertEqual(
            [entry["waveSize"] for entry in manifest["selectedMaterials"]],
            [3, 3, 3, 1],
        )
        self.assertTrue(
            all(
                entry["waveCsvArtifactFinishedAtSec"] >= entry["waveStartedAtSec"]
                and entry["waveCsvArtifactFinishedAtSec"] >= entry["rpaFinishedAtSec"]
                for entry in manifest["selectedMaterials"]
            )
        )

    def test_new_wave_entries_omit_legacy_soft_and_hard_timeout_fields(self):
        entry = self._wave_entry({"materialId": "m-1"}, 1)

        self.assertNotIn("rpaSoftTimeoutExceeded", entry)
        self.assertNotIn("rpaHardTimeoutExceeded", entry)

    def test_wave_submission_checkpoint_omits_completion_timestamp(self):
        release_wave = threading.Event()
        checkpoint_seen = threading.Event()
        captured_manifests = []

        def attempt_rpa(_args, entry):
            release_wave.wait(timeout=1)
            return self._verified_rpa_success(entry)

        def write_json(path, manifest):
            if path == "manifest":
                captured_manifests.append(manifest)
                if any(
                    entry["rpaStatus"] == "submitted"
                    for entry in manifest["materials"]
                ):
                    checkpoint_seen.set()
            self._write_json(path, manifest)

        runner = threading.Thread(
            target=self.run_batch,
            args=(["m-1"], attempt_rpa, self._success_postprocess),
            kwargs={"write_json": write_json, "poll_interval_sec": 0.01},
        )
        runner.start()
        try:
            self.assertTrue(checkpoint_seen.wait(timeout=1))
            checkpoint = next(
                manifest
                for manifest in captured_manifests
                if manifest["materials"][0]["rpaStatus"] == "submitted"
            )
            self.assertNotIn("waveFinishedAtSec", checkpoint["materials"][0])
        finally:
            release_wave.set()
            runner.join(timeout=1)
        self.assertFalse(runner.is_alive())

    def test_wave_batch_checkpoints_interrupt_before_second_wave(self):
        connector_calls = []

        def attempt_rpa(_args, entry):
            connector_calls.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        with self.assertRaises(rpa_batch_runner.BatchInterrupted):
            self.run_batch(
                ["m-1", "m-2", "m-3", "m-4", "m-5", "m-6"],
                attempt_rpa,
                self._success_postprocess,
                args={"target_top_n": 6},
                is_interrupted=lambda: len(connector_calls) == 3,
            )

        self.assertEqual(set(connector_calls), {"m-1", "m-2", "m-3"})
        checkpoint = self.manifests[-1]
        self.assertEqual(checkpoint["interruption"]["reason"], "signal")
        self.assertTrue(
            set(checkpoint["interruption"]["pendingMaterialIds"])
            <= {"m-1", "m-2", "m-3"}
        )

    def test_rpa_future_exception_records_unconfirmed_diagnostics(self):
        def attempt_rpa(_args, _entry):
            raise RuntimeError("worker process exited")

        manifest, failures, _ = self.run_batch(
            ["m-1"], attempt_rpa, self._success_postprocess, args={"target_top_n": 1}
        )

        rejected = manifest["rejectedMaterials"][0]
        self.assertTrue(failures)
        self.assertEqual(rejected["rpaStatus"], "rejected")
        self.assertEqual(rejected.get("rpaResultStatus"), "unknown")
        self.assertEqual(rejected["errorType"], "RuntimeError")
        self.assertEqual(rejected["errorMessage"], "worker process exited")

    def test_candidate_pool_exhaustion_returns_partial_after_final_success_count(self):
        postprocess_calls = []

        def attempt_rpa(_args, entry):
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            postprocess_calls.append((entry["materialId"], entry["postprocessAttemptCount"]))
            if entry["materialId"] == "m-3":
                return {"result": None, "error": RuntimeError("download"), "timingsSec": {}}
            return self._success_postprocess(_args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3"], attempt_rpa, attempt_postprocess
        )

        self.assertTrue(failures)
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 2)
        self.assertEqual(postprocess_calls.count(("m-3", 2)), 1)
        self.assertEqual(self.ready_materials, [])

    def test_parallel_failure_is_compensated_serially(self):
        phases = []

        def attempt_rpa(_args, entry):
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            phases.append((entry["materialId"], entry["postprocessPhase"]))
            if (
                entry["materialId"] == "m-1"
                and entry["postprocessPhase"] == "parallel"
            ):
                return {"result": None, "error": RuntimeError("download"), "timingsSec": {}}
            return self._success_postprocess(_args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3"], attempt_rpa, attempt_postprocess
        )

        first = next(item for item in manifest["materials"] if item["materialId"] == "m-1")
        self.assertFalse(failures)
        self.assertEqual(first["rpaStatus"], "selected")
        self.assertEqual(first["postprocessStatus"], "succeeded")
        self.assertEqual(first["compensationStatus"], "succeeded")
        self.assertEqual(first["postprocessAttemptCount"], 3)
        self.assertEqual(phases.count(("m-1", "parallel")), 2)
        self.assertEqual(phases.count(("m-1", "serial_compensation")), 1)

    def test_failed_compensation_preserves_parallel_error_chain(self):
        def attempt_rpa(_args, entry):
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            message = "parallel download" if entry["postprocessPhase"] == "parallel" else "serial download"
            return {"result": None, "error": RuntimeError(message), "timingsSec": {}}

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3"], attempt_rpa, attempt_postprocess
        )

        first = next(item for item in manifest["materials"] if item["materialId"] == "m-1")
        self.assertTrue(failures)
        self.assertEqual(first["rpaStatus"], "selected")
        self.assertEqual(first["compensationStatus"], "failed")
        self.assertEqual(first["postprocessAttemptCount"], 3)
        self.assertEqual(
            [attempt["errorMessage"] for attempt in first["postprocessAttempts"]],
            ["parallel download", "parallel download", "serial download"],
        )
        self.assertEqual(self.ready_materials, [])

    def test_download_checkpoint_is_persisted_before_worker_continues(self):
        observed = []

        def attempt_postprocess(args, entry):
            checkpoint = getattr(args, "_postprocess_checkpoint", None)
            if checkpoint is not None:
                checkpoint(entry["materialId"], {
                    "videoPath": "/tmp/persisted.mp4", "videoBytes": 42,
                    "videoSha256": "retained-hash", "postprocessSubstage": "frame_extraction",
                })
            observed.append(any(
                item.get("videoSha256") == "retained-hash"
                for manifest in self.manifests for item in manifest["materials"]
            ))
            return self._success_postprocess(args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1"], lambda _args, entry: self._verified_rpa_success(entry),
            attempt_postprocess, args={"target_top_n": 1},
        )
        self.assertFalse(failures)
        self.assertEqual(observed, [True])
        self.assertEqual(manifest["materials"][0]["videoSha256"], "retained-hash")

    def test_postprocess_queue_only_starts_bounded_active_set(self):
        started = []
        active = threading.Event()
        release = threading.Event()
        snapshots = []

        def attempt_postprocess(args, entry):
            started.append(entry["materialId"])
            active.set()
            release.wait(timeout=3)
            return self._success_postprocess(args, entry)

        def write_json(path, payload):
            if path == "manifest":
                snapshots.append(deepcopy(payload))
            self._write_json(path, payload)

        runner = threading.Thread(target=self.run_batch, args=(
            [f"m-{i}" for i in range(12)],
            lambda _args, entry: self._verified_rpa_success(entry),
            attempt_postprocess,
        ), kwargs={"args": {"target_top_n": 12, "download_concurrency": 1,
                             "postprocess_concurrency": 1}, "write_json": write_json})
        runner.start()
        try:
            self.assertTrue(active.wait(timeout=2))
            deadline = time.monotonic() + 1
            while not snapshots or not any(
                item["postprocessAttemptCount"] for item in snapshots[-1]["materials"]
            ):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            snapshot = snapshots[-1]
            self.assertLessEqual(sum(
                item["postprocessAttemptCount"] for item in snapshot["materials"]
            ), 2)
            self.assertLessEqual(len(started), 2)
        finally:
            release.set()
            runner.join(timeout=3)
        self.assertFalse(runner.is_alive())

    def test_interrupted_attempt_does_not_exhaust_parallel_retry_budget(self):
        observed_phases = []

        def resume_state(_args, _candidates, entries):
            entry = self._verified_rpa_success(entries[0])
            entry.update({
                "postprocessAttemptCount": 2,
                "postprocessStatus": "failed",
                "postprocessAttempts": [
                    {"phase": "parallel", "status": "failed"},
                    {"phase": "parallel", "status": "interrupted"},
                ],
            })
            entries[0] = entry
            return {0: entry}, {0: entry}, {}, {}, [0]

        def attempt_postprocess(args, entry):
            observed_phases.append(entry["postprocessPhase"])
            return self._success_postprocess(args, entry)

        _manifest, failures, _ = self.run_batch(
            ["m-1"], lambda *_: self.fail("RPA must not run"), attempt_postprocess,
            args={"target_top_n": 1}, resume_state=resume_state,
        )
        self.assertFalse(failures)
        self.assertEqual(observed_phases, ["parallel"])

    def test_postprocess_signal_stops_new_work_and_checkpoint_contains_manifest(self):
        stop = threading.Event()
        started = []

        def attempt_postprocess(args, entry):
            started.append(entry["materialId"])
            stop.set()
            return self._success_postprocess(args, entry)

        with self.assertRaises(rpa_batch_runner.BatchInterrupted) as raised:
            self.run_batch(
                [f"m-{i}" for i in range(12)],
                lambda _args, entry: self._verified_rpa_success(entry),
                attempt_postprocess,
                args={"target_top_n": 12, "download_concurrency": 1,
                      "postprocess_concurrency": 1}, is_interrupted=stop.is_set,
            )
        self.assertLessEqual(len(started), 2)
        self.assertIn("materials", raised.exception.manifest)

    def test_concurrent_video_checkpoints_keep_every_material_and_serialize_writes(self):
        workers_ready = threading.Barrier(4)
        write_counter = 0
        max_writers = 0
        retained_args = []

        def write_json(path, payload):
            nonlocal write_counter, max_writers
            write_counter += 1
            max_writers = max(max_writers, write_counter)
            time.sleep(0.001)
            self._write_json(path, deepcopy(payload))
            write_counter -= 1

        def attempt_postprocess(args, entry):
            retained_args.append(args)
            workers_ready.wait(timeout=2)
            checkpoint = getattr(args, "_postprocess_checkpoint", None)
            if checkpoint:
                checkpoint(entry["materialId"], {
                    "videoPath": f"/tmp/{entry['materialId']}.mp4",
                    "videoBytes": 42, "videoSha256": f"hash-{entry['materialId']}",
                    "postprocessSubstage": "frame_extraction",
                })
            return self._success_postprocess(args, entry)

        manifest, failures, _ = self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"],
            lambda _args, entry: self._verified_rpa_success(entry), attempt_postprocess,
            args={"target_top_n": 4, "download_concurrency": 2,
                  "postprocess_concurrency": 2}, write_json=write_json,
        )
        self.assertFalse(failures)
        self.assertEqual(max_writers, 1)
        self.assertEqual(
            [entry.get("videoSha256") for entry in manifest["materials"]],
            ["hash-m-1", "hash-m-2", "hash-m-3", "hash-m-4"],
        )
        self.assertFalse(hasattr(retained_args[0], "_postprocess_checkpoint"))

    def test_signal_during_last_compensation_does_not_publish_ready_output(self):
        stop = threading.Event()

        def attempt_postprocess(args, entry):
            if entry["postprocessPhase"] == "parallel":
                return {"result": None, "error": RuntimeError("ffmpeg"), "timingsSec": {}}
            stop.set()
            return self._success_postprocess(args, entry)

        with self.assertRaises(rpa_batch_runner.BatchInterrupted):
            self.run_batch(
                ["m-1"], lambda _args, entry: self._verified_rpa_success(entry),
                attempt_postprocess, args={"target_top_n": 1}, is_interrupted=stop.is_set,
            )
        self.assertEqual(self.ready_materials, [])

    def test_terminal_backfill_does_not_queue_more_rpa_than_active_wave(self):
        stop = threading.Event()
        started = []

        def resume_state(_args, _candidates, entries):
            selected = {}
            for index in range(6):
                entry = self._verified_rpa_success(entries[index])
                entry.update(postprocessAttemptCount=3, postprocessStatus="failed",
                             compensationStatus="failed")
                entries[index] = selected[index] = entry
            return dict(selected), selected, {}, {}, []

        def attempt_rpa(_args, entry):
            started.append(entry["materialId"])
            stop.set()
            return self._verified_rpa_success(entry)

        with self.assertRaises(rpa_batch_runner.BatchInterrupted):
            self.run_batch(
                [f"m-{i}" for i in range(12)], attempt_rpa,
                self._success_postprocess,
                args={"target_top_n": 6, "rpa_concurrency": 2},
                resume_state=resume_state, is_interrupted=stop.is_set,
            )
        self.assertLessEqual(len(started), 2)
        self.assertEqual(set(started), {"m-6", "m-7"})


if __name__ == "__main__":
    unittest.main()
