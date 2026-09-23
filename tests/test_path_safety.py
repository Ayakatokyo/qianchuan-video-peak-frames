import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from path_safety import artifact_path, validate_material_id  # noqa: E402


class MaterialPathSafetyTests(unittest.TestCase):
    def test_accepts_normal_material_id(self):
        self.assertEqual(validate_material_id("material_123-abc"), "material_123-abc")

    def test_rejects_path_traversal_and_separators(self):
        for material_id in ("../secret", "a/b", r"a\\b", "/absolute"):
            with self.subTest(material_id=material_id):
                with self.assertRaises(ValueError):
                    validate_material_id(material_id)

    def test_artifact_path_stays_under_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(
                artifact_path(root, "m-1", ".json"),
                root.absolute() / "m-1.json",
            )

    def test_artifact_path_rejects_invalid_material_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                artifact_path(temp_dir, "../outside", ".json")


if __name__ == "__main__":
    unittest.main()
