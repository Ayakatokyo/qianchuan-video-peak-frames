import threading
import time
import unittest
from types import SimpleNamespace

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_batch_runner  # noqa: E402


class StreamingBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.manifests = []
        self.ready_materials = []

    def _args(self, **overrides):
        values = {
            "target_top_n": 3,
            "candidate_top_n": None,
            "rpa_concurrency": 3,
            "postprocess_concurrency": 3,
            "rpa_wave_barrier": True,
            "rpa_preflight_soft_timeout_sec": 480,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def _stream_entry(material, rank):
        return rpa_batch_runner.stream_entry(material, rank, lambda: {})

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
        return rpa_batch_runner.run_streaming_batch(
            args,
            candidate_payload,
            stream_entry=self._stream_entry,
            resume_state=lambda *_args: ({}, {}, {}, {}, []),
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
            return dict(entry, rpaStatus="selected", postprocessStatus="queued")

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

    def test_completed_postprocess_is_persisted_while_rpa_wave_waits(self):
        persisted_success = threading.Event()

        def attempt_rpa(_args, entry):
            if entry["materialId"] == "m-3":
                self.assertTrue(persisted_success.wait(timeout=0.5))
            return dict(entry, rpaStatus="selected", postprocessStatus="queued")

        def write_json(path, manifest):
            if path == "manifest" and any(
                item["materialId"] == "m-1"
                and item["postprocessStatus"] == "succeeded"
                for item in manifest["materials"]
            ):
                persisted_success.set()
            self._write_json(path, manifest)

        self.run_batch(
            ["m-1", "m-2", "m-3", "m-4"],
            attempt_rpa,
            self._success_postprocess,
            write_json=write_json,
        )

        self.assertTrue(persisted_success.is_set())

    def test_rpa_soft_timeout_is_checkpointed_without_starting_next_wave(self):
        release_first_wave = threading.Event()
        submitted = []
        manifest_seen = threading.Event()
        clock_values = iter([0.0, 0.0, 0.0, 0.0, 481.0])
        current_clock = [0.0]

        def clock():
            try:
                current_clock[0] = next(clock_values)
            except StopIteration:
                pass
            return current_clock[0]

        def attempt_rpa(_args, entry):
            submitted.append(entry["materialId"])
            release_first_wave.wait(timeout=1)
            return dict(entry, rpaStatus="selected", postprocessStatus="queued")

        def write_json(path, manifest):
            if path == "manifest" and any(
                item["materialId"] == "m-1" and item["rpaSoftTimeoutExceeded"]
                for item in manifest["materials"]
            ):
                manifest_seen.set()
            self._write_json(path, manifest)

        thread = threading.Thread(
            target=self.run_batch,
            args=(["m-1", "m-2", "m-3", "m-4"], attempt_rpa, self._success_postprocess),
            kwargs={
                "write_json": write_json,
                "clock": clock,
                "poll_interval_sec": 0.01,
            },
        )
        thread.start()
        self.assertTrue(manifest_seen.wait(timeout=1))
        self.assertEqual(submitted, ["m-1", "m-2", "m-3"])
        release_first_wave.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_candidate_pool_exhaustion_returns_partial_after_final_success_count(self):
        postprocess_calls = []

        def attempt_rpa(_args, entry):
            return dict(entry, rpaStatus="selected", postprocessStatus="queued")

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
        self.assertEqual(self.ready_materials, ["m-1", "m-2"])

    def test_parallel_failure_is_compensated_serially(self):
        phases = []

        def attempt_rpa(_args, entry):
            return dict(
                entry,
                rpaStatus="selected",
                postprocessStatus="queued",
                recordsPersisted=True,
            )

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
            return dict(
                entry,
                rpaStatus="selected",
                postprocessStatus="queued",
                recordsPersisted=True,
            )

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
