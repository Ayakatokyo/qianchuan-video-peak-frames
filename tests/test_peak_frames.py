import json
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
    build_summary,
    extract_frame,
    find_local_peaks,
    format_frame_filename,
    format_seconds_token,
    parse_click_series,
    probe_video_duration,
    resolve_ffmpeg,
    select_peak_frames,
    validate_timestamps,
    write_summaries,
)
import rpa_adapter  # noqa: E402


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


class FakeResponse:
    def __init__(self, payload, status_error=None, content=b""):
        self._payload = payload
        self._status_error = status_error
        self.content = content

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=8192):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]


class RecordingSession:
    def __init__(self, response):
        self.response = response
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self.response


class RpaAdapterTests(unittest.TestCase):
    def _env(self):
        return {
            "ENV_BACKEND_HOST": "https://gateway.example/",
            "YUCE_AUTHORIZATION": "auth-token",
            "YUCE_SESSION_ID": "session-123",
        }

    def test_fetch_material_diagnosis_uses_fixed_connector_payload(self):
        records = [
            click_record(0, 0, material_id="764"),
            click_record(1, 5, material_id="764"),
            click_record(2, 0, material_id="764"),
        ]
        session = RecordingSession(FakeResponse(records))

        try:
            result = rpa_adapter.fetch_material_diagnosis(
                shop_id="shop-1",
                material_id="764",
                date_range_type="LAST_7_DAYS",
                custom_start_date=None,
                custom_end_date=None,
                environ=self._env(),
                session=session,
            )
        except Exception as exc:
            self.fail(f"fetch_material_diagnosis raised {exc!r}")

        self.assertEqual(result, records)
        self.assertEqual(
            session.posts[0][0], "https://gateway.example/adg/v1/agent/invoke"
        )
        payload = session.posts[0][1]["json"]
        self.assertEqual(payload["authorization"], "auth-token")
        self.assertEqual(payload["agent_session_id"], "session-123")
        self.assertEqual(payload["data_source_type"], "rpa")
        self.assertEqual(payload["platform"], rpa_adapter.PLATFORM)
        self.assertEqual(payload["function_code"], rpa_adapter.FUNCTION_CODE)
        self.assertEqual(payload["shop_id"], "shop-1")
        self.assertEqual(
            payload["business_params"],
            {"material_id": "764", "date_range_type": "LAST_7_DAYS"},
        )
        self.assertEqual(session.posts[0][1]["timeout"], 3600)

    def test_list_accounts_uses_qianchuan_platform(self):
        payload = {"data": [{"shop_id": "shop-1"}]}
        session = RecordingSession(FakeResponse(payload))

        self.assertTrue(hasattr(rpa_adapter, "list_accounts"))
        result = rpa_adapter.list_accounts(environ=self._env(), session=session)

        self.assertEqual(result, payload)
        self.assertEqual(
            session.posts[0][0],
            "https://gateway.example/adg/v1/agent/rpa/authorizations/query",
        )
        self.assertEqual(
            session.posts[0][1]["json"],
            {"authorization": "auth-token", "platform": rpa_adapter.PLATFORM},
        )
        self.assertEqual(session.posts[0][1]["timeout"], 30)

    def test_fetch_rejects_custom_range_without_both_dates(self):
        error_type = getattr(rpa_adapter, "RpaAdapterError", Exception)
        with self.assertRaisesRegex(error_type, "CUSTOM requires"):
            rpa_adapter.fetch_material_diagnosis(
                shop_id="shop-1",
                material_id="764",
                date_range_type="CUSTOM",
                custom_start_date="2026-07-01",
                custom_end_date=None,
                environ=self._env(),
                session=RecordingSession(FakeResponse([])),
            )

    def test_fetch_wraps_gateway_http_errors(self):
        session = RecordingSession(
            FakeResponse([], status_error=RuntimeError("gateway down"))
        )

        with self.assertRaisesRegex(rpa_adapter.RpaAdapterError, "gateway"):
            rpa_adapter.fetch_material_diagnosis(
                shop_id="shop-1",
                material_id="764",
                date_range_type="LAST_7_DAYS",
                environ=self._env(),
                session=session,
            )

    def test_normalize_gateway_records_accepts_payload_envelope(self):
        records = [click_record(0, 0), click_record(1, 3), click_record(2, 0)]

        self.assertTrue(hasattr(rpa_adapter, "normalize_gateway_records"))
        self.assertEqual(
            rpa_adapter.normalize_gateway_records(
                {"result_type": "payload", "payload": records}
            ),
            records,
        )

    def test_normalize_gateway_records_rejects_file_results(self):
        error_type = getattr(rpa_adapter, "RpaAdapterError", Exception)
        self.assertTrue(hasattr(rpa_adapter, "normalize_gateway_records"))
        with self.assertRaisesRegex(error_type, "files"):
            rpa_adapter.normalize_gateway_records(
                {"result_type": "files", "files": []}
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
    def test_extracts_center_pixel_color_from_each_target_second(self):
        ffmpeg_bin = resolve_ffmpeg(environ={}, which=lambda _name: None)
        with tempfile.TemporaryDirectory(prefix="peak color proof ") as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "four color segments.mp4"
            generated = subprocess.run(
                [
                    ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=64x64:r=25:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=lime:s=64x64:r=25:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:r=25:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=yellow:s=64x64:r=25:d=2",
                    "-filter_complex",
                    "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
                    "-map",
                    "[v]",
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

            expected_colors = {
                1.0: (255, 0, 0),
                3.0: (0, 255, 0),
                5.0: (0, 0, 255),
                7.0: (255, 255, 0),
            }
            for timestamp, expected_rgb in expected_colors.items():
                with self.subTest(timestamp=timestamp):
                    output_path = temp_path / f"frame-{timestamp:g}.jpg"
                    extract_frame(
                        ffmpeg_bin, video_path, timestamp, output_path
                    )
                    decoded = subprocess.run(
                        [
                            ffmpeg_bin,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(output_path),
                            "-f",
                            "rawvideo",
                            "-pix_fmt",
                            "rgb24",
                            "-",
                        ],
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(
                        decoded.returncode,
                        0,
                        decoded.stderr.decode("utf-8", errors="replace"),
                    )
                    center_offset = ((32 * 64) + 32) * 3
                    center_rgb = tuple(
                        decoded.stdout[center_offset : center_offset + 3]
                    )
                    self.assertEqual(len(center_rgb), 3)
                    for actual, expected in zip(center_rgb, expected_rgb):
                        self.assertLessEqual(abs(actual - expected), 40)

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


class SummaryTests(unittest.TestCase):
    def test_formats_integer_fractional_and_submicrosecond_seconds_safely(self):
        self.assertEqual(format_seconds_token(7.0), "0007s")
        self.assertEqual(format_seconds_token(7.5), "0007p5s")
        self.assertEqual(format_seconds_token(0.0000001), "0000p0000001s")
        self.assertEqual(
            format_frame_filename("first_peak", 7.0, 18.0),
            "first_peak_0007s_18.jpg",
        )
        self.assertEqual(
            format_frame_filename("later_top_1", 7.5, 12.25),
            "later_top_1_0007p5s_12p25.jpg",
        )

    def test_summary_warns_when_later_peaks_are_missing(self):
        selected = [
            SelectedPeak("first_peak", 1.0, 5.0),
            SelectedPeak("later_top_1", 3.0, 4.0),
        ]

        summary = build_summary(
            "m-123",
            "input.json",
            "video.mp4",
            selected,
            ["a.jpg", "b.jpg"],
        )

        self.assertEqual(len(summary["frames"]), 2)
        self.assertEqual(
            summary["warnings"],
            ["2 later peak frame(s) unavailable"],
        )
        self.assertEqual(
            [frame["rankType"] for frame in summary["frames"]],
            ["first_peak", "later_top_1"],
        )

    def test_summary_rejects_mismatched_peaks_and_paths(self):
        selected = [SelectedPeak("first_peak", 1.0, 5.0)]

        with self.assertRaisesRegex(ValueError, "equal length"):
            build_summary(
                "m-123", "input.json", "video.mp4", selected, []
            )

    def test_summary_stores_portable_frame_filenames(self):
        selected = [SelectedPeak("first_peak", 1.0, 5.0)]
        summary = build_summary(
            "m-123",
            "input.json",
            "video.mp4",
            selected,
            [Path("output") / "m-123" / "first.jpg"],
        )

        self.assertEqual(summary["frames"][0]["framePath"], "first.jpg")

    def test_writes_json_and_markdown_after_frames_exist(self):
        selected = [
            SelectedPeak("first_peak", 1.0, 5.0),
            SelectedPeak("later_top_1", 3.0, 4.0),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "m-123"
            result_dir.mkdir()
            frame_paths = [result_dir / "first.jpg", result_dir / "later.jpg"]
            for frame_path in frame_paths:
                frame_path.write_bytes(DECODABLE_JPEG)
            summary = build_summary(
                "m-123",
                "input.json",
                "video.mp4",
                selected,
                frame_paths,
            )

            json_path, markdown_path = write_summaries(summary, result_dir)

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertEqual(payload, summary)
            self.assertIn("input.json", markdown)
            self.assertIn("video.mp4", markdown)
            self.assertIn("earliest_local_peak", markdown)
            self.assertIn(
                "top_3_by_metric_desc_then_time_asc_after_first", markdown
            )
            self.assertIn("collapse_to_earliest_second", markdown)
            self.assertIn("boundariesExcluded: true", markdown)
            self.assertIn("请求截帧秒数", markdown)
            self.assertIn("| first_peak | 1 | 5 | 1 | first.jpg |", markdown)
            self.assertLess(
                markdown.index("first_peak"), markdown.index("later_top_1")
            )
            self.assertIn("2 later peak frame(s) unavailable", markdown)
            self.assertEqual(list(result_dir.glob("*.tmp*")), [])

    def test_refuses_to_write_summaries_for_missing_or_empty_frame(self):
        selected = [SelectedPeak("first_peak", 1.0, 5.0)]
        with tempfile.TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir)
            missing = result_dir / "missing.jpg"
            summary = build_summary(
                "m-123", "input.json", "video.mp4", selected, [missing]
            )

            with self.assertRaisesRegex(InputDataError, "frame.*missing"):
                write_summaries(summary, result_dir)

            empty = result_dir / "empty.jpg"
            empty.touch()
            summary = build_summary(
                "m-123", "input.json", "video.mp4", selected, [empty]
            )
            with self.assertRaisesRegex(InputDataError, "frame.*empty"):
                write_summaries(summary, result_dir)

            self.assertFalse((result_dir / "summary.json").exists())
            self.assertFalse((result_dir / "summary.md").exists())


class CliTests(unittest.TestCase):
    def _make_video(self, ffmpeg, video, duration=9):
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s=64x64:r=25:d={duration}",
                "-c:v",
                "mpeg4",
                "-y",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def _run_local(self, input_json, video, output, ffmpeg):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "extract_peak_frames.py"),
                "--input-json",
                str(input_json),
                "--video",
                str(video),
                "--output-dir",
                str(output),
                "--ffmpeg-bin",
                str(ffmpeg),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )

    def _write_series(self, path, seconds_and_values):
        path.write_text(
            json.dumps(
                [
                    click_record(second, value)
                    for second, value in seconds_and_values
                ]
            ),
            encoding="utf-8",
        )

    def test_local_cli_outputs_four_frames_and_two_summaries(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "material with spaces.mp4"
            input_json = root / "rpa result.json"
            output = root / "output with spaces"
            self._make_video(ffmpeg, video)
            self._write_series(
                input_json,
                list(enumerate([0, 2, 0, 9, 0, 7, 0, 8, 0])),
            )

            completed = self._run_local(input_json, video, output, ffmpeg)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_dir = output / "m-123"
            self.assertEqual(len(list(result_dir.glob("*.jpg"))), 4)
            summary_path = result_dir / "summary.json"
            self.assertTrue(summary_path.is_file())
            self.assertTrue((result_dir / "summary.md").is_file())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [frame["rankType"] for frame in summary["frames"]],
                [
                    "first_peak",
                    "later_top_1",
                    "later_top_2",
                    "later_top_3",
                ],
            )
            self.assertEqual(summary["warnings"], [])
            self.assertIn(f"result_dir={result_dir}", completed.stdout)

    def test_local_cli_rerun_removes_only_owned_stale_frames(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            input_json = root / "input.json"
            output = root / "output"
            result_dir = output / "m-123"
            self._make_video(ffmpeg, video)
            self._write_series(
                input_json,
                list(enumerate([0, 2, 0, 9, 0, 7, 0, 8, 0])),
            )
            first_run = self._run_local(input_json, video, output, ffmpeg)
            self.assertEqual(first_run.returncode, 0, first_run.stderr)
            stale_owned_frames = set(result_dir.glob("*.jpg"))
            self.assertEqual(len(stale_owned_frames), 4)
            user_frame = result_dir / "user-reference.jpg"
            user_frame.write_bytes(DECODABLE_JPEG)

            self._write_series(
                input_json, list(enumerate([0, 5, 0, 4, 0]))
            )
            second_run = self._run_local(input_json, video, output, ffmpeg)

            self.assertEqual(second_run.returncode, 0, second_run.stderr)
            self.assertTrue(user_frame.is_file())
            self.assertTrue(stale_owned_frames.isdisjoint(result_dir.glob("*.jpg")))
            summary = json.loads(
                (result_dir / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(summary["frames"]), 2)

    def test_local_cli_rerun_ignores_non_object_previous_summary(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            input_json = root / "input.json"
            output = root / "output"
            result_dir = output / "m-123"
            self._make_video(ffmpeg, video, duration=5)
            self._write_series(
                input_json, list(enumerate([0, 5, 0, 4, 0]))
            )
            first_run = self._run_local(input_json, video, output, ffmpeg)
            self.assertEqual(first_run.returncode, 0, first_run.stderr)
            (result_dir / "summary.json").write_text("[]\n", encoding="utf-8")

            second_run = self._run_local(input_json, video, output, ffmpeg)

            self.assertEqual(second_run.returncode, 0, second_run.stderr)
            summary = json.loads(
                (result_dir / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(summary["frames"]), 2)

    def test_local_cli_outputs_available_peaks_with_warning(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            input_json = root / "input.json"
            output = root / "output"
            self._make_video(ffmpeg, video, duration=5)
            self._write_series(
                input_json, list(enumerate([0, 5, 0, 4, 0]))
            )

            completed = self._run_local(input_json, video, output, ffmpeg)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(
                (output / "m-123" / "summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(summary["frames"]), 2)
            self.assertEqual(
                summary["warnings"],
                ["2 later peak frame(s) unavailable"],
            )

    def test_rpa_source_requires_shop_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "extract_peak_frames.py"),
                    "--source",
                    "rpa",
                    "--material-id",
                    "m-123",
                    "--date-range-type",
                    "LAST_7_DAYS",
                    "--output-dir",
                    temp_dir,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--shop-id", completed.stderr)

    def test_rpa_cli_fetches_downloads_and_reuses_local_pipeline(self):
        import extract_peak_frames

        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_video = root / "source.mp4"
            output = root / "output"
            self._make_video(ffmpeg, source_video)
            records = [
                click_record(second, value, material_id="764")
                for second, value in enumerate([0, 2, 0, 9, 0, 7, 0, 8, 0])
            ]
            records.append(
                {
                    "recordType": "material_info",
                    "materialId": "764",
                    "videoUrl": "https://cdn.example/material.mp4",
                    "videoDuration": 9.0,
                }
            )
            downloaded = []

            def fake_download(video_url, target_path):
                downloaded.append((video_url, Path(target_path)))
                Path(target_path).write_bytes(source_video.read_bytes())
                return Path(target_path)

            with patch(
                "extract_peak_frames.fetch_material_diagnosis",
                return_value=records,
            ) as fetch_mock, patch(
                "extract_peak_frames.download_video",
                side_effect=fake_download,
                create=True,
            ):
                try:
                    completed = extract_peak_frames.main(
                        [
                            "--source",
                            "rpa",
                            "--shop-id",
                            "shop-1",
                            "--material-id",
                            "764",
                            "--date-range-type",
                            "LAST_7_DAYS",
                            "--output-dir",
                            str(output),
                            "--ffmpeg-bin",
                            ffmpeg,
                        ]
                    )
                except SystemExit as exc:
                    completed = exc.code

            self.assertEqual(completed, 0)
            fetch_mock.assert_called_once_with(
                shop_id="shop-1",
                material_id="764",
                date_range_type="LAST_7_DAYS",
                custom_start_date=None,
                custom_end_date=None,
            )
            self.assertEqual(downloaded[0][0], "https://cdn.example/material.mp4")
            summary = json.loads(
                (output / "764" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["materialId"], "764")
            self.assertEqual(
                [frame["rankType"] for frame in summary["frames"]],
                [
                    "first_peak",
                    "later_top_1",
                    "later_top_2",
                    "later_top_3",
                ],
            )

    def test_rpa_cli_processes_top_materials_json(self):
        import extract_peak_frames

        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_video = root / "source.mp4"
            output = root / "output"
            top_materials = root / "top_materials.json"
            self._make_video(ffmpeg, source_video)
            top_materials.write_text(
                json.dumps(
                    {
                        "materials": [
                            {"rank": 1, "materialId": "764"},
                            {"rank": 2, "materialId": "765"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def records_for(material_id):
                records = [
                    click_record(second, value, material_id=material_id)
                    for second, value in enumerate(
                        [0, 2, 0, 9, 0, 7, 0, 8, 0]
                    )
                ]
                records.append(
                    {
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://cdn.example/{material_id}.mp4",
                    }
                )
                return records

            def fake_fetch(**kwargs):
                return records_for(kwargs["material_id"])

            def fake_download(_video_url, target_path):
                Path(target_path).write_bytes(source_video.read_bytes())
                return Path(target_path)

            with patch(
                "extract_peak_frames.fetch_material_diagnosis",
                side_effect=fake_fetch,
            ) as fetch_mock, patch(
                "extract_peak_frames.download_video",
                side_effect=fake_download,
            ):
                completed = extract_peak_frames.main(
                    [
                        "--source",
                        "rpa",
                        "--shop-id",
                        "shop-1",
                        "--top-materials-json",
                        str(top_materials),
                        "--date-range-type",
                        "LAST_7_DAYS",
                        "--output-dir",
                        str(output),
                        "--ffmpeg-bin",
                        ffmpeg,
                    ]
                )

            self.assertEqual(completed, 0)
            self.assertEqual(
                [call.kwargs["material_id"] for call in fetch_mock.call_args_list],
                ["764", "765"],
            )
            self.assertTrue((output / "764" / "summary.json").exists())
            self.assertTrue((output / "765" / "summary.json").exists())

    def test_rpa_custom_range_requires_both_dates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "extract_peak_frames.py"),
                    "--source",
                    "rpa",
                    "--shop-id",
                    "shop-1",
                    "--material-id",
                    "m-123",
                    "--date-range-type",
                    "CUSTOM",
                    "--custom-start-date",
                    "2026-07-01",
                    "--output-dir",
                    temp_dir,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("CUSTOM requires", completed.stderr)

    def test_local_cli_rejects_malformed_json(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            video.touch()
            input_json = root / "bad.json"
            input_json.write_text("{not-json", encoding="utf-8")

            completed = self._run_local(
                input_json, video, root / "out", ffmpeg
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("valid JSON", completed.stderr)

    def test_local_cli_rejects_missing_video(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_json = root / "input.json"
            self._write_series(
                input_json, list(enumerate([0, 5, 0]))
            )

            completed = self._run_local(
                input_json, root / "missing.mp4", root / "out", ffmpeg
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("video does not exist", completed.stderr)

    def test_local_cli_rejects_no_peaks(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            video.touch()
            input_json = root / "input.json"
            self._write_series(
                input_json, list(enumerate([1, 2, 3]))
            )

            completed = self._run_local(
                input_json, video, root / "out", ffmpeg
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("No local peaks", completed.stderr)

    def test_local_cli_rejects_peak_at_video_duration(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            self._make_video(ffmpeg, video, duration=2)
            input_json = root / "input.json"
            self._write_series(input_json, [(0, 0), (2, 5), (3, 0)])

            completed = self._run_local(
                input_json, video, root / "out", ffmpeg
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("video duration", completed.stderr)

if __name__ == "__main__":
    unittest.main()
