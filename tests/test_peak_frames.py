from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from peak_frame_lib import (  # noqa: E402
    FfmpegError,
    InputDataError,
    SelectedPeak,
    SeriesPoint,
    extract_frame,
    find_local_peaks,
    parse_click_series,
    probe_video_duration,
    resolve_ffmpeg,
    select_peak_frames,
    validate_timestamps,
)


def click_record(second, value, material_id="m-123"):
    return {
        "recordType": "interaction_series",
        "interactionMetric": "CLICK_COUNT",
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


DECODABLE_JPEG = None
OLD_JPEG = None
_JPEG_FIXTURE_DIR = None


def setUpModule():
    global DECODABLE_JPEG, OLD_JPEG, _JPEG_FIXTURE_DIR
    ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
    _JPEG_FIXTURE_DIR = tempfile.TemporaryDirectory()
    fixture_path = Path(_JPEG_FIXTURE_DIR.name)
    fixture_bytes = []
    for name, color in (("new.jpg", "blue"), ("old.jpg", "red")):
        output_path = fixture_path / name
        process = subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=16x16:d=0.1",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if process.returncode != 0:
            raise RuntimeError(process.stderr)
        fixture_bytes.append(output_path.read_bytes())
    DECODABLE_JPEG, OLD_JPEG = fixture_bytes


def tearDownModule():
    _JPEG_FIXTURE_DIR.cleanup()


def successful_frame_subprocess(command, **_kwargs):
    if command[-1] != "-":
        Path(command[-1]).write_bytes(DECODABLE_JPEG)
    return subprocess.CompletedProcess(
        command, returncode=0, stdout="", stderr=""
    )


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

    def test_imageio_error_falls_back_to_system_path(self):
        resolver_error = RuntimeError("imageio resolver unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            system_path = Path(temp_dir) / "ffmpeg.exe"
            system_path.touch()

            def failing_imageio():
                raise resolver_error

            resolved = resolve_ffmpeg(
                environ={},
                imageio_resolver=failing_imageio,
                which=lambda _name: str(system_path),
            )

            self.assertEqual(resolved, str(system_path))

    def test_imageio_error_is_preserved_when_system_path_is_missing(self):
        resolver_error = RuntimeError("imageio resolver unavailable")

        def failing_imageio():
            raise resolver_error

        with self.assertRaises(FfmpegError) as context:
            resolve_ffmpeg(
                environ={},
                imageio_resolver=failing_imageio,
                which=lambda _name: None,
            )

        message = str(context.exception)
        self.assertIn("imageio resolver unavailable", message)
        self.assertIn("imageio-ffmpeg", message)
        self.assertIn("--ffmpeg-bin", message)
        self.assertIs(context.exception.__cause__, resolver_error)

    def test_path_lookup_error_is_wrapped_with_preserved_cause(self):
        path_error = OSError("PATH lookup unavailable")

        def failing_which(_name):
            raise path_error

        with self.assertRaises(FfmpegError) as context:
            resolve_ffmpeg(
                environ={},
                imageio_resolver=lambda: None,
                which=failing_which,
            )

        message = str(context.exception)
        self.assertIn("PATH lookup unavailable", message)
        self.assertIn("imageio-ffmpeg", message)
        self.assertIn("--ffmpeg-bin", message)
        self.assertIs(context.exception.__cause__, path_error)

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

    def test_probe_wraps_timeout_with_path_timeout_and_cause(self):
        timeout_sec = 1.25
        timeout_error = subprocess.TimeoutExpired(["ffmpeg"], timeout_sec)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "video.mp4"
            video_path.touch()

            with patch(
                "peak_frame_lib.subprocess.run", side_effect=timeout_error
            ) as run_mock:
                with self.assertRaises(FfmpegError) as context:
                    probe_video_duration(
                        "ffmpeg", video_path, timeout_sec=timeout_sec
                    )

            message = str(context.exception)
            self.assertIn(str(video_path), message)
            self.assertIn(str(timeout_sec), message)
            self.assertIs(context.exception.__cause__, timeout_error)
            self.assertEqual(run_mock.call_args.kwargs["timeout"], timeout_sec)

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

    def test_probe_does_not_parse_duration_from_invalid_filename(self):
        ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_video = (
                Path(temp_dir) / "Duration: 00:00:02.00 invalid.txt"
            )
            invalid_video.write_text("not video data", encoding="utf-8")

            with self.assertRaises(FfmpegError):
                probe_video_duration(ffmpeg_bin, invalid_video)

    def test_probe_rejects_duration_with_trailing_garbage_and_bounds_stderr(self):
        stderr = (
            "  Duration: 00:00:02.00garbage\n"
            + "x" * 2000
            + "UNBOUNDED_TAIL_MARKER"
        )
        process = subprocess.CompletedProcess(
            ["ffmpeg"], returncode=17, stdout="", stderr=stderr
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "invalid.mp4"
            video_path.touch()

            with patch("peak_frame_lib.subprocess.run", return_value=process):
                with self.assertRaises(FfmpegError) as context:
                    probe_video_duration("ffmpeg", video_path)

            message = str(context.exception)
            self.assertIn("return code 17", message)
            self.assertIn("Duration: 00:00:02.00garbage", message)
            self.assertNotIn("UNBOUNDED_TAIL_MARKER", message)


class FrameExtractionTests(unittest.TestCase):
    def test_extracts_exact_frames_matching_direct_ffmpeg_reference(self):
        ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
        with tempfile.TemporaryDirectory(prefix="peak frames with spaces ") as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "changing test video.mp4"
            process = subprocess.run(
                [
                    ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=s=64x64:r=25:d=9",
                    "-c:v",
                    "mpeg4",
                    "-y",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(process.returncode, 0, process.stderr)

            production_blobs = []
            for timestamp in (1.0, 3.0, 5.0, 7.0):
                with self.subTest(timestamp=timestamp):
                    output_path = (
                        temp_path
                        / "production frames"
                        / f"frame {timestamp:.1f}.jpg"
                    )
                    reference_path = (
                        temp_path
                        / "reference frames"
                        / f"frame {timestamp:.1f}.jpg"
                    )
                    reference_path.parent.mkdir(parents=True, exist_ok=True)

                    result = extract_frame(
                        ffmpeg_bin,
                        video_path,
                        timestamp,
                        output_path,
                    )
                    reference = subprocess.run(
                        [
                            ffmpeg_bin,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(video_path),
                            "-ss",
                            repr(float(timestamp)),
                            "-frames:v",
                            "1",
                            "-q:v",
                            "2",
                            "-y",
                            str(reference_path),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )

                    self.assertEqual(result, output_path)
                    self.assertEqual(reference.returncode, 0, reference.stderr)
                    self.assertGreater(output_path.stat().st_size, 0)
                    self.assertGreater(reference_path.stat().st_size, 0)
                    production_bytes = output_path.read_bytes()
                    self.assertEqual(production_bytes, reference_path.read_bytes())
                    production_blobs.append(production_bytes)

            self.assertEqual(len(set(production_blobs)), 4)

    def test_real_ffmpeg_accepts_plain_decimal_submicrosecond_timestamp(self):
        ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
        with tempfile.TemporaryDirectory(prefix="tiny timestamp ") as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "one second video.mp4"
            output_path = temp_path / "tiny timestamp frame.jpg"
            generated = subprocess.run(
                [
                    ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=s=64x64:r=25:d=1",
                    "-c:v",
                    "mpeg4",
                    "-y",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertIsNone(
                validate_timestamps(
                    [SelectedPeak("first_peak", 1e-7, 1.0)],
                    1.0,
                )
            )

            real_run = subprocess.run
            with patch(
                "peak_frame_lib.subprocess.run", wraps=real_run
            ) as run_mock:
                result = extract_frame(
                    ffmpeg_bin,
                    video_path,
                    1e-7,
                    output_path,
                    timeout_sec=30,
                )

            self.assertEqual(result, output_path)
            self.assertEqual(run_mock.call_args_list[0].args[0][7], "0.0000001")
            self.assertGreater(output_path.stat().st_size, 0)

    def test_uses_exact_output_seeking_command_and_creates_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video with spaces.mp4"
            output_path = temp_path / "new output dir" / "frame image.jpg"
            video_path.touch()

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=successful_frame_subprocess,
            ) as run_mock:
                result = extract_frame(
                    "ffmpeg with spaces", video_path, 1.25, output_path
                )

            self.assertEqual(result, output_path)
            self.assertEqual(run_mock.call_count, 2)
            extraction_call = run_mock.call_args_list[0]
            self.assertEqual(
                extraction_call.args[0][:-1],
                [
                    "ffmpeg with spaces",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(video_path),
                    "-ss",
                    "1.25",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                ],
            )
            staging_path = Path(extraction_call.args[0][-1])
            self.assertNotEqual(staging_path, output_path)
            self.assertEqual(staging_path.parent, output_path.parent)
            self.assertEqual(staging_path.suffix, output_path.suffix)
            self.assertFalse(staging_path.exists())
            self.assertEqual(output_path.read_bytes(), DECODABLE_JPEG)
            self.assertEqual(extraction_call.kwargs["timeout"], 30.0)
            self.assertEqual(
                run_mock.call_args_list[1].args[0],
                [
                    "ffmpeg with spaces",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(staging_path),
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ],
            )
            self.assertEqual(run_mock.call_args_list[1].kwargs["timeout"], 30.0)

    def test_successive_extractions_use_unique_same_directory_staging_paths(self):
        staging_paths = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frames" / "frame.jpg"
            video_path.touch()

            def create_output(command, **_kwargs):
                if command[-1] == "-":
                    return subprocess.CompletedProcess(
                        command, returncode=0, stdout="", stderr=""
                    )
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                staging_path.write_bytes(DECODABLE_JPEG)
                return subprocess.CompletedProcess(
                    command, returncode=0, stdout="", stderr=""
                )

            with patch(
                "peak_frame_lib.subprocess.run", side_effect=create_output
            ):
                extract_frame("ffmpeg", video_path, 1.0, output_path)
                extract_frame("ffmpeg", video_path, 2.0, output_path)

            self.assertEqual(len(set(staging_paths)), 2)
            self.assertTrue(
                all(path.parent == output_path.parent for path in staging_paths)
            )
            self.assertTrue(all(path.suffix == ".jpg" for path in staging_paths))
            self.assertTrue(all(not path.exists() for path in staging_paths))
            self.assertEqual(output_path.read_bytes(), DECODABLE_JPEG)

    def test_nonzero_return_preserves_final_and_cleans_only_partial_staging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)
            stderr = "decoder failure\n" + "x" * 1000 + "UNBOUNDED_TAIL_MARKER"
            staging_paths = []

            def fail_after_partial_output(command, **_kwargs):
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                self.assertNotEqual(staging_path, output_path)
                staging_path.write_bytes(b"partial output")
                self.assertEqual(output_path.read_bytes(), original_bytes)
                return subprocess.CompletedProcess(
                    command, returncode=17, stdout="", stderr=stderr
                )

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=fail_after_partial_output,
            ):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 3.5, output_path)

            message = str(context.exception)
            self.assertIn("3.5", message)
            self.assertIn("return code 17", message)
            self.assertIn("decoder failure", message)
            self.assertNotIn("UNBOUNDED_TAIL_MARKER", message)
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_replace_error_preserves_final_and_cleans_valid_staging(self):
        replace_error = PermissionError("destination is locked")
        staging_paths = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)

            def create_output(command, **_kwargs):
                if command[-1] == "-":
                    return subprocess.CompletedProcess(
                        command, returncode=0, stdout="", stderr=""
                    )
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                staging_path.write_bytes(DECODABLE_JPEG)
                return subprocess.CompletedProcess(
                    command, returncode=0, stdout="", stderr=""
                )

            with patch(
                "peak_frame_lib.subprocess.run", side_effect=create_output
            ), patch("peak_frame_lib.os.replace", side_effect=replace_error):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 4.0, output_path)

            message = str(context.exception)
            self.assertIn("publish", message.lower())
            self.assertIn("destination is locked", message)
            self.assertIs(context.exception.__cause__, replace_error)
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_cleanup_error_is_actionable_and_preserves_final(self):
        cleanup_error = PermissionError("staging file is locked")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)
            staging_paths = []

            def fail_after_partial_output(command, **_kwargs):
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                staging_path.write_bytes(b"partial output")
                return subprocess.CompletedProcess(
                    command, returncode=11, stdout="", stderr="decode failed"
                )

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=fail_after_partial_output,
            ) as run_mock, patch(
                "peak_frame_lib.Path.unlink", side_effect=cleanup_error
            ):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 4.5, output_path)

            message = str(context.exception)
            self.assertTrue(run_mock.called)
            self.assertIn("clean", message.lower())
            self.assertIn("staging file is locked", message)
            self.assertIs(context.exception.__cause__, cleanup_error)
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertEqual(len(staging_paths), 1)
            self.assertTrue(staging_paths[0].exists())

    def test_timeout_maps_to_ffmpeg_error_and_cleans_partial_output(self):
        timeout_sec = 1.25
        timeout_error = subprocess.TimeoutExpired(["ffmpeg"], timeout_sec)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)
            staging_paths = []

            def time_out_after_partial_output(command, **_kwargs):
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                staging_path.write_bytes(b"partial output")
                raise timeout_error

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=time_out_after_partial_output,
            ):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame(
                        "ffmpeg",
                        video_path,
                        5.0,
                        output_path,
                        timeout_sec=timeout_sec,
                    )

            message = str(context.exception)
            self.assertIn("5.0", message)
            self.assertIn(str(timeout_sec), message)
            self.assertIs(context.exception.__cause__, timeout_error)
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_os_error_maps_to_ffmpeg_error_with_cause_and_cleans_output(self):
        os_error = OSError("cannot launch ffmpeg")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)
            staging_paths = []

            def fail_to_launch_after_partial_output(command, **_kwargs):
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                staging_path.write_bytes(b"partial output")
                raise os_error

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=fail_to_launch_after_partial_output,
            ):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 7.0, output_path)

            message = str(context.exception)
            self.assertIn("7.0", message)
            self.assertIn("cannot launch ffmpeg", message)
            self.assertIs(context.exception.__cause__, os_error)
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_success_code_without_output_fails(self):
        process = subprocess.CompletedProcess(
            ["ffmpeg"], returncode=0, stdout="", stderr=""
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "missing frame.jpg"
            video_path.touch()

            with patch("peak_frame_lib.subprocess.run", return_value=process):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 1.0, output_path)

            self.assertIn("1.0", str(context.exception))
            self.assertIn("missing", str(context.exception).lower())
            self.assertFalse(output_path.exists())

    def test_success_code_with_zero_byte_output_fails_and_cleans_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "empty frame.jpg"
            video_path.touch()

            def create_empty_output(command, **_kwargs):
                Path(command[-1]).touch()
                return subprocess.CompletedProcess(
                    command, returncode=0, stdout="", stderr=""
                )

            with patch(
                "peak_frame_lib.subprocess.run", side_effect=create_empty_output
            ):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 2.0, output_path)

            self.assertIn("2.0", str(context.exception))
            self.assertIn("empty", str(context.exception).lower())
            self.assertFalse(output_path.exists())

    def test_nonempty_non_jpeg_output_fails_and_preserves_final(self):
        staging_paths = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)

            def create_garbage_output(command, **_kwargs):
                staging_path = Path(command[-1])
                staging_paths.append(staging_path)
                staging_path.write_bytes(b"not a jpeg")
                return subprocess.CompletedProcess(
                    command, returncode=0, stdout="", stderr=""
                )

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=create_garbage_output,
            ):
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 2.5, output_path)

            self.assertIn("JPEG", str(context.exception))
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_framed_garbage_fails_ffmpeg_decode_and_preserves_final(self):
        stderr = "invalid JPEG data\n" + "x" * 1000 + "UNBOUNDED_TAIL_MARKER"
        staging_paths = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            output_path.write_bytes(OLD_JPEG)

            def extract_then_reject_decode(command, **_kwargs):
                if command[-1] != "-":
                    staging_path = Path(command[-1])
                    staging_paths.append(staging_path)
                    staging_path.write_bytes(b"\xff\xd8garbage\xff\xd9")
                    return subprocess.CompletedProcess(
                        command, returncode=0, stdout="", stderr=""
                    )
                return subprocess.CompletedProcess(
                    command, returncode=23, stdout="", stderr=stderr
                )

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=extract_then_reject_decode,
            ) as run_mock:
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 2.75, output_path)

            message = str(context.exception)
            self.assertEqual(run_mock.call_count, 2)
            self.assertIn("decode", message.lower())
            self.assertIn("return code 23", message)
            self.assertIn("invalid JPEG data", message)
            self.assertNotIn("UNBOUNDED_TAIL_MARKER", message)
            self.assertEqual(output_path.read_bytes(), OLD_JPEG)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_ffmpeg_decode_timeout_preserves_final_and_cleans_staging(self):
        timeout_sec = 1.5
        timeout_error = subprocess.TimeoutExpired(["ffmpeg"], timeout_sec)
        staging_paths = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            output_path.write_bytes(OLD_JPEG)

            def extract_then_timeout_decode(command, **_kwargs):
                if command[-1] != "-":
                    staging_path = Path(command[-1])
                    staging_paths.append(staging_path)
                    staging_path.write_bytes(DECODABLE_JPEG)
                    return subprocess.CompletedProcess(
                        command, returncode=0, stdout="", stderr=""
                    )
                raise timeout_error

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=extract_then_timeout_decode,
            ) as run_mock:
                with self.assertRaises(FfmpegError) as context:
                    extract_frame(
                        "ffmpeg",
                        video_path,
                        3.0,
                        output_path,
                        timeout_sec=timeout_sec,
                    )

            message = str(context.exception)
            self.assertEqual(run_mock.call_count, 2)
            self.assertIn("decode", message.lower())
            self.assertIn(str(timeout_sec), message)
            self.assertIs(context.exception.__cause__, timeout_error)
            self.assertEqual(output_path.read_bytes(), OLD_JPEG)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_ffmpeg_decode_os_error_preserves_final_and_cleans_staging(self):
        os_error = OSError("decoder process unavailable")
        staging_paths = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_path = temp_path / "frame.jpg"
            video_path.touch()
            output_path.write_bytes(OLD_JPEG)

            def extract_then_fail_decode(command, **_kwargs):
                if command[-1] != "-":
                    staging_path = Path(command[-1])
                    staging_paths.append(staging_path)
                    staging_path.write_bytes(DECODABLE_JPEG)
                    return subprocess.CompletedProcess(
                        command, returncode=0, stdout="", stderr=""
                    )
                raise os_error

            with patch(
                "peak_frame_lib.subprocess.run",
                side_effect=extract_then_fail_decode,
            ) as run_mock:
                with self.assertRaises(FfmpegError) as context:
                    extract_frame("ffmpeg", video_path, 3.25, output_path)

            message = str(context.exception)
            self.assertEqual(run_mock.call_count, 2)
            self.assertIn("decode", message.lower())
            self.assertIn("decoder process unavailable", message)
            self.assertIs(context.exception.__cause__, os_error)
            self.assertEqual(output_path.read_bytes(), OLD_JPEG)
            self.assertEqual(len(staging_paths), 1)
            self.assertFalse(staging_paths[0].exists())

    def test_invalid_timeout_fails_before_filesystem_or_subprocess_changes(self):
        invalid_timeouts = (False, 0, -1, float("nan"), float("inf"), float("-inf"))
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            output_dir = temp_path / "frames"
            output_path = output_dir / "frame.jpg"
            video_path.touch()
            output_dir.mkdir()
            original_bytes = OLD_JPEG
            output_path.write_bytes(original_bytes)

            for timeout_sec in invalid_timeouts:
                with self.subTest(timeout_sec=timeout_sec):
                    entries_before = {path.name for path in output_dir.iterdir()}
                    with patch("peak_frame_lib.subprocess.run") as run_mock:
                        with self.assertRaises(FfmpegError) as context:
                            extract_frame(
                                "ffmpeg",
                                video_path,
                                1.0,
                                output_path,
                                timeout_sec=timeout_sec,
                            )

                    message = str(context.exception)
                    self.assertIn("timeout", message.lower())
                    self.assertIn(repr(timeout_sec), message)
                    run_mock.assert_not_called()
                    self.assertEqual(output_path.read_bytes(), original_bytes)
                    self.assertEqual(
                        {path.name for path in output_dir.iterdir()},
                        entries_before,
                    )


