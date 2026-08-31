import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from highlight_export import (  # noqa: E402
    HighlightExportError,
    build_highlight_export_rows,
    export_highlight_workbook,
)


class HighlightExportTests(unittest.TestCase):
    @staticmethod
    def _write_image(path, size=(92, 112)):
        Image.new("RGB", size, color=(210, 170, 120)).save(path, format="JPEG")

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

    def test_rows_follow_report_presentation_frame_facts_and_category_refs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_cover = root / "legacy-cover.jpg"
            canonical_peak = root / "canonical-peak.jpg"
            legacy_cover.write_bytes(b"legacy")
            canonical_peak.write_bytes(b"canonical")
            data = self._data()
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(legacy_cover)
            presentation = {
                "materials": [{"materialId": "m-1", "materialName": "目录标题", "rank": 7}],
                "chapters": {
                    "chapter04": {
                        "groups": {
                            "portrait": {"frameRefs": ["ref:m-1:peak"]},
                            "product": {"frameRefs": []},
                            "other": {"frameRefs": []},
                        }
                    },
                    "chapter05": {
                        "materials": [{
                            "materialId": "m-1",
                            "highlight": {"frames": [{
                                "frameRef": "ref:m-1:peak",
                                "rankType": "peak_top_1",
                                "framePath": str(canonical_peak),
                                "durationSec": 8,
                                "metricValue": 99,
                            }]},
                        }]
                    },
                },
            }

            rows = build_highlight_export_rows(data, presentation)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["视觉类型"], "人像画面")
        self.assertEqual(rows[0]["素材排名"], 7)
        self.assertEqual(rows[0]["秒数"], 8)
        self.assertEqual(rows[0]["点击指数"], 99)
        self.assertEqual(rows[0]["__image_path"], str(canonical_peak.resolve()))

    def test_rows_keep_all_chapter04_categories_for_shared_workbook_consumers(self):
        data = {
            "materials": [{
                "rank": 1,
                "materialId": "m-1",
                "materialName": "素材标题",
                "highlightFrames": [
                    {"frameRef": "ref:portrait", "rankType": "peak_top_1", "framePath": "p.jpg"},
                    {"frameRef": "ref:product", "rankType": "peak_top_2", "framePath": "q.jpg"},
                    {"frameRef": "ref:other", "rankType": "peak_top_3", "framePath": "o.jpg"},
                ],
            }]
        }
        presentation = {
            "chapters": {
                "chapter04": {
                    "groups": {
                        "portrait": {"frameRefs": ["ref:portrait"]},
                        "product": {"frameRefs": ["ref:product"]},
                        "other": {"frameRefs": ["ref:other"]},
                    }
                }
            }
        }
        rows = build_highlight_export_rows(data, presentation)
        counts = {label: sum(row["视觉类型"] == label for row in rows) for label in ("人像画面", "产品画面", "其他画面")}
        self.assertEqual(counts, {"人像画面": 1, "产品画面": 1, "其他画面": 1})
        self.assertEqual(len({row["__image_path"] for row in rows}), 3)

    def test_export_rejects_missing_image_instead_of_publishing_incomplete_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "highlight-frames.xlsx"
            data = self._data()
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(
                Path(temp_dir) / "missing.jpg"
            )

            with self.assertRaisesRegex(HighlightExportError, "image"):
                export_highlight_workbook(data, None, output_path)

            self.assertFalse(output_path.exists())

    def test_workbook_can_be_generated_with_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "cover.jpg"
            self._write_image(image)
            data = self._data()
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(image)
            path = export_highlight_workbook(data, {"chapters": {"chapter04": {"groups": {}}}}, Path(temp_dir) / "highlight-frames.xlsx")
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)
            with zipfile.ZipFile(path) as archive:
                self.assertTrue(any(name.startswith("xl/media/") for name in archive.namelist()))

    def test_workbook_has_asset_and_gallery_sheets_with_matching_images_and_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "cover.jpg"
            self._write_image(image)
            data = self._data()
            data["materials"][0]["highlightFrames"][1]["framePath"] = str(image)
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(image)
            presentation = {
                "chapters": {
                    "chapter04": {
                        "groups": {
                            "portrait": {"frameRefs": ["highlight:m-1:zero_second"]},
                            "product": {"frameRefs": []},
                            "other": {"frameRefs": []},
                        }
                    }
                }
            }
            path = export_highlight_workbook(data, presentation, Path(temp_dir) / "highlight-frames.xlsx")

            with zipfile.ZipFile(path) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                sheet_names = [sheet.attrib["name"] for sheet in workbook.findall(".//{*}sheet")]
                self.assertEqual(sheet_names, ["高光帧资产", "高光帧图片墙"])
                media = [name for name in archive.namelist() if name.startswith("xl/media/") and not name.endswith("/")]
                self.assertGreaterEqual(len(media), 2)
                sheet1 = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                rows = sheet1.findall(".//{*}sheetData/{*}row")
                self.assertTrue(all(row.attrib.get("customHeight") == "1" for row in rows[1:]))
                sheet2_xml = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
                sheet2_text = "".join(sheet2_xml.itertext())
                self.assertIn("全量", sheet2_text)
                self.assertIn("人像画面", sheet2_text)
                self.assertIn("产品画面", sheet2_text)
                self.assertIn("其他画面", sheet2_text)
                self.assertNotIn("素材 ID", sheet2_text)
                self.assertNotIn("点击指数", sheet2_text)
                hyperlinks = sheet2_xml.findall(".//{*}hyperlink")
                self.assertEqual(len(hyperlinks), 4)
                locations = [link.attrib.get("location") for link in hyperlinks]
                self.assertEqual(locations[0], "'高光帧图片墙'!A3")
                self.assertTrue(all(location.startswith("'高光帧图片墙'!A") for location in locations))
                self.assertFalse(any("r:id" in link.attrib for link in hyperlinks))

    def test_workbook_export_uses_python_dependencies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "cover.jpg"
            self._write_image(image)
            data = self._data()
            data["materials"][0]["highlightFrames"][0]["framePath"] = str(image)
            path = export_highlight_workbook(data, None, Path(temp_dir) / "highlight-frames.xlsx")
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
