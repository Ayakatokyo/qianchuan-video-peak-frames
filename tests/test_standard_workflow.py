import io
import json
import os
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import standard_workflow  # noqa: E402
import run_standard_analysis  # noqa: E402
from asset_metrics_lib import build_asset_facts_payload  # noqa: E402


def write_rendered_report(root):
    report_dir = root / "final-report"
    report_dir.mkdir(exist_ok=True)
    data_path = report_dir / "final-report-data.json"
    presentation_path = report_dir / "presentation-report.json"
    html_path = report_dir / "final-report.html"
    data_path.write_text("{}", encoding="utf-8")
    presentation_path.write_text("{}", encoding="utf-8")
    html_path.write_text("<main>report</main>", encoding="utf-8")
    return data_path, html_path, {}


class StandardWorkflowTests(unittest.TestCase):
    def request(self):
        return standard_workflow.WorkflowRequest(
            start_date="2026-07-19",
            end_date="2026-07-25",
            top_n=2,
            advertiser_id="1770096415226951",
            shop_id="shop-1",
        )

    def request_payload(self):
        return {
            "startDate": "2026-07-19",
            "endDate": "2026-07-25",
            "topN": 2,
            "advertiserId": "1770096415226951",
            "shopId": "shop-1",
        }

    def test_status_exposes_distinct_rpa_artifact_and_postprocess_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            standard_workflow.write_run_summary(
                root / "run-summary.json", {"status": "running", "activeStage": "diagnosis_batch"}
            )
            peak = root / "peak-frames"
            peak.mkdir()
            (peak / "batch-manifest.json").write_text(
                json.dumps({
                    "stageCounts": {
                        "rpaRequestCount": 16,
                        "rpaAcceptedCount": 12,
                        "rpaSelectedCount": 12,
                        "recordsPersistedCount": 12,
                        "postprocessSucceededCount": 7,
                    }
                }),
                encoding="utf-8",
            )

            status = standard_workflow.standard_run_status(root)

        self.assertEqual(status["rpaRequestCount"], 16)
        self.assertEqual(status["rpaAcceptedCount"], 12)
        self.assertEqual(status["recordsPersistedCount"], 12)
        self.assertEqual(status["postprocessSucceededCount"], 7)

    def write_ready_materials(self, root, material_ids):
        ready_path = root / "peak-frames" / "top_materials_rpa_ready.json"
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(json.dumps({
            "materials": [{"materialId": material_id} for material_id in material_ids]
        }), encoding="utf-8")

    def write_success_manifest(self, root, material_ids, *, target_top_n):
        peak_frames = root / "peak-frames"
        peak_frames.mkdir(parents=True, exist_ok=True)
        selected = [
            {
                "rank": rank,
                "materialId": material_id,
                "rpaStatus": "selected",
                "postprocessStatus": "succeeded",
            }
            for rank, material_id in enumerate(material_ids, start=1)
        ]
        (peak_frames / "batch-manifest.json").write_text(json.dumps({
            "schemaVersion": 2,
            "targetTopN": target_top_n,
            "materials": selected,
            "selectedMaterials": selected,
            "rejectedMaterials": [],
            "cancelledMaterials": [],
        }), encoding="utf-8")
        (peak_frames / "batch-verification.json").write_text(json.dumps({
            "valid": True,
            "materialCount": len(material_ids),
            "materialIds": material_ids,
        }), encoding="utf-8")

    def write_complete_ready_data(self, root):
        material_ids = ["m-1", "m-2"]
        self.write_ready_materials(root, material_ids)
        self.write_success_manifest(root, material_ids, target_top_n=2)
        self.write_asset_facts(root, material_ids)

    def write_asset_facts(self, root, material_ids=("m-1", "m-2")):
        payload = build_asset_facts_payload(
            "2026-07-19", "2026-07-25", None, (), "1770096415226951", {
                material_id: {
                    "stat_cost_for_overall_roi2": Decimal("10"),
                    "total_order_settle_amount_for_roi2_1h": Decimal("20"),
                    "total_pay_order_gmv_include_coupon_for_roi2": Decimal("30"),
                }
                for material_id in material_ids
            }
        )
        (root / "asset-material-facts-v1.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_prepare_runs_selection_batch_verification_and_fact_package(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def export_main(argv):
                calls.append(("selection", argv))
                self.write_asset_facts(root)
                return 0

            def extract_main(argv):
                calls.append(("verification" if "--verify" in argv else "batch", argv))
                if "--verify" in argv:
                    self.write_complete_ready_data(root)
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
            self.assertIn("--asset-facts-output", calls[0][1])
            self.assertIn("--target-top-n", calls[1][1])
            self.assertIn("--verify", calls[2][1])
            self.assertNotIn("--asset-facts-output", calls[1][1])
            self.assertNotIn("--asset-facts-output", calls[2][1])
            self.assertEqual(
                calls[3][1]["top_materials_json"],
                root / "peak-frames" / "top_materials_rpa_ready.json",
            )
            self.assertEqual(
                calls[3][1]["top_material_roi_facts_path"],
                root / "peak-frames" / "top-material-roi-facts-v1.json",
            )
            persisted = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, summary)

    def test_prepare_writes_top_roi_facts_only_after_rpa_verification(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def export_main(_argv):
                self.write_asset_facts(root)
                return 0

            def extract_main(argv):
                calls.append("verification" if "--verify" in argv else "batch")
                self.assertFalse(
                    (root / "peak-frames" / "top-material-roi-facts-v1.json").exists()
                )
                if "--verify" in argv:
                    self.write_complete_ready_data(root)
                return 0

            standard_workflow.prepare_standard_run(
                self.request(), root,
                export_main=export_main,
                extract_main=extract_main,
                prepare_facts=lambda **_kwargs: (root / "data.json", root / "prompt.md", {}),
            )

            roi_facts = json.loads(
                (root / "peak-frames" / "top-material-roi-facts-v1.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(calls, ["batch", "verification"])
        self.assertEqual(roi_facts["materialIds"], ["m-1", "m-2"])
        self.assertEqual(roi_facts["metrics"][0]["value"], "2")

    def test_prepare_passes_the_same_required_shop_id_to_selection_and_rpa(self):
        selection_argv = []
        batch_argv = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_complete_ready_data(root)

            def export_main(argv):
                selection_argv.extend(argv)
                return 0

            def extract_main(argv):
                if "--verify" not in argv:
                    batch_argv.extend(argv)
                return 0

            summary = standard_workflow.prepare_standard_run(
                self.request(), root,
                export_main=export_main,
                extract_main=extract_main,
                prepare_facts=lambda **_kwargs: (
                    root / "data.json", root / "prompt.md", {}
                ),
            )

        self.assertEqual(selection_argv[selection_argv.index("--shop-id") + 1], "shop-1")
        self.assertEqual(batch_argv[batch_argv.index("--shop-id") + 1], "shop-1")
        self.assertEqual(summary["request"]["shopId"], "shop-1")
        self.assertEqual(summary["artifacts"]["sourceData"], "source-data")

    def test_prepare_does_not_build_facts_from_partial_ready_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_ready_materials(root, ["m-1"])
            self.write_success_manifest(root, ["m-1"], target_top_n=2)

            with self.assertRaisesRegex(
                standard_workflow.StandardWorkflowError, "ready materials"
            ):
                standard_workflow.prepare_standard_run(
                    self.request(),
                    root,
                    export_main=lambda _argv: 0,
                    extract_main=lambda _argv: 0,
                    prepare_facts=lambda **_kwargs: self.fail("facts must not run"),
                )

    def test_prepare_rejects_v2_while_same_request_is_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "qianchuan-run"
            versioned = root / "qianchuan-run-v2"
            lock = standard_workflow.acquire_workflow_lock(self.request(), original)
            try:
                with self.assertRaisesRegex(
                    standard_workflow.StandardWorkflowError, "already running"
                ):
                    standard_workflow.prepare_standard_run(
                        self.request(),
                        versioned,
                        export_main=lambda _argv: self.fail("selection must not run"),
                        extract_main=lambda _argv: self.fail("RPA must not run"),
                    )
            finally:
                lock.release()

            self.assertFalse((versioned / "run-summary.json").exists())

    def test_prepare_lock_conflict_preserves_existing_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "qianchuan-run"
            versioned = root / "qianchuan-run-v2"
            summary_path = versioned / "run-summary.json"
            original_summary = b'{"status":"existing"}\n'
            summary_path.parent.mkdir(parents=True)
            summary_path.write_bytes(original_summary)
            lock = standard_workflow.acquire_workflow_lock(self.request(), original)
            try:
                with self.assertRaisesRegex(
                    standard_workflow.StandardWorkflowError, "already running"
                ):
                    standard_workflow.prepare_standard_run(
                        self.request(),
                        versioned,
                        export_main=lambda _argv: self.fail("selection must not run"),
                        extract_main=lambda _argv: self.fail("RPA must not run"),
                    )
            finally:
                lock.release()

            self.assertEqual(summary_path.read_bytes(), original_summary)

    def test_status_is_read_only_and_reports_manifest_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "run-summary.json"
            manifest_path = root / "peak-frames" / "batch-manifest.json"
            standard_workflow.write_run_summary(summary_path, {
                "schemaVersion": 1,
                "status": "running",
                "activeStage": "diagnosis_batch",
                "request": self.request_payload(),
                "artifacts": {},
                "failure": None,
            })
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps({"stageCounts": {
                "rpaAcceptedCount": 2,
                "postprocessSucceededCount": 1,
                "reportReadyCount": 0,
            }}), encoding="utf-8")
            before = (summary_path.read_bytes(), manifest_path.read_bytes())

            status = standard_workflow.standard_run_status(root)

            self.assertEqual(status["workflowStatus"], "running")
            self.assertEqual(status["activeStage"], "diagnosis_batch")
            self.assertEqual(status["rpaAcceptedCount"], 2)
            self.assertEqual(status["postprocessSucceededCount"], 1)
            self.assertEqual(status["reportReadyCount"], 0)
            self.assertIsNone(status["lock"])
            self.assertEqual(before, (summary_path.read_bytes(), manifest_path.read_bytes()))

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
            shop_id="shop-1",
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

    def test_prepare_surfaces_verification_schema_error_in_run_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def extract_main(argv):
                if "--verify" in argv:
                    raise RuntimeError(
                        "batch manifest material waveSize must be a positive integer"
                    )
                return 0

            with self.assertRaises(standard_workflow.StandardWorkflowError) as error:
                standard_workflow.prepare_standard_run(
                    self.request(),
                    root,
                    export_main=lambda _argv: 0,
                    extract_main=extract_main,
                    prepare_facts=lambda **_kwargs: self.fail("facts must not run"),
                )
            summary = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(error.exception.stage, "verification")
        self.assertEqual(
            str(error.exception),
            "batch manifest material waveSize must be a positive integer",
        )
        self.assertEqual(summary["failure"], {
            "stage": "verification",
            "message": "batch manifest material waveSize must be a positive integer",
        })

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
                        "shopId": "shop-1",
                    },
                    "artifacts": {},
                    "failure": None,
                },
            )
            summary = standard_workflow.finalize_standard_run(
                root, analysis_json,
                render_report=lambda **_kwargs: write_rendered_report(root),
                mark_ready=lambda *_paths: True,
            )
            archive_exists = (root / "final-report.zip").is_file()

        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["artifacts"]["report"], "final-report/final-report.html")
        self.assertEqual(summary["artifacts"]["reportArchive"], "final-report.zip")
        self.assertTrue(archive_exists)

    def test_finalize_requires_dense_ai_analysis(self):
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
                        "shopId": "shop-1",
                    },
                    "artifacts": {},
                    "failure": None,
                },
            )

            def render_report(**kwargs):
                render_calls.append(kwargs)
                return write_rendered_report(root)

            standard_workflow.finalize_standard_run(
                root,
                analysis_json,
                render_report=render_report,
                mark_ready=lambda *_paths: True,
            )

        self.assertEqual(len(render_calls), 1)
        self.assertTrue(render_calls[0]["copy_assets"])
        self.assertTrue(render_calls[0]["require_ai_quality"])

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
                        "shopId": "shop-1",
                    },
                    "artifacts": {},
                    "failure": None,
                },
            )

            def render_report(**_kwargs):
                render_calls.append("rendered")
                if len(render_calls) == 1:
                    raise ValueError("AI JSON needs correction")
                return write_rendered_report(root)

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
    def test_prepare_prints_artifact_paths_without_artifact_path_helper(self):
        stdout = io.StringIO()
        with patch.object(run_standard_analysis, "ensure_prepare_dependencies"), patch.object(
            run_standard_analysis,
            "prepare_standard_run",
            return_value={
                "artifacts": {
                    "diagnosticData": "diagnostic-analysis/diagnostic-analysis-data.json",
                    "diagnosticPrompt": "diagnostic-analysis/diagnostic-analysis-prompt.md",
                }
            },
        ), redirect_stdout(stdout):
            result = run_standard_analysis.main(
                [
                    "prepare",
                    "--start-date", "2026-07-19",
                    "--end-date", "2026-07-25",
                    "--top-n", "9",
                    "--advertiser-id", "1770096415226951",
                    "--shop-id", "shop-1",
                    "--output-dir", "/tmp/qianchuan-run",
                ]
            )

        self.assertEqual(result, 0)
        self.assertIn(
            "diagnostic_analysis_data=/tmp/qianchuan-run/diagnostic-analysis/diagnostic-analysis-data.json",
            stdout.getvalue(),
        )
        self.assertIn(
            "diagnostic_analysis_prompt=/tmp/qianchuan-run/diagnostic-analysis/diagnostic-analysis-prompt.md",
            stdout.getvalue(),
        )
        self.assertFalse(hasattr(run_standard_analysis, "_artifact_path"))

    def test_prepare_parser_accepts_only_business_inputs(self):
        parser = run_standard_analysis.build_parser()
        args = parser.parse_args([
            "prepare",
            "--start-date", "2026-07-19",
            "--end-date", "2026-07-25",
            "--top-n", "9",
            "--advertiser-id", "1770096415226951",
            "--shop-id", "shop-1",
            "--output-dir", "run",
        ])

        self.assertEqual(args.command, "prepare")
        self.assertFalse(hasattr(args, "rpa_concurrency"))
        self.assertFalse(hasattr(args, "resume"))

    def test_prepare_reports_dependency_bootstrap_failures(self):
        stderr = io.StringIO()
        with patch.object(
            run_standard_analysis,
            "ensure_prepare_dependencies",
            side_effect=run_standard_analysis.DependencyBootstrapError("uv is unavailable"),
        ), redirect_stderr(stderr):
            result = run_standard_analysis.main(
                [
                    "prepare",
                    "--start-date", "2026-07-19",
                    "--end-date", "2026-07-25",
                    "--top-n", "9",
                    "--advertiser-id", "1770096415226951",
                    "--shop-id", "shop-1",
                    "--output-dir", "run",
                ]
            )

        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "dependencies: uv is unavailable\n")

    def test_finalize_parser_requires_analysis_json(self):
        parser = run_standard_analysis.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                parser.parse_args(["finalize", "--output-dir", "run"])

        self.assertEqual(error.exception.code, 2)

    def test_status_parser_requires_output_dir(self):
        parser = run_standard_analysis.build_parser()
        args = parser.parse_args(["status", "--output-dir", "run"])

        self.assertEqual(args.command, "status")

    def test_finalize_missing_analysis_json_returns_cli_error_code(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = run_standard_analysis.main(["finalize", "--output-dir", "run"])

        self.assertEqual(result, 2)
        self.assertIn("usage:", stderr.getvalue())

    def test_finalize_prints_final_report_zip_path(self):
        stdout = io.StringIO()
        with patch.object(
            run_standard_analysis,
            "finalize_standard_run",
            return_value={
                "artifacts": {
                    "report": "final-report/final-report.html",
                    "reportArchive": "final-report.zip",
                }
            },
        ):
            with redirect_stdout(stdout):
                result = run_standard_analysis.main(
                    [
                        "finalize",
                        "--output-dir",
                        "/tmp/qianchuan-run",
                        "--analysis-json",
                        "/tmp/analysis.json",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn(
            "final_report_zip=/tmp/qianchuan-run/final-report.zip",
            stdout.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
