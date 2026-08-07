import queue
import os
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rpa_runtime import _worker_entry, run_isolated_preflight  # noqa: E402


class RpaRuntimeReceiptTests(unittest.TestCase):
    def test_worker_exception_preserves_stage_and_traceback(self):
        result_queue = queue.Queue()

        def worker(_params):
            raise RuntimeError("gateway response decode failed")

        _worker_entry(
            {"material_id": "m-error"},
            worker,
            lambda material_id, reason: {
                "materialId": material_id,
                "failureReason": reason,
            },
            result_queue,
        )

        outcome = result_queue.get_nowait()
        self.assertEqual(outcome["failureReason"], "unknown_preflight_failure")
        self.assertEqual(outcome["errorType"], "RuntimeError")
        self.assertEqual(outcome["errorMessage"], "gateway response decode failed")
        self.assertEqual(outcome["workerStage"], "worker")
        self.assertIn("RuntimeError", outcome["errorTraceback"])

    def test_isolated_preflight_reports_nonzero_child_exit_and_empty_queue(self):
        def worker(_params):
            os._exit(17)

        outcome = run_isolated_preflight(
            {"material_id": "m-exit"},
            timeout_sec=1,
            worker=worker,
            failure_factory=lambda material_id, reason: {
                "materialId": material_id,
                "failureReason": reason,
            },
        )

        self.assertEqual(outcome["failureReason"], "rpa_request_failed")
        self.assertEqual(outcome["childExitCode"], 17)
        self.assertTrue(outcome["resultQueueEmpty"])

    def test_worker_publishes_csv_receipt_before_records_receipt(self):
        material_id = "m-1"
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / f"{material_id}.csv"
            csv_path.write_text("material_info", encoding="utf-8")
            records_dir = root / "records"
            result_queue = queue.Queue()

            def worker(_params):
                return {
                    "rpaAvailable": True,
                    "records": [{"materialId": material_id}],
                    "diagnosisCsvPath": str(csv_path),
                }

            def persist_records(records, directory, identifier):
                target = Path(directory) / f"{identifier}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(records), encoding="utf-8")
                return target

            def publish_receipt(_params, _outcome, state, records_path=None):
                events.append((state, records_path, records_path is None or Path(records_path).is_file()))

            _worker_entry(
                {"material_id": material_id, "diagnosis_records_dir": records_dir},
                worker,
                lambda *_args: {"rpaAvailable": False},
                result_queue,
                persist_records=persist_records,
                publish_receipt=publish_receipt,
            )

            outcome = result_queue.get_nowait()

        self.assertEqual(
            events,
            [
                ("artifact_downloaded", None, True),
                ("records_persisted", str(records_dir / f"{material_id}.json"), True),
            ],
        )
        self.assertEqual(outcome["diagnosisRecordsPath"], str(records_dir / f"{material_id}.json"))
        self.assertNotIn("records", outcome)


if __name__ == "__main__":
    unittest.main()
