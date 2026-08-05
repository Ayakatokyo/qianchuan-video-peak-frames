import json
from pathlib import Path
import tempfile
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from batch_runtime import mark_report_ready, stage_counts  # noqa: E402
from report_renderer import render_html_report  # noqa: E402


class StageCountTests(unittest.TestCase):
    def test_stage_counts_separates_accepted_records_from_postprocess(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records_path = root / "m-1.json"
            records_path.write_text(json.dumps([{"materialId": "m-1"}]), encoding="utf-8")
            counts = stage_counts([
                {
                    "materialId": "m-1",
                    "rpaStatus": "selected",
                    "rpaCsvVerified": True,
                    "recordsPersisted": True,
                    "diagnosisRecordsPath": str(records_path),
                    "postprocessStatus": "running",
                },
                {
                    "materialId": "m-2",
                    "rpaStatus": "submitted",
                    "recordsPersisted": False,
                    "postprocessStatus": "pending",
                },
            ])

        self.assertEqual(counts["rpaRequestCount"], 2)
        self.assertEqual(counts["rpaAcceptedCount"], 1)
        self.assertEqual(counts["postprocessSucceededCount"], 0)

    def test_records_persisted_count_requires_valid_matching_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "valid.json"
            valid.write_text(
                json.dumps([{"recordType": "material_info", "materialId": "m-1"}]),
                encoding="utf-8",
            )
            invalid = root / "invalid.json"
            invalid.write_text("not-json", encoding="utf-8")
            counts = stage_counts(
                [
                    {
                        "materialId": "m-1",
                        "diagnosisRecordsPath": str(valid),
                        "recordsPersisted": True,
                    },
                    {
                        "materialId": "m-2",
                        "diagnosisRecordsPath": str(invalid),
                        "recordsPersisted": True,
                    },
                    {
                        "materialId": "m-3",
                        "diagnosisRecordsPath": str(root / "missing.json"),
                    },
                ]
            )
        self.assertEqual(counts["recordsPersistedCount"], 1)
        self.assertEqual(counts["recordsPersistedUnknownCount"], 2)

    def test_legacy_entry_without_boolean_is_explicitly_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = root / "m-1.json"
            records.write_text(
                json.dumps([{"recordType": "material_info", "materialId": "m-1"}]),
                encoding="utf-8",
            )
            counts = stage_counts(
                [{"materialId": "m-1", "diagnosisRecordsPath": str(records)}]
            )
        self.assertEqual(counts["recordsPersistedCount"], 0)
        self.assertEqual(counts["recordsPersistedUnknownCount"], 1)


class ReportReadyTests(unittest.TestCase):
    def _write_report(self, root, *, run_id="run-1", html=None, data=None):
        manifest = root / "batch-manifest.json"
        report_data = root / "final-report-data.json"
        report_html = root / "final-report.html"
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "runId": run_id,
                    "selectedMaterials": [
                        {
                            "materialId": "m-1",
                            "rpaStatus": "selected",
                            "postprocessStatus": "succeeded",
                        }
                    ],
                    "stageCounts": {"reportReadyCount": 0},
                }
            ),
            encoding="utf-8",
        )
        data = data or {
            "schemaVersion": 1,
            "status": "complete",
            "runId": run_id,
            "materials": [{"materialId": "m-1", "highlightFrames": []}],
            "pipelineSummary": {"runId": run_id},
        }
        report_data.write_text(json.dumps(data), encoding="utf-8")
        html = html or (
            '<!doctype html><html><body>'
            '<script type="application/json" id="report-data">'
            + json.dumps(data)
            + "</script></body></html>"
        )
        report_html.write_text(html, encoding="utf-8")
        return manifest, report_data, report_html

    def test_report_ready_requires_matching_rendered_report_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest, data, html = self._write_report(Path(temp_dir))
            report_data = json.loads(data.read_text(encoding="utf-8"))
            html.write_text(render_html_report(report_data), encoding="utf-8")
            self.assertTrue(mark_report_ready(manifest, data, html))
            updated = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(updated["reportReady"]["runId"], "run-1")

    def test_report_ready_rejects_placeholder_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest, data, html = self._write_report(
                Path(temp_dir), html="<html>__REPORT_DATA_JSON__</html>"
            )
            self.assertFalse(mark_report_ready(manifest, data, html))

    def test_report_ready_rejects_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest, data, html = self._write_report(
                Path(temp_dir), data={"schemaVersion": 1, "status": "complete", "runId": "other", "materials": [], "pipelineSummary": {"runId": "other"}}
            )
            self.assertFalse(mark_report_ready(manifest, data, html))

    def test_report_ready_rejects_missing_copied_frame_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = {
                "schemaVersion": 1,
                "status": "complete",
                "runId": "run-1",
                "materials": [
                    {
                        "materialId": "m-1",
                        "highlightFrames": [{"framePath": "assets/missing.jpg"}],
                    }
                ],
                "pipelineSummary": {"runId": "run-1"},
            }
            manifest, report_data, html = self._write_report(root, data=data)
            html.write_text(
                '<script type="application/json" id="report-data">'
                + json.dumps(data)
                + "</script>",
                encoding="utf-8",
            )
            self.assertFalse(mark_report_ready(manifest, report_data, html))


if __name__ == "__main__":
    unittest.main()
