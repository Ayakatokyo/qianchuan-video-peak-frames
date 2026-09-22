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

    def test_nonterminal_rpa_result_is_finalized_before_next_wave(self):
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

        self.assertEqual(starts, ["m-1", "m-2", "m-3"])
        rejected = manifest["rejectedMaterials"][0]
        self.assertEqual(rejected["materialId"], "m-2")
        self.assertEqual(rejected["rpaResultStatus"], "final_failed")
        self.assertEqual(rejected["failureReason"], "rpa_csv_artifact_unavailable")

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

    def test_rpa_collection_finishes_before_postprocess_starts(self):
        rpa_finished = []
        observed_rpa_sets = []

        def attempt_rpa(_args, entry):
            rpa_finished.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(args, entry):
            observed_rpa_sets.append(set(rpa_finished))
            return self._success_postprocess(args, entry)

        self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"],
            attempt_rpa,
            attempt_postprocess,
            args={"target_top_n": 4, "rpa_concurrency": 2},
        )

        self.assertEqual(set(rpa_finished), {"m-1", "m-2", "m-3", "m-4"})
        self.assertTrue(observed_rpa_sets)
        self.assertTrue(
            all(
                observed == {"m-1", "m-2", "m-3", "m-4"}
                for observed in observed_rpa_sets
            )
        )

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

    def test_all_rpa_waves_finish_before_any_postprocess(self):
        rpa_starts = []
        observed_rpa_sets = []

        def attempt_rpa(_args, entry):
            rpa_starts.append(entry["materialId"])
            return self._verified_rpa_success(entry)

        def attempt_postprocess(_args, entry):
            observed_rpa_sets.append(set(rpa_starts))
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
        self.assertTrue(
            all(
                observed == {"m-1", "m-2", "m-3", "m-4"}
                for observed in observed_rpa_sets
            )
        )

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

    def test_rpa_future_exception_records_final_diagnostics(self):
        def attempt_rpa(_args, _entry):
            raise RuntimeError("worker process exited")

        manifest, failures, _ = self.run_batch(
            ["m-1"], attempt_rpa, self._success_postprocess, args={"target_top_n": 1}
        )

        rejected = manifest["rejectedMaterials"][0]
        self.assertTrue(failures)
        self.assertEqual(rejected["rpaStatus"], "rejected")
        self.assertEqual(rejected.get("rpaResultStatus"), "final_failed")
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


if __name__ == "__main__":
    unittest.main()
