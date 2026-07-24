from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"


class BatchSkillContractTests(unittest.TestCase):
    def test_batch_workflow_exports_and_reuses_run_local_top_materials(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("## Batch TOP Materials Run", skill)
        batch_start = skill.index("## Batch TOP Materials Run")
        export_start = skill.index(
            "python scripts/export_top_materials.py", batch_start
        )
        rpa_start = skill.index(
            "python scripts/extract_peak_frames.py", batch_start
        )

        self.assertLess(export_start, rpa_start)
        self.assertIn('--output "/path/to/run/top_materials.json"', skill)
        self.assertIn(
            '--top-materials-json "/path/to/run/top_materials.json"', skill
        )
        self.assertIn("--custom-start-date 2026-06-01", skill)
        self.assertIn("--custom-end-date 2026-06-07", skill)

    def test_single_material_rpa_is_documented_as_debugging_only(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("## Batch TOP Materials Run", skill)
        batch_start = skill.index("## Batch TOP Materials Run")
        single_start = skill.index("## Single-Material RPA Debug Run")

        self.assertLess(batch_start, single_start)
        self.assertIn(
            "not an alternative selection method for a TOP-material batch run",
            skill,
        )

    def test_files_response_documents_retained_csv_artifact(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("diagnosis-csv/<materialId>.csv", skill)
        self.assertIn("CSV download or parsing failures remain blocking", skill)
        self.assertNotIn(
            "result_type=files` CSV-envelope response is blocking", skill
        )

    def test_batch_resume_documents_manifest_and_partial_failure(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("batch-manifest.json", skill)
        self.assertIn("--resume", skill)
        self.assertIn("later TOP materials still run", skill)


if __name__ == "__main__":
    unittest.main()
