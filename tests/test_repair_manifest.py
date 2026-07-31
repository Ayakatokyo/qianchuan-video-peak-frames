import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import repair_manifest  # noqa: E402


def write_manifest(root, entry):
    peak = root / "peak-frames"
    peak.mkdir()
    (peak / "batch-manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "source": "rpa",
                "runId": "run-1",
                "targetTopN": 1,
                "candidateCount": 1,
                "candidateLimit": 1,
                "materialIds": [entry["materialId"]],
                "materials": [entry],
                "submittedMaterials": [entry],
                "selectedMaterials": [entry],
                "rejectedMaterials": [],
                "cancelledMaterials": [],
            }
        ),
        encoding="utf-8",
    )
    return peak


class RepairManifestTests(unittest.TestCase):
    def test_repairs_csv_to_records_without_rpa_and_recomputes_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_id = "m-1"
            entry = {
                "materialId": material_id,
                "rank": 1,
                "rpaStatus": "selected",
                "postprocessStatus": "succeeded",
                "status": "succeeded",
                "diagnosisRecordsPath": None,
                "diagnosisCsvPath": "diagnosis-csv/m-1.csv",
                "summaryJson": "peak-frames/m-1/summary.json",
                "summaryMd": "peak-frames/m-1/summary.md",
            }
            peak = write_manifest(root, entry)
            csv_dir = root / "diagnosis-csv"
            csv_dir.mkdir()
            (csv_dir / "m-1.csv").write_text(
                "recordType\x01materialId\x01videoUrl\n"
                "material_info\x01m-1\x01https://video.example/m-1.mp4\n",
                encoding="utf-8",
            )
            report_dir = peak / material_id
            report_dir.mkdir()
            (report_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "materialId": material_id,
                        "frames": [{"framePath": "peak.jpg"}],
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            (report_dir / "summary.md").write_text("summary\n", encoding="utf-8")
            (report_dir / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")

            result = repair_manifest.repair_batch(peak)
            manifest = json.loads(
                (peak / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["rpaCalls"], 0)
        self.assertEqual(result["actions"][0]["action"], "csv_to_records")
        self.assertEqual(manifest["stageCounts"]["recordsPersistedCount"], 1)
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 1)
        self.assertTrue(manifest["materials"][0]["diagnosisRecordsPath"])

    def test_repairs_existing_records_by_running_only_local_postprocess(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = {
                "materialId": "m-1",
                "rank": 1,
                "rpaStatus": "selected",
                "postprocessStatus": "queued",
                "status": "running",
                "diagnosisRecordsPath": "diagnosis-records/m-1.json",
                "diagnosisCsvPath": None,
                "summaryJson": None,
                "summaryMd": None,
                "timingsSec": {},
            }
            peak = write_manifest(root, entry)
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            (records_dir / "m-1.json").write_text(
                json.dumps(
                    [
                        {
                            "recordType": "material_info",
                            "materialId": "m-1",
                            "videoUrl": "https://video.example/m-1.mp4",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            output = peak / "m-1"
            output.mkdir()
            fake_summary = output / "summary.json"
            fake_markdown = output / "summary.md"
            fake_summary.write_text("{}", encoding="utf-8")
            fake_markdown.write_text("summary\n", encoding="utf-8")

            def local_postprocess(_args, _entry):
                return {
                    "summaryJson": str(fake_summary),
                    "summaryMd": str(fake_markdown),
                }

            result = repair_manifest.repair_batch(
                peak, postprocess=local_postprocess
            )
            manifest = json.loads(
                (peak / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["rpaCalls"], 0)
        self.assertEqual(result["actions"][0]["action"], "records_to_postprocess")
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 1)

    def test_repairs_valid_summary_and_jpeg_into_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = {
                "materialId": "m-1",
                "rank": 1,
                "rpaStatus": "selected",
                "postprocessStatus": "failed",
                "status": "failed",
                "diagnosisRecordsPath": None,
                "diagnosisCsvPath": None,
                "summaryJson": "peak-frames/m-1/summary.json",
                "summaryMd": "peak-frames/m-1/summary.md",
            }
            peak = write_manifest(root, entry)
            output = peak / "m-1"
            output.mkdir()
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "materialId": "m-1",
                        "frames": [{"framePath": "peak.jpg"}],
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            (output / "summary.md").write_text("summary\n", encoding="utf-8")
            (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")

            result = repair_manifest.repair_batch(peak)
            manifest = json.loads(
                (peak / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["rpaCalls"], 0)
        self.assertEqual(result["actions"][0]["action"], "summary_to_manifest")
        self.assertEqual(manifest["stageCounts"]["rpaSelectedCount"], 1)
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 1)

    def test_rebuilds_missing_manifest_from_top_materials_and_retained_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            peak = root / "peak-frames"
            peak.mkdir()
            (root / "top_materials.json").write_text(
                json.dumps(
                    {
                        "actualStartDate": "2026-06-01",
                        "actualEndDate": "2026-06-07",
                        "materials": [{"materialId": "m-1", "rank": 1}],
                    }
                ),
                encoding="utf-8",
            )
            output = peak / "m-1"
            output.mkdir()
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "materialId": "m-1",
                        "frames": [{"framePath": "peak.jpg"}],
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            (output / "summary.md").write_text("summary\n", encoding="utf-8")
            (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")

            result = repair_manifest.repair_batch(
                peak, top_materials_json=root / "top_materials.json"
            )
            manifest = json.loads(
                (peak / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["rpaCalls"], 0)
        self.assertEqual(manifest["materials"][0]["rpaStatus"], "unknown")
        self.assertEqual(manifest["materials"][0]["postprocessStatus"], "succeeded")
        self.assertEqual(manifest["stageCounts"]["rpaSelectedCount"], 0)
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 1)


if __name__ == "__main__":
    unittest.main()
