from pathlib import Path
import tempfile
import sys
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_url_video_input  # noqa: E402


class VerifyUrlVideoInputTests(unittest.TestCase):
    URL = "https://video.example/material.mp4?token=do-not-log&sig=secret-value"

    def test_parser_defaults_to_all_materials_and_requires_explicit_run_output(self):
        parser = verify_url_video_input.build_parser()

        args = parser.parse_args(["--run-dir", "retained", "--output-dir", "out"])

        self.assertEqual(args.run_dir, "retained")
        self.assertEqual(args.output_dir, "out")
        self.assertIsNone(args.material_id)

    def test_safe_url_details_records_capability_metrics_without_signed_query(self):
        capabilities = SimpleNamespace(
            url_host="video.example",
            status_code=206,
            content_length=123456,
            accept_ranges=True,
            supports_range=True,
            moov_offset=321,
            moov_position="head",
            probe_method="HEAD+GET",
            bytes_read=4096,
            elapsed_sec=0.125,
        )

        details = verify_url_video_input._safe_url_details(self.URL, capabilities)

        self.assertEqual(details["urlHost"], "video.example")
        self.assertEqual(details["contentLength"], 123456)
        self.assertTrue(details["supportsRange"])
        self.assertEqual(details["moovPosition"], "head")
        self.assertEqual(details["bytesRead"], 4096)
        self.assertNotIn("do-not-log", str(details))
        self.assertNotIn("secret-value", str(details))
        self.assertNotIn("videoUrl", details)

    def test_frame_comparison_checks_count_timestamps_and_filenames(self):
        retained = [
            {
                "rankType": "zero_second",
                "requestedTimestampSec": 0.0,
                "framePath": "zero_second_0000s_10.jpg",
            },
            {
                "rankType": "peak_top_1",
                "requestedTimestampSec": 2.0,
                "framePath": "peak_top_1_0002s_8.jpg",
            },
        ]

        result = verify_url_video_input._compare_frame_metadata(retained, retained)

        self.assertEqual(
            result,
            {
                "frameCount": 2,
                "timestampsMatch": True,
                "filenamesMatch": True,
            },
        )

    def test_cleanup_scan_finds_mp4_and_hidden_staging_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "material.mp4").touch()
            (root / ".frame.123.jpg").touch()
            (root / ".summary.123.json.tmp").touch()
            (root / "frame.jpg").touch()

            leftovers = verify_url_video_input._find_cleanup_leftovers(root)

        self.assertEqual(
            {path.name for path in leftovers},
            {"material.mp4", ".frame.123.jpg", ".summary.123.json.tmp"},
        )


if __name__ == "__main__":
    unittest.main()
