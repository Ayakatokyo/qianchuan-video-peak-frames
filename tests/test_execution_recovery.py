"""Regressions for overlapping commands, resumable status and runtime identity."""
import contextlib
import dataclasses
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_peak_frames
import run_standard_analysis as cli
import standard_workflow as workflow
from batch_runtime import write_json_atomically
from workflow_status import standard_run_status


class ExecutionRecoveryTests(unittest.TestCase):
    def request(self):
        return workflow.WorkflowRequest("2026-07-19", "2026-07-25", 2, "123", "shop")

    def test_same_directory_rejects_different_request_without_overwriting_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            with workflow.acquire_workflow_lock(self.request(), tmp):
                with self.assertRaises(workflow.WorkflowLockError):
                    with workflow.acquire_workflow_lock(dataclasses.replace(self.request(), top_n=3), tmp):
                        self.fail("second writer entered the same run directory")
                self.assertEqual(standard_run_status(tmp)["nextAction"], "wait")

    def test_active_resume_rejected_before_heavy_dependency_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            with workflow.acquire_workflow_lock(self.request(), tmp):
                with patch.object(cli, "ensure_prepare_dependencies", side_effect=AssertionError("heavy import")), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = cli.main(["resume", "--start-date", "2026-07-19", "--end-date", "2026-07-25",
                                     "--top-n", "2", "--advertiser-id", "123", "--shop-id", "shop", "--output-dir", tmp])
            self.assertEqual(code, 2)
            self.assertIn("already active", errors.getvalue())
            self.assertNotIn("heavy import", errors.getvalue())

    def test_status_retains_last_complete_progress_after_truncated_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json_atomically(root / "run-summary.json", {"status": "interrupted", "activeStage": "diagnosis_batch"})
            peak = root / "peak-frames"
            peak.mkdir()
            (peak / "batch-progress.jsonl").write_bytes(b'{"observedAtEpoch":1,"stageCounts":{"rpaAcceptedCount":30}}\n{"stageCounts":')
            result = standard_run_status(root)
            self.assertEqual(result["lastProgress"]["stageCounts"]["rpaAcceptedCount"], 30)
            self.assertEqual(result["nextAction"], "resume")

    def test_cli_tuning_reaches_real_batch_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured = []
            def extract(argv):
                args = extract_peak_frames.build_parser().parse_args(argv)
                captured.append((args.download_concurrency, args.postprocess_concurrency))
                return 2  # Stop before any remote work or facts are built.
            with self.assertRaises(workflow.StandardWorkflowError):
                workflow.prepare_standard_run(self.request(), tmp, export_main=lambda args: 0,
                                              extract_main=extract, download_concurrency=1, postprocess_concurrency=3)
            self.assertEqual(captured, [(1, 3)])

    def test_version_is_lightweight_and_identifies_runtime_sources(self):
        with patch.object(cli, "ensure_prepare_dependencies", side_effect=AssertionError("heavy import")), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = cli.main(["version"])
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(len(result["sourceSha256"]), 64)
        self.assertGreater(result["sourceFileCount"], 10)

    def test_stage_summary_carries_version_and_flushes_progress_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as stderr:
            workflow._write_running(self.request(), workflow.workflow_paths(tmp), "diagnosis_batch")
            result = standard_run_status(tmp)
            self.assertEqual(len(result["runtime"]["sourceSha256"]), 64)
            self.assertIn("diagnosis_batch", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