class TimestampValidationTests(unittest.TestCase):
    def test_accepts_zero_and_timestamp_just_under_video_duration(self):
        selected = [
            SelectedPeak("first_peak", 0.0, 10.0),
            SelectedPeak("later_top_1", 9.999999, 8.0),
        ]

        self.assertIsNone(validate_timestamps(selected, 10.0))

    def test_submicrosecond_timestamps_remain_below_duration_in_ffmpeg_argv(self):
        timestamps = (0.9999994, 0.9999995, 0.9999996, 0.9999999)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            video_path.touch()

            for index, timestamp in enumerate(timestamps):
                with self.subTest(timestamp=timestamp):
                    selected = [SelectedPeak("first_peak", timestamp, 10.0)]
                    self.assertIsNone(validate_timestamps(selected, 1.0))
                    output_path = temp_path / f"frame-{index}.jpg"

                    with patch(
                        "peak_frame_lib.subprocess.run",
                        side_effect=successful_frame_subprocess,
                    ) as run_mock:
                        extract_frame("ffmpeg", video_path, timestamp, output_path)

                    serialized_timestamp = run_mock.call_args_list[0].args[0][7]
                    self.assertEqual(serialized_timestamp, repr(timestamp))
                    self.assertNotEqual(serialized_timestamp, "1.000000")

    def test_rejects_timestamp_outside_video_bounds(self):
        for timestamp in (-0.1, 10.0, 10.1):
            with self.subTest(timestamp=timestamp):
                selected = [SelectedPeak("first_peak", timestamp, 10.0)]

                with self.assertRaises(InputDataError) as context:
                    validate_timestamps(selected, 10.0)

                message = str(context.exception)
                self.assertIn(str(timestamp), message)
                self.assertIn("10.0", message)

    def test_rejects_nonfinite_peak_timestamp(self):
        for timestamp in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timestamp=timestamp):
                selected = [SelectedPeak("first_peak", timestamp, 10.0)]

                with self.assertRaises(InputDataError) as context:
                    validate_timestamps(selected, 10.0)

                message = str(context.exception)
                self.assertIn(str(timestamp), message)
                self.assertIn("10.0", message)

    def test_rejects_nonfinite_or_nonpositive_video_duration(self):
        for duration in (
            float("nan"),
            float("inf"),
            float("-inf"),
            0.0,
            -1.0,
        ):
            with self.subTest(duration=duration):
                selected = [SelectedPeak("first_peak", 1.0, 10.0)]

                with self.assertRaises(InputDataError) as context:
                    validate_timestamps(selected, duration)

                self.assertIn(str(duration), str(context.exception))

if __name__ == "__main__":
    unittest.main()
