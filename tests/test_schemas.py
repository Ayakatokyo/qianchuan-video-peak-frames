import unittest
from types import SimpleNamespace

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schemas import (  # noqa: E402
    SchemaError,
    validate_manifest,
    validate_preflight_receipt,
    validate_report_data,
)


class SchemaNormalizationTests(unittest.TestCase):
    def test_legacy_keyed_manifest_is_normalized_to_list_semantics(self):
        normalized = validate_manifest(
            {
                "schemaVersion": 1,
                "materials": {
                    "m-1": {"status": "succeeded", "summaryJson": "summary.json"}
                },
                "materialIds": ["m-1"],
            },
            material_ids=["m-1"],
        )
        self.assertEqual(normalized["schemaVersion"], 2)
        self.assertEqual(normalized["sourceSchemaVersion"], 1)
        self.assertEqual(normalized["materials"][0]["materialId"], "m-1")
        self.assertEqual(normalized["selectedMaterials"][0]["rpaStatus"], "selected")

    def test_new_v2_keyed_manifest_is_accepted_during_scheduler_migration(self):
        normalized = validate_manifest(
            {
                "schemaVersion": 2,
                "materials": {"m-1": {"status": "failed"}},
                "materialIds": ["m-1"],
            }
        )
        self.assertEqual(normalized["sourceSchemaVersion"], 2)
        self.assertEqual(normalized["materials"][0]["materialId"], "m-1")

    def test_manifest_validates_optional_wave_fields(self):
        normalized = validate_manifest(
            {
                "schemaVersion": 2,
                "waveBarrier": True,
                "rpaConcurrency": 3,
                "materials": [
                    {
                        "materialId": "m-1",
                        "waveIndex": 0,
                        "waveSize": 3,
                        "waveStartedAtSec": 0.0,
                        "waveFinishedAtSec": 12.5,
                    },
                    {
                        "materialId": "m-2",
                        "waveIndex": 1,
                        "waveSize": 2,
                        "waveStartedAtSec": 12.5,
                        "waveFinishedAtSec": 24.0,
                    },
                ],
            }
        )

        self.assertTrue(normalized["waveBarrier"])
        self.assertEqual(normalized["materials"][1]["waveIndex"], 1)

    def test_manifest_rejects_invalid_wave_field_types(self):
        with self.assertRaises(SchemaError):
            validate_manifest(
                {
                    "schemaVersion": 2,
                    "waveBarrier": "true",
                    "materials": [{"materialId": "m-1", "waveIndex": -1}],
                }
            )

    def test_manifest_accepts_lifecycle_progress_fields(self):
        manifest = validate_manifest(
            {
                "schemaVersion": 2,
                "materials": [
                    {
                        "materialId": "m-1",
                        "rpaSoftTimeoutExceeded": True,
                        "rpaHardTimeoutExceeded": False,
                        "rpaStartedAtSec": 0.0,
                        "postprocessAttemptCount": 1,
                        "lastStateChangedAtSec": 2.0,
                    }
                ],
            }
        )

        self.assertEqual(manifest["materials"][0]["postprocessAttemptCount"], 1)

    def test_manifest_rejects_invalid_lifecycle_progress_fields(self):
        with self.assertRaises(SchemaError):
            validate_manifest(
                {
                    "schemaVersion": 2,
                    "materials": [
                        {
                            "materialId": "m-1",
                            "rpaHardTimeoutExceeded": "false",
                            "postprocessAttemptCount": -1,
                        }
                    ],
                }
            )

    def test_legacy_manifest_without_wave_fields_remains_valid(self):
        normalized = validate_manifest(
            {
                "schemaVersion": 2,
                "materials": [{"materialId": "m-1", "status": "succeeded"}],
            }
        )

        self.assertNotIn("waveBarrier", normalized)
        self.assertNotIn("waveIndex", normalized["materials"][0])

    def test_manifest_accepts_postprocess_attempt_telemetry(self):
        normalized = validate_manifest(
            {
                "schemaVersion": 2,
                "materials": [
                    {
                        "materialId": "m-1",
                        "postprocessAttemptCount": 3,
                        "postprocessPhase": "serial_compensation",
                        "compensationStatus": "succeeded",
                        "postprocessAttempts": [
                            {
                                "phase": "parallel",
                                "substage": "download",
                                "errorType": "RpaAdapterError",
                                "errorMessage": "unable to download RPA video",
                                "startedAtSec": 1.0,
                                "finishedAtSec": 2.0,
                                "durationSec": 1.0,
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(
            normalized["materials"][0]["compensationStatus"], "succeeded"
        )

    def test_legacy_manifest_defaults_postprocess_compensation_fields(self):
        normalized = validate_manifest(
            {"schemaVersion": 2, "materials": [{"materialId": "m-1"}]}
        )

        self.assertEqual(
            normalized["materials"][0]["compensationStatus"], "not_needed"
        )
        self.assertEqual(normalized["materials"][0]["postprocessAttempts"], [])

    def test_preflight_receipt_rejects_invalid_wave_fields(self):
        args = SimpleNamespace(
            shop_id="shop-1",
            date_range_type="CUSTOM",
            custom_start_date="2026-07-25",
            custom_end_date="2026-07-31",
        )
        payload = {
            "schemaVersion": 2,
            "satisfied": True,
            "shopId": "shop-1",
            "waveBarrier": "true",
            "dateRange": {
                "type": "CUSTOM",
                "customStartDate": "2026-07-25",
                "customEndDate": "2026-07-31",
                "actualStartDate": "2026-07-25",
                "actualEndDate": "2026-07-31",
            },
            "selectedMaterials": [{"materialId": "m-1", "waveIndex": -1}],
        }

        with self.assertRaises(SchemaError):
            validate_preflight_receipt(
                payload,
                args=args,
                candidate_payload={
                    "actualStartDate": "2026-07-25",
                    "actualEndDate": "2026-07-31",
                },
                material_ids=["m-1"],
            )

    def test_report_schema_requires_matching_pipeline_run_id(self):
        with self.assertRaises(SchemaError):
            validate_report_data(
                {
                    "schemaVersion": 1,
                    "status": "complete",
                    "runId": "run-1",
                    "pipelineSummary": {"runId": "run-2"},
                    "materials": [{"materialId": "m-1"}],
                }
            )


if __name__ == "__main__":
    unittest.main()
