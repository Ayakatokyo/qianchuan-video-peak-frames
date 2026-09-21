import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import repair_manifest  # noqa: E402
from rpa_artifact_receipts import publish_receipt  # noqa: E402


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
    def test_repair_defaults_match_staged_postprocess_limits(self):
        args = repair_manifest.build_parser().parse_args(["--output-dir", "/tmp/run"])

        self.assertEqual(args.download_concurrency, 2)
        self.assertEqual(args.postprocess_concurrency, 2)
        self.assertEqual(args.max_materials_per_run, 2)

    def test_recovers_stale_manifest_from_records_persisted_receipt_without_rpa(self):
        """A durable RPA receipt must outrank an interrupted manifest checkpoint."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_id = "m-receipt"
            entry = {
                "materialId": material_id,
                "rank": 1,
                "rpaStatus": "submitted",
                "rpaResultStatus": None,
                "recordsPersisted": False,
                "postprocessStatus": "pending",
                "status": "running",
                "diagnosisRecordsPath": None,
                "diagnosisCsvPath": None,
                "summaryJson": None,
                "summaryMd": None,
                "timingsSec": {},
            }
            peak = write_manifest(root, entry)
            retained = root / "retained-rpa-artifacts"
            retained.mkdir()
            csv_path = retained / f"{material_id}.csv"
            records_path = retained / f"{material_id}.json"
            csv_path.write_text(
                "recordType\x01materialId\x01videoUrl\n"
                f"material_info\x01{material_id}\x01https://video.example/{material_id}.mp4\n",
                encoding="utf-8",
            )
            records_path.write_text(
                json.dumps([{
                    "recordType": "material_info",
                    "materialId": material_id,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                }]),
                encoding="utf-8",
            )
            publish_receipt(
                root / "rpa-receipts",
                material_id=material_id,
                client_attempt_id="attempt-1",
                state="records_persisted",
                csv_path=csv_path,
                records_path=records_path,
            )
            output = peak / material_id
            output.mkdir()
            summary_json = output / "summary.json"
            summary_md = output / "summary.md"

            def local_postprocess(_args, _entry):
                summary_json.write_text(
                    json.dumps({
                        "materialId": material_id,
                        "frames": [{"framePath": "peak.jpg"}],
                        "warnings": [],
                    }),
                    encoding="utf-8",
                )
                summary_md.write_text("summary\n", encoding="utf-8")
                (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")
                return {"summaryJson": str(summary_json), "summaryMd": str(summary_md)}

            result = repair_manifest.repair_batch(peak, postprocess=local_postprocess)
            manifest = json.loads((peak / "batch-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result["rpaCalls"], 0)
        self.assertIn(
            {"materialId": material_id, "action": "receipt_to_records"},
            result["actions"],
        )
        self.assertEqual(manifest["materials"][0]["rpaStatus"], "selected")
        self.assertEqual(manifest["materials"][0]["rpaResultStatus"], "available")
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 1)
        self.assertNotIn("records", manifest["materials"][0])
        self.assertNotIn("records", manifest["selectedMaterials"][0])

    def test_default_postprocess_uses_current_wave_postprocess_contract(self):
        result_dir = Path("/tmp/peak-frames/m-1")
        summary_json = result_dir / "summary.json"
        summary_md = result_dir / "summary.md"
        args = object()
        entry = {"materialId": "m-1"}
        with patch.object(
            repair_manifest.pipeline,
            "_attempt_wave_postprocess",
            return_value={
                "result": (result_dir, summary_json, summary_md),
                "error": None,
            },
        ) as postprocess:
            result = repair_manifest._default_postprocess(args, entry)

        postprocess.assert_called_once_with(args, entry)
        self.assertEqual(result["summaryJson"], str(summary_json))
        self.assertEqual(result["summaryMd"], str(summary_md))

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

    def test_default_repair_queues_multiple_local_postprocess_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            peak = root / "peak-frames"
            peak.mkdir()
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            entries = []
            for index in range(4):
                material_id = f"m-{index}"
                entries.append(
                    {
                        "materialId": material_id,
                        "rank": index + 1,
                        "rpaStatus": "selected",
                        "postprocessStatus": "queued",
                        "status": "running",
                        "diagnosisRecordsPath": f"diagnosis-records/{material_id}.json",
                        "summaryJson": None,
                        "summaryMd": None,
                        "timingsSec": {},
                    }
                )
                (records_dir / f"{material_id}.json").write_text(
                    json.dumps(
                        [{
                            "recordType": "material_info",
                            "materialId": material_id,
                            "videoUrl": f"https://video.example/{material_id}.mp4",
                        }]
                    ),
                    encoding="utf-8",
                )
            (peak / "batch-manifest.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "source": "rpa",
                        "runId": "run-1",
                        "targetTopN": 4,
                        "candidateCount": 4,
                        "candidateLimit": 4,
                        "materialIds": [entry["materialId"] for entry in entries],
                        "materials": entries,
                        "submittedMaterials": entries,
                        "selectedMaterials": entries,
                        "rejectedMaterials": [],
                        "cancelledMaterials": [],
                    }
                ),
                encoding="utf-8",
            )
            lock = threading.Lock()
            active = 0
            max_active = 0

            def local_attempt(_args, entry):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.03)
                material_id = entry["materialId"]
                output = peak / material_id
                output.mkdir()
                summary_json = output / "summary.json"
                summary_md = output / "summary.md"
                summary_json.write_text(
                    json.dumps(
                        {
                            "materialId": material_id,
                            "frames": [{"framePath": "peak.jpg"}],
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                summary_md.write_text("summary\n", encoding="utf-8")
                (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")
                with lock:
                    active -= 1
                return {
                    "result": (output, summary_json, summary_md),
                    "error": None,
                }

            with patch.object(
                repair_manifest.pipeline,
                "_attempt_wave_postprocess",
                side_effect=local_attempt,
            ):
                result = repair_manifest.repair_batch(
                    peak, max_materials_per_run=4
                )

        self.assertTrue(result["valid"])
        self.assertGreater(max_active, 1)
        self.assertEqual(result["stageCounts"]["postprocessSucceededCount"], 4)

    def test_default_repair_processes_a_bounded_chunk_and_reports_remaining_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            peak = root / "peak-frames"
            peak.mkdir()
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            entries = []
            for index in range(4):
                material_id = f"m-{index}"
                entry = {
                    "materialId": material_id,
                    "rank": index + 1,
                    "rpaStatus": "selected",
                    "postprocessStatus": "queued",
                    "status": "running",
                    "diagnosisRecordsPath": f"diagnosis-records/{material_id}.json",
                    "summaryJson": None,
                    "summaryMd": None,
                    "timingsSec": {},
                }
                entries.append(entry)
                (records_dir / f"{material_id}.json").write_text(
                    json.dumps([{
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://video.example/{material_id}.mp4",
                    }]),
                    encoding="utf-8",
                )
            (peak / "batch-manifest.json").write_text(
                json.dumps({
                    "schemaVersion": 2,
                    "source": "rpa",
                    "runId": "run-1",
                    "targetTopN": 4,
                    "candidateCount": 4,
                    "candidateLimit": 4,
                    "materialIds": [entry["materialId"] for entry in entries],
                    "materials": entries,
                    "submittedMaterials": entries,
                    "selectedMaterials": entries,
                    "rejectedMaterials": [],
                    "cancelledMaterials": [],
                }),
                encoding="utf-8",
            )

            def local_attempt(_args, entry):
                material_id = entry["materialId"]
                output = peak / material_id
                output.mkdir()
                summary_json = output / "summary.json"
                summary_md = output / "summary.md"
                summary_json.write_text(json.dumps({
                    "materialId": material_id,
                    "frames": [{"framePath": "peak.jpg"}],
                    "warnings": [],
                }), encoding="utf-8")
                summary_md.write_text("summary\n", encoding="utf-8")
                (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")
                return {
                    "result": (output, summary_json, summary_md),
                    "error": None,
                    "timingsSec": {"totalSec": 1.5},
                    "videoPath": str(peak / "video-assets" / f"{material_id}.mp4"),
                    "videoBytes": 10,
                    "videoSha256": "a" * 64,
                }

            with patch.object(
                repair_manifest.pipeline,
                "_attempt_wave_postprocess",
                side_effect=local_attempt,
            ) as postprocess:
                result = repair_manifest.repair_batch(peak)

            manifest = json.loads((peak / "batch-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(postprocess.call_count, 2)
        self.assertEqual(result["postprocessAttemptedCount"], 2)
        self.assertEqual(result["postprocessRemainingCount"], 2)
        self.assertFalse(result["postprocessBatchComplete"])
        self.assertEqual(manifest["stageCounts"]["postprocessSucceededCount"], 2)
        self.assertEqual(
            [entry["status"] for entry in manifest["materials"][2:]],
            ["pending", "pending"],
        )

    def test_download_checkpoint_survives_frame_extraction_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_id = "m-1"
            entry = {
                "materialId": material_id,
                "rank": 1,
                "rpaStatus": "selected",
                "postprocessStatus": "queued",
                "status": "running",
                "diagnosisRecordsPath": "diagnosis-records/m-1.json",
                "summaryJson": None,
                "summaryMd": None,
                "timingsSec": {},
            }
            peak = write_manifest(root, entry)
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            (records_dir / "m-1.json").write_text(
                json.dumps([{
                    "recordType": "material_info",
                    "materialId": material_id,
                    "videoUrl": "https://video.example/m-1.mp4",
                }]),
                encoding="utf-8",
            )

            def interrupted_attempt(args, current):
                args._postprocess_checkpoint(current["materialId"], {
                    "postprocessStatus": "running",
                    "postprocessSubstage": "frame_extraction",
                    "videoPath": str(peak / "video-assets" / "m-1.mp4"),
                    "videoBytes": 123,
                    "videoSha256": "b" * 64,
                    "timingsSec": {"videoDownloadSec": 2.0},
                })
                return {"result": None, "error": RuntimeError("frame extraction stopped")}

            with patch.object(
                repair_manifest.pipeline,
                "_attempt_wave_postprocess",
                side_effect=interrupted_attempt,
            ):
                result = repair_manifest.repair_batch(peak)

            persisted = json.loads((peak / "batch-manifest.json").read_text(encoding="utf-8"))
            repaired = persisted["materials"][0]

        self.assertFalse(result["valid"])
        self.assertEqual(repaired["videoBytes"], 123)
        self.assertEqual(repaired["videoSha256"], "b" * 64)
        self.assertEqual(repaired["postprocessSubstage"], "frame_extraction")
        self.assertEqual(repaired["timingsSec"]["videoDownloadSec"], 2.0)

    def test_failed_materials_do_not_starve_unattempted_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            peak = root / "peak-frames"
            peak.mkdir()
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            entries = []
            for index in range(4):
                material_id = f"m-{index}"
                entries.append({
                    "materialId": material_id,
                    "rank": index + 1,
                    "rpaStatus": "selected",
                    "postprocessStatus": "failed" if index < 2 else "queued",
                    "postprocessAttemptCount": 1 if index < 2 else 0,
                    "postprocessAttempts": [],
                    "status": "failed" if index < 2 else "pending",
                    "diagnosisRecordsPath": f"diagnosis-records/{material_id}.json",
                    "summaryJson": None,
                    "summaryMd": None,
                    "timingsSec": {},
                })
                (records_dir / f"{material_id}.json").write_text(
                    json.dumps([{
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://video.example/{material_id}.mp4",
                    }]),
                    encoding="utf-8",
                )
            (peak / "batch-manifest.json").write_text(
                json.dumps({
                    "schemaVersion": 2,
                    "source": "rpa",
                    "runId": "run-1",
                    "targetTopN": 4,
                    "candidateCount": 4,
                    "candidateLimit": 4,
                    "materialIds": [entry["materialId"] for entry in entries],
                    "materials": entries,
                    "submittedMaterials": entries,
                    "selectedMaterials": entries,
                    "rejectedMaterials": [],
                    "cancelledMaterials": [],
                }),
                encoding="utf-8",
            )
            attempted = []

            def local_attempt(_args, entry):
                material_id = entry["materialId"]
                attempted.append(material_id)
                output = peak / material_id
                output.mkdir()
                summary_json = output / "summary.json"
                summary_md = output / "summary.md"
                summary_json.write_text(json.dumps({
                    "materialId": material_id,
                    "frames": [{"framePath": "peak.jpg"}],
                    "warnings": [],
                }), encoding="utf-8")
                summary_md.write_text("summary\n", encoding="utf-8")
                (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")
                return {"result": (output, summary_json, summary_md), "error": None}

            with patch.object(
                repair_manifest.pipeline,
                "_attempt_wave_postprocess",
                side_effect=local_attempt,
            ):
                result = repair_manifest.repair_batch(peak)
            manifest = json.loads(
                (peak / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertCountEqual(attempted, ["m-2", "m-3"])
        self.assertEqual(result["postprocessRemainingCount"], 2)
        self.assertEqual(
            [entry["postprocessAttemptCount"] for entry in manifest["materials"]],
            [1, 1, 1, 1],
        )
        self.assertTrue(
            all(isinstance(entry["postprocessAttempts"], list) for entry in manifest["materials"])
        )

    def test_integer_postprocess_attempts_are_migrated_to_count_and_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material_id = "m-legacy"
            entry = {
                "materialId": material_id,
                "rank": 1,
                "rpaStatus": "selected",
                "postprocessStatus": "queued",
                "postprocessAttempts": 3,
                "status": "pending",
                "diagnosisRecordsPath": f"diagnosis-records/{material_id}.json",
                "summaryJson": None,
                "summaryMd": None,
                "timingsSec": {},
            }
            peak = write_manifest(root, entry)
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            (records_dir / f"{material_id}.json").write_text(
                json.dumps([{
                    "recordType": "material_info",
                    "materialId": material_id,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                }]),
                encoding="utf-8",
            )

            def local_attempt(_args, current):
                output = peak / material_id
                output.mkdir()
                summary_json = output / "summary.json"
                summary_md = output / "summary.md"
                summary_json.write_text(json.dumps({
                    "materialId": material_id,
                    "frames": [{"framePath": "peak.jpg"}],
                    "warnings": [],
                }), encoding="utf-8")
                summary_md.write_text("summary\n", encoding="utf-8")
                (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")
                return {"result": (output, summary_json, summary_md), "error": None}

            with patch.object(
                repair_manifest.pipeline,
                "_attempt_wave_postprocess",
                side_effect=local_attempt,
            ):
                repair_manifest.repair_batch(peak)
            repaired = json.loads(
                (peak / "batch-manifest.json").read_text(encoding="utf-8")
            )["materials"][0]

        self.assertEqual(repaired["postprocessAttemptCount"], 4)
        self.assertIsInstance(repaired["postprocessAttempts"], list)
        self.assertEqual(len(repaired["postprocessAttempts"]), 1)
        self.assertEqual(repaired["postprocessAttempts"][0]["status"], "succeeded")

    def test_repairs_records_when_manifest_status_claims_success_but_summary_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = {
                "materialId": "m-1",
                "rank": 1,
                "rpaStatus": "selected",
                "postprocessStatus": "succeeded",
                "postprocessSucceeded": True,
                "status": "succeeded",
                "diagnosisRecordsPath": "diagnosis-records/m-1.json",
                "summaryJson": None,
                "summaryMd": None,
            }
            peak = write_manifest(root, entry)
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            (records_dir / "m-1.json").write_text(
                json.dumps([{
                    "recordType": "material_info",
                    "materialId": "m-1",
                    "videoUrl": "https://video.example/m-1.mp4",
                }]),
                encoding="utf-8",
            )
            output = peak / "m-1"
            output.mkdir()
            fake_summary = output / "summary.json"
            fake_markdown = output / "summary.md"

            def local_postprocess(_args, _entry):
                fake_summary.write_text(
                    json.dumps({
                        "materialId": "m-1",
                        "frames": [{"framePath": "peak.jpg"}],
                        "warnings": [],
                    }),
                    encoding="utf-8",
                )
                fake_markdown.write_text("summary\n", encoding="utf-8")
                (output / "peak.jpg").write_bytes(b"\xff\xd8frame\xff\xd9")
                return {"summaryJson": str(fake_summary), "summaryMd": str(fake_markdown)}

            result = repair_manifest.repair_batch(peak, postprocess=local_postprocess)
            manifest = json.loads((peak / "batch-manifest.json").read_text(encoding="utf-8"))

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
