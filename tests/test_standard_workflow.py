import io
import json
import os
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
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
            self.assertNotIn("diagnosticData", summary["artifacts"])
            self.assertEqual(summary["schemaVersion"], 2)
            self.assertEqual(
                summary["artifacts"]["analysisUnitManifest"],
                "diagnostic-analysis/analysis-units/analysis-unit-manifest.json",
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
                prepare_facts=lambda **_kwargs: (root / "prompt.md", {}),
            )

            roi_facts = json.loads(
                (root / "peak-frames" / "top-material-roi-facts-v1.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(calls, ["batch", "verification"])
        self.assertEqual(roi_facts["materialIds"], ["m-1", "m-2"])
        self.assertEqual(roi_facts["metrics"][0]["value"], "2")

    def test_prepare_passes_report_shop_id_to_selection_and_summary(self):
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
                    root / "prompt.md", {}
                ),
            )

        self.assertEqual(selection_argv[selection_argv.index("--shop-id") + 1], "shop-1")
        self.assertNotIn("--shop-id", batch_argv)
        self.assertNotIn("--date-range-type", batch_argv)
        self.assertNotIn("--custom-start-date", batch_argv)
        self.assertNotIn("--custom-end-date", batch_argv)
        self.assertEqual(
            json.loads(batch_argv[batch_argv.index("--params-json") + 1]),
            {
                "date_type": "CUSTOM",
                "custom_start_date": "2026-07-19",
                "custom_end_date": "2026-07-25",
            },
        )
        self.assertEqual(summary["request"]["shopId"], "shop-1")
        self.assertEqual(summary["artifacts"]["sourceData"], "source-data")

    def test_prepare_does_not_pass_report_shop_id_to_rpa(self):
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

            standard_workflow.prepare_standard_run(
                self.request(), root,
                export_main=export_main,
                extract_main=extract_main,
                prepare_facts=lambda **_kwargs: (
                    root / "prompt.md", {}
                ),
            )

        self.assertEqual(selection_argv[selection_argv.index("--shop-id") + 1], "shop-1")
        self.assertNotIn("--shop-id", batch_argv)

    def test_prepare_passes_selected_rpa_shop_id_without_comparing_id_domains(self):
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

            request = standard_workflow.WorkflowRequest(
                start_date="2026-07-19",
                end_date="2026-07-25",
                top_n=2,
                advertiser_id="1770096415226951",
                shop_id="4456758065503561",
                rpa_shop_id="408IGuUKaf",
            )
            summary = standard_workflow.prepare_standard_run(
                request, root,
                export_main=export_main,
                extract_main=extract_main,
                prepare_facts=lambda **_kwargs: (root / "prompt.md", {}),
            )

        self.assertEqual(selection_argv[selection_argv.index("--shop-id") + 1], "4456758065503561")
        self.assertEqual(batch_argv[batch_argv.index("--shop-id") + 1], "408IGuUKaf")
        self.assertEqual(summary["request"]["rpaShopId"], "408IGuUKaf")

    def test_fetch_metadata_writes_metadata_ready_summary_without_rpa(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def fetch_metadata(shop_id, advertiser_id, start_date, end_date, *, output_dir):
                calls.append((shop_id, advertiser_id, start_date, end_date, output_dir))
                Path(output_dir).mkdir(parents=True)
                (Path(output_dir) / "gateway-response.json").write_text("{}", encoding="utf-8")
                return {"resultType": "files"}

            summary = standard_workflow.fetch_report_metadata(
                standard_workflow.WorkflowRequest(
                    start_date="2026-07-19",
                    end_date="2026-07-25",
                    top_n=None,
                    advertiser_id="1770096415226951",
                    shop_id="shop-1",
                ),
                root,
                fetch_metadata=fetch_metadata,
            )

            persisted = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["status"], "metadata_ready")
        self.assertEqual(summary["request"]["topN"], None)
        self.assertEqual(summary["artifacts"]["sourceData"], "source-data")
        self.assertEqual(persisted, summary)
        self.assertEqual(calls, [
            ("shop-1", "1770096415226951", "2026-07-19", "2026-07-25", root / "source-data")
        ])

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

            self.assertEqual(status["workflowStatus"], "interrupted")
            self.assertEqual(status["persistedWorkflowStatus"], "running")
            self.assertEqual(status["lockState"], "missing")
            self.assertTrue(status["recoveryRequired"])
            self.assertEqual(status["activeStage"], "diagnosis_batch")
            self.assertEqual(status["rpaAcceptedCount"], 2)
            self.assertEqual(status["postprocessSucceededCount"], 1)
            self.assertEqual(status["reportReadyCount"], 0)
            self.assertIsNone(status["lock"])
            self.assertEqual(before, (summary_path.read_bytes(), manifest_path.read_bytes()))

    def test_resume_repairs_retained_data_and_skips_report_selection(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_asset_facts(root)
            (root / "top_materials.json").write_text(
                json.dumps({"materials": [{"materialId": "m-1"}, {"materialId": "m-2"}]}),
                encoding="utf-8",
            )
            standard_workflow.write_run_summary(
                root / "run-summary.json",
                {
                    "schemaVersion": 2,
                    "status": "running",
                    "activeStage": "diagnosis_batch",
                    "request": self.request_payload(),
                    "artifacts": {},
                    "failure": None,
                },
            )

            def repair_batch(peak_dir, **kwargs):
                calls.append(("repair", peak_dir, kwargs))
                return {"valid": True, "rpaCalls": 0}

            def extract_main(argv):
                calls.append(("verification" if "--verify" in argv else "resume", argv))
                if "--verify" in argv:
                    self.write_complete_ready_data(root)
                return 0

            def prepare_facts(**kwargs):
                calls.append(("facts", kwargs))
                return root / "diagnostic-analysis" / "diagnostic-analysis-prompt.md", {}

            summary = standard_workflow.resume_standard_run(
                self.request(),
                root,
                extract_main=extract_main,
                repair_batch=repair_batch,
                prepare_facts=prepare_facts,
            )

        self.assertEqual(summary["status"], "analysis_pending")
        self.assertEqual([item[0] for item in calls], ["repair", "resume", "verification", "facts"])
        self.assertIn("--resume", calls[1][1])
        self.assertNotIn("--resume", calls[2][1])
        self.assertEqual(calls[0][2]["diagnosis_records_dir"], root / "diagnosis-records")

    def test_resume_request_mismatch_preserves_existing_run_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "run-summary.json"
            original = {
                "schemaVersion": 2,
                "status": "running",
                "activeStage": "diagnosis_batch",
                "request": self.request_payload(),
                "artifacts": {"topMaterials": "top_materials.json"},
                "failure": None,
            }
            standard_workflow.write_run_summary(summary_path, original)
            mismatched = standard_workflow.WorkflowRequest(
                start_date="2026-07-19",
                end_date="2026-07-25",
                top_n=2,
                advertiser_id="1770096415226951",
                shop_id="different-shop",
            )

            with self.assertRaisesRegex(
                standard_workflow.StandardWorkflowError,
                'shopId: saved="shop-1", current="different-shop"',
            ):
                standard_workflow.resume_standard_run(mismatched, root)

            persisted = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, original)

    def test_prepare_local_request_mismatch_reports_missing_rpa_shop_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = {
                "schemaVersion": 2,
                "status": "failed",
                "activeStage": "facts",
                "request": {**self.request_payload(), "rpaShopId": "Krgt45EZVS"},
                "artifacts": {},
                "failure": {"stage": "facts", "message": "missing dependency"},
            }
            standard_workflow.write_run_summary(root / "run-summary.json", original)

            with self.assertRaisesRegex(
                standard_workflow.StandardWorkflowError,
                'rpaShopId: saved="Krgt45EZVS", current=<missing>',
            ):
                standard_workflow.prepare_local_run(self.request(), root)

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
        self.assertIsNone(summary["artifacts"]["analysisUnitManifest"])
        self.assertTrue(archive_exists)

    def test_validate_analysis_checks_existing_artifacts_without_changing_run_state(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            analysis_json = root / "diagnostic-ai-analysis.json"
            analysis_json.write_text('{"schemaVersion": 5}', encoding="utf-8")
            original_summary = {
                "schemaVersion": 2,
                "status": "analysis_pending",
                "request": self.request_payload(),
                "artifacts": {},
                "failure": None,
            }
            standard_workflow.write_run_summary(root / "run-summary.json", original_summary)

            result = standard_workflow.validate_analysis(
                root,
                analysis_json,
                validate=lambda **kwargs: calls.append(kwargs) or {
                    "analysisSchemaVersion": 5,
                    "materialCount": 2,
                    "highlightFrameCount": 12,
                },
            )
            persisted = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(result["analysisStatus"], "valid")
        self.assertEqual(result["analysisSchemaVersion"], 5)
        self.assertEqual(result["materialCount"], 2)
        self.assertEqual(result["highlightFrameCount"], 12)
        self.assertEqual(persisted, original_summary)
        self.assertEqual(calls[0]["analysis_json"], analysis_json)
        self.assertEqual(
            calls[0]["top_materials_json"],
            root / "peak-frames" / "top_materials_rpa_ready.json",
        )

    def test_validate_analysis_keeps_run_retryable_after_quality_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            analysis_json = root / "diagnostic-ai-analysis.json"
            analysis_json.write_text('{"schemaVersion": 5}', encoding="utf-8")
            original_summary = {
                "schemaVersion": 2,
                "status": "analysis_pending",
                "request": self.request_payload(),
                "artifacts": {},
                "failure": None,
            }
            standard_workflow.write_run_summary(root / "run-summary.json", original_summary)

            with self.assertRaisesRegex(
                standard_workflow.StandardWorkflowError, "overallConclusion"
            ) as error:
                standard_workflow.validate_analysis(
                    root,
                    analysis_json,
                    validate=lambda **_kwargs: (_ for _ in ()).throw(
                        ValueError("overallConclusion must contain at least 100 characters")
                    ),
                )
            persisted = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(error.exception.stage, "analysis")
        self.assertEqual(persisted, original_summary)

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
        self.assertTrue(render_calls[0]["require_local_video_assets"])

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
    def test_dependency_bootstrap_runs_before_workflow_import(self):
        events = []

        class FakeWorkflow:
            def standard_run_status(self, _output_dir):
                events.append("workflow_call")
                return {"workflowStatus": "not_started"}

        with patch.object(
            run_standard_analysis,
            "ensure_prepare_dependencies",
            side_effect=lambda: events.append("dependencies"),
        ), patch.object(
            run_standard_analysis,
            "_load_workflow",
            side_effect=lambda: events.append("workflow_import") or FakeWorkflow(),
        ), redirect_stdout(io.StringIO()):
            result = run_standard_analysis.main(["status", "--output-dir", "run"])

        self.assertEqual(result, 0)
        self.assertEqual(events, ["dependencies", "workflow_import", "workflow_call"])

    def test_fetch_metadata_prints_run_artifacts_without_prepare_or_rpa(self):
        stdout = io.StringIO()
        fetch_metadata = Mock(return_value={"artifacts": {"sourceData": "source-data"}})
        fake_workflow = type("FakeWorkflow", (), {
            "WorkflowRequest": standard_workflow.WorkflowRequest,
            "StandardWorkflowError": standard_workflow.StandardWorkflowError,
            "fetch_report_metadata": fetch_metadata,
        })()
        with patch.object(run_standard_analysis, "_load_workflow", return_value=fake_workflow), redirect_stdout(stdout):
            result = run_standard_analysis.main([
                "fetch-metadata",
                "--start-date", "2026-07-19",
                "--end-date", "2026-07-25",
                "--advertiser-id", "1770096415226951",
                "--shop-id", "shop-1",
                "--output-dir", "/tmp/qianchuan-metadata",
            ])

        self.assertEqual(result, 0)
        self.assertEqual(fetch_metadata.call_args.args[0].top_n, None)
        self.assertIn("run_summary=/tmp/qianchuan-metadata/run-summary.json", stdout.getvalue())
        self.assertIn(
            "gateway_response=/tmp/qianchuan-metadata/source-data/gateway-response.json",
            stdout.getvalue(),
        )

    def test_prepare_prints_artifact_paths_without_artifact_path_helper(self):
        stdout = io.StringIO()
        prepare_standard_run = Mock(return_value={
            "artifacts": {
                "diagnosticPrompt": "diagnostic-analysis/diagnostic-analysis-prompt.md",
                "analysisUnitManifest": "diagnostic-analysis/analysis-units/analysis-unit-manifest.json",
            }
        })
        fake_workflow = type("FakeWorkflow", (), {
            "WorkflowRequest": standard_workflow.WorkflowRequest,
            "StandardWorkflowError": standard_workflow.StandardWorkflowError,
            "prepare_standard_run": prepare_standard_run,
        })()
        with patch.object(run_standard_analysis, "_load_workflow", return_value=fake_workflow), redirect_stdout(stdout):
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
        self.assertNotIn("diagnostic_analysis_data=", stdout.getvalue())
        self.assertIn(
            "diagnostic_analysis_prompt=/tmp/qianchuan-run/diagnostic-analysis/diagnostic-analysis-prompt.md",
            stdout.getvalue(),
        )
        self.assertIn(
            "analysis_unit_manifest=/tmp/qianchuan-run/diagnostic-analysis/analysis-units/analysis-unit-manifest.json",
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

    def test_resume_parser_accepts_business_inputs_as_a_distinct_command(self):
        parser = run_standard_analysis.build_parser()
        args = parser.parse_args([
            "resume",
            "--start-date", "2026-07-19",
            "--end-date", "2026-07-25",
            "--top-n", "9",
            "--advertiser-id", "1770096415226951",
            "--shop-id", "shop-1",
            "--output-dir", "run",
        ])

        self.assertEqual(args.command, "resume")
        self.assertFalse(hasattr(args, "rpa_concurrency"))

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
        finalize_standard_run = Mock(return_value={
            "artifacts": {
                "report": "final-report/final-report.html",
                "reportArchive": "final-report.zip",
            }
        })
        fake_workflow = type("FakeWorkflow", (), {
            "WorkflowRequest": standard_workflow.WorkflowRequest,
            "StandardWorkflowError": standard_workflow.StandardWorkflowError,
            "finalize_standard_run": finalize_standard_run,
        })()
        with patch.object(run_standard_analysis, "_load_workflow", return_value=fake_workflow):
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

    def test_finalize_prints_standalone_final_report_path(self):
        stdout = io.StringIO()
        finalize_standard_run = Mock(return_value={
            "artifacts": {
                "report": "final-report/final-report.html",
                "reportArchive": "final-report.zip",
            }
        })
        fake_workflow = type("FakeWorkflow", (), {
            "WorkflowRequest": standard_workflow.WorkflowRequest,
            "StandardWorkflowError": standard_workflow.StandardWorkflowError,
            "finalize_standard_run": finalize_standard_run,
        })()
        with patch.object(run_standard_analysis, "_load_workflow", return_value=fake_workflow):
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
            "final_report_html=/tmp/qianchuan-run/final-report/final-report.html",
            stdout.getvalue(),
        )

    def test_validate_analysis_prints_validation_summary(self):
        validate_analysis = Mock(return_value={
            "analysisStatus": "valid",
            "analysisSchemaVersion": 5,
            "materialCount": 2,
            "highlightFrameCount": 12,
        })
        fake_workflow = type("FakeWorkflow", (), {
            "WorkflowRequest": standard_workflow.WorkflowRequest,
            "StandardWorkflowError": standard_workflow.StandardWorkflowError,
            "validate_analysis": validate_analysis,
        })()
        with patch.object(run_standard_analysis, "_load_workflow", return_value=fake_workflow):
            with patch.object(run_standard_analysis, "ensure_prepare_dependencies"):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    result = run_standard_analysis.main(
                        [
                            "validate-analysis",
                            "--output-dir",
                            "/tmp/qianchuan-run",
                            "--analysis-json",
                            "/tmp/qianchuan-run/diagnostic-ai-analysis.json",
                        ]
                    )

        self.assertEqual(result, 0)
        self.assertIn('"analysisStatus": "valid"', stdout.getvalue())
        validate_analysis.assert_called_once_with(
            "/tmp/qianchuan-run",
            "/tmp/qianchuan-run/diagnostic-ai-analysis.json",
        )


if __name__ == "__main__":
    unittest.main()
