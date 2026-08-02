import unittest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schemas import SchemaError, validate_manifest, validate_report_data  # noqa: E402


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
