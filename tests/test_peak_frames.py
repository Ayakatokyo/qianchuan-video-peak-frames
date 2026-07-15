from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from peak_frame_lib import (  # noqa: E402
    FfmpegError,
    InputDataError,
    SeriesPoint,
    find_local_peaks,
    parse_click_series,
    probe_video_duration,
    resolve_ffmpeg,
    select_peak_frames,
)


def click_record(second, value, material_id="m-123"):
    return {
        "recordType": "interaction_series",
        "interactionMetric": "CLICK_COUNT",
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


class PeakSelectionTests(unittest.TestCase):
    def test_selects_first_chronological_peak_then_later_top_three(self):
        values = [1, 5, 1, 9, 1, 7, 1, 8, 1]
        points = [SeriesPoint(i, value) for i, value in enumerate(values)]

        selected = select_peak_frames(find_local_peaks(points))

        self.assertEqual(
            [
                (item.rank_type, item.duration_sec, item.metric_value)
                for item in selected
            ],
            [
                ("first_peak", 1.0, 5.0),
                ("later_top_1", 3.0, 9.0),
                ("later_top_2", 7.0, 8.0),
                ("later_top_3", 5.0, 7.0),
            ],
        )

    def test_collapses_equal_plateau_to_earliest_second(self):
        points = [
            SeriesPoint(i, value)
            for i, value in enumerate([1, 4, 8, 8, 3, 1])
        ]

        peaks = find_local_peaks(points)

        self.assertEqual(
            [(peak.duration_sec, peak.metric_value) for peak in peaks],
            [(2.0, 8.0)],
        )

    def test_excludes_leading_plateau(self):
        points = [
            SeriesPoint(i, value)
            for i, value in enumerate([5, 5, 1, 0])
        ]

        self.assertEqual(find_local_peaks(points), [])

    def test_excludes_trailing_plateau(self):
        points = [
            SeriesPoint(i, value)
            for i, value in enumerate([0, 1, 5, 5])
        ]

        self.assertEqual(find_local_peaks(points), [])

    def test_constant_plateau_has_no_peaks(self):
        points = [
            SeriesPoint(i, value)
            for i, value in enumerate([5, 5, 5, 5])
        ]

        self.assertEqual(find_local_peaks(points), [])

    def test_finds_two_separated_interior_plateau_peaks(self):
        points = [
            SeriesPoint(i, value)
            for i, value in enumerate([0, 5, 5, 1, 7, 7, 0])
        ]

        peaks = find_local_peaks(points)

        self.assertEqual(
            [(peak.duration_sec, peak.metric_value) for peak in peaks],
            [(1.0, 5.0), (4.0, 7.0)],
        )

    def test_excludes_first_and_last_points(self):
        points = [
            SeriesPoint(i, value)
            for i, value in enumerate([9, 1, 2, 1, 8])
        ]

        peaks = find_local_peaks(points)

        self.assertEqual(
            [(peak.duration_sec, peak.metric_value) for peak in peaks],
            [(2.0, 2.0)],
        )

    def test_later_ties_are_ordered_by_second(self):
        values = [0, 2, 0, 6, 0, 6, 0, 6, 0]

        selected = select_peak_frames(
            find_local_peaks(
                [SeriesPoint(i, value) for i, value in enumerate(values)]
            )
        )

        self.assertEqual(
            [item.duration_sec for item in selected],
            [1.0, 3.0, 5.0, 7.0],
        )

    def test_select_rejects_no_peaks(self):
        with self.assertRaisesRegex(InputDataError, "No local peaks"):
            select_peak_frames([])

    def test_returns_only_existing_later_peaks(self):
        values = [0, 5, 0, 4, 0]

        selected = select_peak_frames(
            find_local_peaks(
                [SeriesPoint(i, value) for i, value in enumerate(values)]
            )
        )

        self.assertEqual(
            [item.rank_type for item in selected],
            ["first_peak", "later_top_1"],
        )


class ClickSeriesParsingTests(unittest.TestCase):
    def test_filters_click_records_and_collapses_identical_duplicates(self):
        records = [
            click_record(1, 5),
            click_record(1, 5),
            {**click_record(2, 99), "interactionMetric": "LIKE_COUNT"},
            click_record(3, 7),
        ]

        material_id, points = parse_click_series(records)

        self.assertEqual(material_id, "m-123")
        self.assertEqual(
            points,
            [SeriesPoint(1.0, 5.0), SeriesPoint(3.0, 7.0)],
        )

    def test_rejects_conflicting_values_for_same_second(self):
        with self.assertRaisesRegex(InputDataError, "conflicting.*1"):
            parse_click_series([click_record(1, 5), click_record(1, 6)])

    def test_rejects_missing_click_series(self):
        with self.assertRaisesRegex(InputDataError, "CLICK_COUNT"):
            parse_click_series([])

    def test_rejects_non_list_root(self):
        with self.assertRaisesRegex(InputDataError, "list"):
            parse_click_series((click_record(1, 5),))

    def test_rejects_non_dict_record(self):
        with self.assertRaisesRegex(InputDataError, "record.*dict"):
            parse_click_series([click_record(1, 5), "not-a-record"])

    def test_rejects_nonfinite_duration_and_metric_values(self):
        invalid_records = [
            click_record(float("nan"), 5),
            click_record(float("inf"), 5),
            click_record(1, float("nan")),
            click_record(1, float("-inf")),
        ]

        for record in invalid_records:
            with self.subTest(record=record):
                with self.assertRaisesRegex(InputDataError, "finite"):
                    parse_click_series([record])

    def test_rejects_boolean_duration(self):
        with self.assertRaises(InputDataError):
            parse_click_series([click_record(True, 5)])

    def test_rejects_boolean_metric_value(self):
        with self.assertRaises(InputDataError):
            parse_click_series([click_record(1, False)])

    def test_rejects_negative_duration(self):
        with self.assertRaisesRegex(InputDataError, "durationSec.*negative"):
            parse_click_series([click_record(-1, 5)])

    def test_rejects_multiple_material_ids(self):
        with self.assertRaisesRegex(InputDataError, "multiple materialId"):
            parse_click_series(
                [
                    click_record(1, 5, material_id="m-123"),
                    click_record(2, 7, material_id="m-456"),
                ]
            )

    def test_normalizes_material_id_whitespace_before_comparing(self):
        material_id, points = parse_click_series(
            [
                click_record(1, 5, material_id="m-123"),
                click_record(2, 7, material_id=" m-123 "),
            ]
        )

        self.assertEqual(material_id, "m-123")
        self.assertEqual(
            points,
            [SeriesPoint(1.0, 5.0), SeriesPoint(2.0, 7.0)],
        )

    def test_whitespace_only_material_id_returns_empty_string(self):
        material_id, points = parse_click_series(
            [click_record(1, 5, material_id="   ")]
        )

        self.assertEqual(material_id, "")
        self.assertEqual(points, [SeriesPoint(1.0, 5.0)])

    def test_missing_material_id_returns_empty_string(self):
        record = click_record(1, 5)
        del record["materialId"]

        material_id, points = parse_click_series([record])

        self.assertEqual(material_id, "")
        self.assertEqual(points, [SeriesPoint(1.0, 5.0)])


class FfmpegResolutionTests(unittest.TestCase):
    def test_explicit_path_wins_over_environment_imageio_and_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ffmpeg_path = Path(temp_dir) / "ffmpeg custom.exe"
            ffmpeg_path.touch()

            def unexpected_imageio():
                self.fail("imageio resolver must not run for an explicit path")

            def unexpected_which(_name):
                self.fail("PATH lookup must not run for an explicit path")

            resolved = resolve_ffmpeg(
                explicit=ffmpeg_path,
                environ={"IMAGEIO_FFMPEG_EXE": "/ignored/environment/ffmpeg"},
                imageio_resolver=unexpected_imageio,
                which=unexpected_which,
            )

            self.assertEqual(resolved, str(ffmpeg_path))

    def test_environment_path_wins_over_imageio_and_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ffmpeg_path = Path(temp_dir) / "environment ffmpeg.exe"
            ffmpeg_path.touch()

            def unexpected_imageio():
                self.fail("imageio resolver must not run for an environment path")

            def unexpected_which(_name):
                self.fail("PATH lookup must not run for an environment path")

            resolved = resolve_ffmpeg(
                environ={"IMAGEIO_FFMPEG_EXE": str(ffmpeg_path)},
                imageio_resolver=unexpected_imageio,
                which=unexpected_which,
            )

            self.assertEqual(resolved, str(ffmpeg_path))

    def test_imageio_path_wins_over_system_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            imageio_path = Path(temp_dir) / "imageio ffmpeg.exe"
            imageio_path.touch()

            def unexpected_which(_name):
                self.fail("PATH lookup must not run after imageio resolves")

            resolved = resolve_ffmpeg(
                environ={},
                imageio_resolver=lambda: str(imageio_path),
                which=unexpected_which,
            )

            self.assertEqual(resolved, str(imageio_path))

    def test_system_path_is_used_when_imageio_returns_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            system_path = Path(temp_dir) / "ffmpeg.exe"
            system_path.touch()

            resolved = resolve_ffmpeg(
                environ={},
                imageio_resolver=lambda: None,
                which=lambda name: str(system_path) if name == "ffmpeg" else None,
            )

            self.assertEqual(resolved, str(system_path))

    def test_missing_all_sources_has_actionable_error(self):
        with self.assertRaises(FfmpegError) as context:
            resolve_ffmpeg(
                environ={},
                imageio_resolver=lambda: None,
                which=lambda _name: None,
            )

        self.assertIn("imageio-ffmpeg", str(context.exception))
        self.assertIn("--ffmpeg-bin", str(context.exception))

    def test_invalid_explicit_path_does_not_fall_through(self):
        missing_path = Path("missing explicit ffmpeg.exe")

        with self.assertRaises(FfmpegError) as context:
            resolve_ffmpeg(
                explicit=missing_path,
                environ={"IMAGEIO_FFMPEG_EXE": "/ignored/environment/ffmpeg"},
                imageio_resolver=lambda: self.fail("must not fall through"),
                which=lambda _name: self.fail("must not fall through"),
            )

        self.assertIn("--ffmpeg-bin", str(context.exception))
        self.assertIn(str(missing_path), str(context.exception))

    def test_invalid_environment_path_does_not_fall_through(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir)

            with self.assertRaises(FfmpegError) as context:
                resolve_ffmpeg(
                    environ={"IMAGEIO_FFMPEG_EXE": str(invalid_path)},
                    imageio_resolver=lambda: self.fail("must not fall through"),
                    which=lambda _name: self.fail("must not fall through"),
                )

            self.assertIn("IMAGEIO_FFMPEG_EXE", str(context.exception))
            self.assertIn(str(invalid_path), str(context.exception))

    def test_real_bundled_ffmpeg_probes_two_second_video(self):
        ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "two second blue video.mp4"
            process = subprocess.run(
                [
                    ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:d=2",
                    "-c:v",
                    "mpeg4",
                    "-y",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)

            duration = probe_video_duration(ffmpeg_bin, video_path)

            self.assertGreaterEqual(duration, 1.9)
            self.assertLessEqual(duration, 2.1)

    def test_probe_rejects_missing_video(self):
        video_path = Path("missing video.mp4")

        with self.assertRaises(InputDataError) as context:
            probe_video_duration("unused-ffmpeg", video_path)

        self.assertIn("video does not exist", str(context.exception))
        self.assertIn(str(video_path), str(context.exception))

    def test_probe_rejects_file_without_video_duration(self):
        ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_video = Path(temp_dir) / "not a video.txt"
            invalid_video.write_text("not video data", encoding="utf-8")

            with self.assertRaises(FfmpegError) as context:
                probe_video_duration(ffmpeg_bin, invalid_video)

            self.assertIn(
                "Unable to decode video duration", str(context.exception)
            )
            self.assertIn(str(invalid_video), str(context.exception))

if __name__ == "__main__":
    unittest.main()
