import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from highlight_export import build_highlight_export_rows, export_highlight_workbook  # noqa: E402


class HighlightExportTests(unittest.TestCase):
    def _data(self):
        return {
            "materials": [{
                "rank": 1, "materialId": "m-1", "materialName": "素材标题",
                "highlightFrames": [
                    {"rankType": "zero_second", "durationSec": 0, "metricValue": 2, "framePath": "cover.jpg"},
                    {"rankType": "peak_top_1", "durationSec": 3, "metricValue": 9, "framePath": ""},
                ],
            }]
        }

    def test_rows_only_include_available_frames_and_map_category(self):
        presentation = {"chapters": {"chapter04": {"groups": {"portrait": {"frameRefs": ["highlight:m-1:zero_second"]}}}}}
        rows = build_highlight_export_rows(self._data(), presentation)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["视觉类型"], "人像画面")
        self.assertEqual(rows[0]["画面类型"], "封面画面")
        self.assertNotIn("frame_ref", rows[0])

    def test_rows_resolve_relative_image_paths_for_node_builder(self):
        rows = build_highlight_export_rows(self._data(), None)
        self.assertTrue(Path(rows[0]["__image_path"]).is_absolute())

    def test_workbook_can_be_generated_with_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "cover.jpg"
            image.write_bytes(b"jpeg-bytes")
            data = self._data()
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(image)
            path = export_highlight_workbook(data, {"chapters": {"chapter04": {"groups": {}}}}, Path(temp_dir) / "highlight-frames.xlsx")
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)
            with zipfile.ZipFile(path) as archive:
                self.assertTrue(any(name.startswith("xl/media/") for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
