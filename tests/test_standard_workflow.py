import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import standard_workflow  # noqa: E402
import run_standard_analysis  # noqa: E402


class StandardWorkflowTests(unittest.TestCase):
    def request(self):
        return standard_workflow.WorkflowRequest(
            start_date="2026-07-19",
            end_date="2026-07-25",
            top_n=2,
            advertiser_id="1770096415226951",
        )

    def test_prepare_runs_selection_batch_verification_and_fact_package(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def export_main(argv):
                calls.append(("selection", argv))
                return 0

            def extract_main(argv):
                calls.append(("verification" if "--verify" in argv else "batch", argv))
                return 0

            def prepare_facts(**kwargs):
                calls.append(("facts", kwargs))
                return (
                    root / "diagnostic-analysis" / "diagnostic-analysis-data.json",
                    root / "diagnostic-analysis" / "diagnostic-analysis-prompt.md",
                    {},
                )

            summary = standard_workflow.prepare_standard_run(
                self.request(), root,
                export_main=export_main,
                extract_main=extract_main,
                prepare_facts=prepare_facts,
            )

            self.assertEqual(summary["status"], "analysis_pending")
            self.assertEqual([name for name, _value in calls], [
                "selection", "batch", "verification", "facts",
            ])
            self.assertEqual(calls[0][1][calls[0][1].index("--top-n") + 1], "6")
            self.assertEqual(
                calls[0][1][calls[0][1].index("--advertiser-id") + 1],
                "1770096415226951",
            )
            self.assertIn("--target-top-n", calls[1][1])
            self.assertIn("--verify", calls[2][1])
            self.assertEqual(
                calls[3][1]["top_materials_json"],
                root / "peak-frames" / "top_materials_rpa_ready.json",
            )
            persisted = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, summary)

    def test_prepare_uses_configured_shop_id_without_exposing_it_in_request(self):
        batch_argv = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def extract_main(argv):
                if "--verify" not in argv:
                    batch_argv.extend(argv)
                return 0

            with patch.dict(os.environ, {"QIANCHUAN_RPA_SHOP_ID": "shop-1"}):
                standard_workflow.prepare_standard_run(
                    self.request(), root,
                    export_main=lambda _argv: 0,
                    extract_main=extract_main,
                    prepare_facts=lambda **_kwargs: (
                        root / "data.json", root / "prompt.md", {}
                    ),
                )

        self.assertEqual(batch_argv[batch_argv.index("--shop-id") + 1], "shop-1")

    def test_prepare_omits_shop_id_when_not_configured(self):
        batch_argv = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=True):
                standard_workflow.prepare_standard_run(
                    self.request(), root,
                    export_main=lambda _argv: 0,
                    extract_main=lambda argv: batch_argv.append(argv) or 0,
                    prepare_facts=lambda **_kwargs: (
                        root / "data.json", root / "prompt.md", {}
                    ),
                )

        self.assertEqual(len(batch_argv), 2)
        self.assertTrue(all("--shop-id" not in argv for argv in batch_argv))

    def test_prepare_records_request_failure_for_non_request_input(self):
        export_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(standard_workflow.StandardWorkflowError) as error:
                standard_workflow.prepare_standard_run(
                    object(), root,
                    export_main=lambda argv: export_calls.append(argv),
                )

            summary = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(error.exception.stage, "request")
        self.assertEqual(str(error.exception), "request must be a WorkflowRequest")
        self.assertEqual(export_calls, [])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failure"]["stage"], "request")
        self.assertEqual(summary["failure"]["message"], str(error.exception))
        self.assertIn("request", summary)

    def test_prepare_records_request_failure_for_invalid_request_field_types(self):
        export_calls = []
        request = standard_workflow.WorkflowRequest(
            start_date="2026-07-19",
            end_date="2026-07-25",
            top_n="2",
            advertiser_id="1770096415226951",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(standard_workflow.StandardWorkflowError) as error:
                standard_workflow.prepare_standard_run(
                    request, root,
                    export_main=lambda argv: export_calls.append(argv),
                )

            summary = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(error.exception.stage, "request")
        self.assertEqual(str(error.exception), "top_n must be a positive integer")
        self.assertEqual(export_calls, [])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failure"]["stage"], "request")
        self.assertEqual(summary["failure"]["message"], str(error.exception))
        self.assertEqual(summary["request"]["topN"], "2")

    def test_prepare_marks_run_failed_without_preparing_facts_when_batch_fails(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(standard_workflow.StandardWorkflowError) as error:
                standard_workflow.prepare_standard_run(
                    self.request(), root,
                    export_main=lambda _argv: 0,
                    extract_main=lambda argv: 5 if "--verify" not in argv else 0,
                    prepare_facts=lambda **_kwargs: calls.append("facts"),
                )

            self.assertEqual(error.exception.stage, "diagnosis_batch")
            self.assertEqual(calls, [])
            summary = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["failure"]["stage"], "diagnosis_batch")

    def test_finalize_marks_run_complete_only_after_manifest_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            analysis_json = root / "diagnostic-ai-analysis.json"
            analysis_json.write_text("{}", encoding="utf-8")
            standard_workflow.write_run_summary(
                root / "run-summary.json",
                {
                    "schemaVersion": 1,
                    "status": "analysis_pending",
                    "request": {
                        "startDate": "2026-07-19",
                        "endDate": "2026-07-25",
                        "topN": 2,
                        "advertiserId": "1770096415226951",
                    },
                    "artifacts": {},
                    "failure": None,
                },
            )
            summary = standard_workflow.finalize_standard_run(
                root, analysis_json,
                render_report=lambda **_kwargs: (
                    root / "final-report" / "final-report-data.json",
                    root / "final-report" / "final-report.html",
                    {},
                ),
                mark_ready=lambda *_paths: True,
            )

        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["artifacts"]["report"], "final-report/final-report.html")

    def test_finalize_retries_pending_run_after_report_failure(self):
        render_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            analysis_json = root / "diagnostic-ai-analysis.json"
            analysis_json.write_text('{"schemaVersion": 2}', encoding="utf-8")
            standard_workflow.write_run_summary(
                root / "run-summary.json",
                {
                    "schemaVersion": 1,
                    "status": "analysis_pending",
                    "request": {
                        "startDate": "2026-07-19",
                        "endDate": "2026-07-25",
                        "topN": 2,
                        "advertiserId": "1770096415226951",
                    },
                    "artifacts": {},
                    "failure": None,
                },
            )

            def render_report(**_kwargs):
                render_calls.append("rendered")
                if len(render_calls) == 1:
                    raise ValueError("AI JSON needs correction")
                return (
                    root / "final-report" / "final-report-data.json",
                    root / "final-report" / "final-report.html",
                    {},
                )

            with self.assertRaises(standard_workflow.StandardWorkflowError) as error:
                standard_workflow.finalize_standard_run(
                    root, analysis_json, render_report=render_report,
                    mark_ready=lambda *_paths: True,
                )

            retryable = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))
            analysis_json.write_text('{"schemaVersion": 2, "corrected": true}', encoding="utf-8")
            completed = standard_workflow.finalize_standard_run(
                root, analysis_json, render_report=render_report,
                mark_ready=lambda *_paths: True,
            )
            persisted = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(error.exception.stage, "report")
        self.assertEqual(str(error.exception), "AI JSON needs correction")
        self.assertEqual(retryable["status"], "analysis_pending")
        self.assertEqual(retryable["failure"], {
            "stage": "report",
            "message": "AI JSON needs correction",
        })
        self.assertEqual(render_calls, ["rendered", "rendered"])
        self.assertEqual(completed["status"], "complete")
        self.assertIsNone(completed["failure"])
        self.assertEqual(persisted, completed)


class StandardWorkflowCliTests(unittest.TestCase):
    def test_prepare_parser_accepts_only_business_inputs(self):
        parser = run_standard_analysis.build_parser()
        args = parser.parse_args([
            "prepare",
            "--start-date", "2026-07-19",
            "--end-date", "2026-07-25",
            "--top-n", "9",
            "--advertiser-id", "1770096415226951",
            "--output-dir", "run",
        ])

        self.assertEqual(args.command, "prepare")
        self.assertFalse(hasattr(args, "rpa_concurrency"))
        self.assertFalse(hasattr(args, "resume"))

    def test_finalize_parser_requires_analysis_json(self):
        parser = run_standard_analysis.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                parser.parse_args(["finalize", "--output-dir", "run"])

        self.assertEqual(error.exception.code, 2)

    def test_finalize_missing_analysis_json_returns_cli_error_code(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = run_standard_analysis.main(["finalize", "--output-dir", "run"])

        self.assertEqual(result, 2)
        self.assertIn("usage:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
