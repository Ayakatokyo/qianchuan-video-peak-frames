import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from peak_frame_lib import (  # noqa: E402
    DEFAULT_FFMPEG_TIMEOUT_SEC,
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
    process_records,
    resolve_ffmpeg,
    select_peak_frames,
    validate_timestamps,
    write_summaries,
)
import rpa_adapter  # noqa: E402
import extract_peak_frames  # noqa: E402
from batch_runtime import (  # noqa: E402
    BatchLockError,
    acquire_run_lock,
    mark_report_ready,
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


class FakeResponse:
    def __init__(self, payload, status_error=None, content=b""):
        self._payload = payload
        self._status_error = status_error
        self.content = content
        self.headers = {}
        self.closed = False

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=8192):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]

    def close(self):
        self.closed = True


class RecordingSession:
    def __init__(self, post_response, get_response=None):
        self.post_response = post_response
        self.get_response = post_response if get_response is None else get_response
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.post_response

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self.get_response


class RaisingPostSession:
    def __init__(self, error):
        self.error = error
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        raise self.error


class RpaAdapterTests(unittest.TestCase):
    def _env(self):
        return {
            "ENV_BACKEND_HOST": "https://gateway.example/",
            "YUCE_AUTHORIZATION": "auth-token",
            "YUCE_SESSION_ID": "session-123",
        }

    def test_video_download_rejects_maximum_size_and_closes_response(self):
        response = FakeResponse(None, content=b"12345")
        session = RecordingSession(FakeResponse(None), response)
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "video.mp4"
            with self.assertRaises(rpa_adapter.RpaAdapterError):
                rpa_adapter.download_video(
                    "https://video.example/video.mp4",
                    target,
                    session=session,
                    max_bytes=4,
                )
            self.assertFalse(target.exists())
        self.assertTrue(response.closed)

    def test_video_download_enforces_total_deadline_and_closes_response(self):
        response = FakeResponse(None, content=b"1")
        session = RecordingSession(FakeResponse(None), response)
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(rpa_adapter.RpaAdapterError):
                rpa_adapter.download_video(
                    "https://video.example/video.mp4",
                    Path(temp_dir) / "video.mp4",
                    session=session,
                    deadline_sec=0,
                )
        self.assertTrue(response.closed)

    def _files_envelope(
        self,
        *,
        success=True,
        code="200",
        status="COMPLETED",
        failed_instances=0,
        files=None,
    ):
        if files is None:
            files = [
                {
                    "fileUrl": (
                        "https://files.example/diagnosis.csv?secret=redacted"
                    )
                }
            ]
        return {
            "success": success,
            "code": code,
            "data": {
                "result_type": "files",
                "result_content": {
                    "status": status,
                    "failedInstances": failed_instances,
                    "files": files,
                },
            },
        }

    def test_fetch_material_diagnosis_downloads_nested_files_envelope(self):
        envelope = {
            "success": True,
            "code": "200",
            "data": {
                "result_type": "files",
                "result_content": {
                    "status": "COMPLETED",
                    "failedInstances": 0,
                    "files": [
                        {"fileUrl": "https://files.example/diagnosis.csv"}
                    ],
                },
            },
        }
        csv_bytes = (
            b"recordType\x01materialId\x01interactionMetric\x01durationSec\x01metricValue\n"
            b"\x02interaction_series\x01m-files\x01CLICK_COUNT\x011\x012\n"
        )
        session = RecordingSession(
            FakeResponse(envelope), FakeResponse(None, content=csv_bytes)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_dir = Path(temp_dir) / "diagnosis-csv"
            records = rpa_adapter.fetch_material_diagnosis(
                shop_id="shop-1",
                material_id="m-files",
                date_range_type="LAST_7_DAYS",
                diagnosis_csv_dir=csv_dir,
                environ=self._env(),
                session=session,
            )

            self.assertEqual(records, [
                {
                    "recordType": "interaction_series",
                    "materialId": "m-files",
                    "interactionMetric": "CLICK_COUNT",
                    "durationSec": 1.0,
                    "metricValue": 2.0,
                }
            ])
            self.assertEqual(
                (csv_dir / "m-files.csv").read_bytes(), csv_bytes
            )
        self.assertEqual(
            session.gets,
            [
                (
                    "https://files.example/diagnosis.csv",
                    {"stream": True, "timeout": 300},
                )
            ],
        )

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

    def test_preflight_material_availability_passes_with_video_url(self):
        records = [
            click_record(0, 0, material_id="m-available"),
            {
                "recordType": "material_info",
                "materialId": "m-available",
                "videoUrl": " https://video.example/m-available.mp4 ",
            },
        ]

        result = rpa_adapter.preflight_material_availability(
            records, "m-available"
        )

        self.assertEqual(
            result,
            {
                "materialId": "m-available",
                "rpaAvailable": True,
                "failureReason": None,
                "videoUrl": "https://video.example/m-available.mp4",
            },
        )

    def test_preflight_material_availability_rejects_missing_material_info(self):
        records = [click_record(0, 0, material_id="m-missing-info")]

        result = rpa_adapter.preflight_material_availability(
            records, "m-missing-info"
        )

        self.assertEqual(
            result,
            {
                "materialId": "m-missing-info",
                "rpaAvailable": False,
                "failureReason": "missing_material_info",
                "videoUrl": None,
            },
        )

    def test_preflight_material_availability_rejects_empty_video_url(self):
        records = [
            {
                "recordType": "material_info",
                "materialId": "m-empty-video",
                "videoUrl": "   ",
            }
        ]

        result = rpa_adapter.preflight_material_availability(
            records, "m-empty-video"
        )

        self.assertEqual(
            result,
            {
                "materialId": "m-empty-video",
                "rpaAvailable": False,
                "failureReason": "missing_video_url",
                "videoUrl": None,
            },
        )

    def test_fetch_material_preflight_classifies_invalid_payload(self):
        session = RecordingSession(FakeResponse({"payload": {"not": "a-list"}}))

        result = rpa_adapter.fetch_material_preflight(
            shop_id="shop-1",
            material_id="m-invalid",
            date_range_type="LAST_7_DAYS",
            environ=self._env(),
            session=session,
        )

        self.assertEqual(result["materialId"], "m-invalid")
        self.assertFalse(result["rpaAvailable"])
        self.assertEqual(result["failureReason"], "rpa_payload_invalid")
        self.assertIsNone(result["videoUrl"])

    def test_fetch_material_preflight_uses_configured_timeout(self):
        records = [
            {
                "recordType": "material_info",
                "materialId": "m-timeout",
                "videoUrl": "https://video.example/m-timeout.mp4",
            }
        ]
        session = RecordingSession(FakeResponse(records))

        result = rpa_adapter.fetch_material_preflight(
            shop_id="shop-1",
            material_id="m-timeout",
            date_range_type="LAST_7_DAYS",
            environ=self._env(),
            session=session,
            timeout_sec=45,
        )

        self.assertTrue(result["rpaAvailable"])
        self.assertEqual(session.posts[0][1]["timeout"], 45)

    def test_fetch_material_preflight_exposes_normalized_records(self):
        records = [
            {
                "recordType": "material_info",
                "materialId": "m-records",
                "videoUrl": "https://video.example/m-records.mp4",
            }
        ]
        session = RecordingSession(FakeResponse({"payload": records}))

        result = rpa_adapter.fetch_material_preflight(
            shop_id="shop-1",
            material_id="m-records",
            date_range_type="LAST_7_DAYS",
            environ=self._env(),
            session=session,
        )

        self.assertTrue(result["rpaAvailable"])
        self.assertEqual(result["records"], records)
        self.assertIsNone(result["diagnosisCsvPath"])

    def test_fetch_material_preflight_exposes_files_csv_path_and_records(self):
        csv_bytes = (
            b"recordType\x01materialId\x01videoUrl\n"
            b"material_info\x01m-files-preflight\x01https://video.example/m-files.mp4\n"
        )
        session = RecordingSession(
            FakeResponse(self._files_envelope()),
            FakeResponse(None, content=csv_bytes),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_dir = Path(temp_dir) / "diagnosis-csv"
            result = rpa_adapter.fetch_material_preflight(
                shop_id="shop-1",
                material_id="m-files-preflight",
                date_range_type="LAST_7_DAYS",
                diagnosis_csv_dir=csv_dir,
                environ=self._env(),
                session=session,
            )

            self.assertTrue(result["rpaAvailable"])
            self.assertEqual(
                result["records"],
                [
                    {
                        "recordType": "material_info",
                        "materialId": "m-files-preflight",
                        "videoUrl": "https://video.example/m-files.mp4",
                    }
                ],
            )
            self.assertEqual(
                result["diagnosisCsvPath"], str(csv_dir / "m-files-preflight.csv")
            )
            self.assertEqual(
                (csv_dir / "m-files-preflight.csv").read_bytes(), csv_bytes
            )

    def test_fetch_material_preflight_classifies_request_timeout(self):
        session = RaisingPostSession(TimeoutError("timed out"))

        result = rpa_adapter.fetch_material_preflight(
            shop_id="shop-1",
            material_id="m-timeout",
            date_range_type="LAST_7_DAYS",
            environ=self._env(),
            session=session,
            timeout_sec=1,
        )

        self.assertFalse(result["rpaAvailable"])
        self.assertEqual(result["failureReason"], "rpa_request_failed")

    def test_fetch_rejects_invalid_files_envelopes_without_url_leakage(self):
        invalid_envelopes = [
            self._files_envelope(success=False),
            self._files_envelope(code="500"),
            self._files_envelope(status="RUNNING"),
            self._files_envelope(failed_instances=1),
            self._files_envelope(files=[]),
            self._files_envelope(files=[{}]),
        ]

        for envelope in invalid_envelopes:
            with self.subTest(envelope=envelope):
                session = RecordingSession(FakeResponse(envelope))
                with tempfile.TemporaryDirectory() as temp_dir:
                    with self.assertRaises(rpa_adapter.RpaAdapterError) as error:
                        rpa_adapter.fetch_material_diagnosis(
                            shop_id="shop-1",
                            material_id="m-files",
                            date_range_type="LAST_7_DAYS",
                            diagnosis_csv_dir=Path(temp_dir),
                            environ=self._env(),
                            session=session,
                        )
                self.assertNotIn("secret=redacted", str(error.exception))
                self.assertEqual(session.gets, [])

    def test_fetch_redacts_download_error_url(self):
        session = RecordingSession(
            FakeResponse(self._files_envelope()),
            FakeResponse(
                None,
                status_error=RuntimeError(
                    "download https://files.example/diagnosis.csv?secret=redacted"
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(rpa_adapter.RpaAdapterError) as error:
                rpa_adapter.fetch_material_diagnosis(
                    shop_id="shop-1",
                    material_id="m-files",
                    date_range_type="LAST_7_DAYS",
                    diagnosis_csv_dir=Path(temp_dir),
                    environ=self._env(),
                    session=session,
                )

        self.assertNotIn("secret=redacted", str(error.exception))

    def test_fetch_retains_malformed_diagnostic_csv(self):
        csv_bytes = b"recordType,materialId\ninteraction_series,m-files\n"
        session = RecordingSession(
            FakeResponse(self._files_envelope()),
            FakeResponse(None, content=csv_bytes),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_dir = Path(temp_dir) / "diagnosis-csv"
            with self.assertRaises(rpa_adapter.RpaAdapterError) as error:
                rpa_adapter.fetch_material_diagnosis(
                    shop_id="shop-1",
                    material_id="m-files",
                    date_range_type="LAST_7_DAYS",
                    diagnosis_csv_dir=csv_dir,
                    environ=self._env(),
                    session=session,
                )

            self.assertEqual((csv_dir / "m-files.csv").read_bytes(), csv_bytes)
        self.assertNotIn("secret=redacted", str(error.exception))


class PeakSelectionTests(unittest.TestCase):
    def test_selects_zero_second_frame_then_global_top_three_peaks(self):
        values = [1, 5, 1, 9, 1, 7, 1, 8, 1]
        points = [SeriesPoint(i, value) for i, value in enumerate(values)]

        selected = select_peak_frames(points)

        self.assertEqual(
            [
                (item.rank_type, item.duration_sec, item.metric_value)
                for item in selected
            ],
            [
                ("zero_second", 0.0, 1.0),
                ("peak_top_1", 3.0, 9.0),
                ("peak_top_2", 7.0, 8.0),
                ("peak_top_3", 5.0, 7.0),
            ],
        )

    def test_selects_video_start_for_cover_when_series_starts_after_zero(self):
        points = [
            SeriesPoint(1.0, 5.0),
            SeriesPoint(2.0, 1.0),
            SeriesPoint(3.0, 9.0),
            SeriesPoint(4.0, 1.0),
        ]

        selected = select_peak_frames(points)

        self.assertEqual(selected[0], SelectedPeak("zero_second", 0.0, None))

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
            [SeriesPoint(i, value) for i, value in enumerate(values)]
        )

        self.assertEqual(
            [item.duration_sec for item in selected],
            [0.0, 3.0, 5.0, 7.0],
        )

    def test_select_rejects_no_peaks(self):
        with self.assertRaisesRegex(InputDataError, "No local peaks"):
            select_peak_frames([])

    def test_returns_only_existing_top_peaks(self):
        values = [0, 5, 0, 4, 0]

        selected = select_peak_frames(
            [SeriesPoint(i, value) for i, value in enumerate(values)]
        )

        self.assertEqual(
            [item.rank_type for item in selected],
            ["zero_second", "peak_top_1", "peak_top_2"],
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
                    [SelectedPeak("zero_second", 1e-7, 1.0)],
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
            self.assertEqual(
                extraction_call.kwargs["timeout"], DEFAULT_FFMPEG_TIMEOUT_SEC
            )
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
            self.assertEqual(
                run_mock.call_args_list[1].kwargs["timeout"],
                DEFAULT_FFMPEG_TIMEOUT_SEC,
            )

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
            SelectedPeak("zero_second", 0.0, 10.0),
            SelectedPeak("peak_top_1", 9.999999, 8.0),
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
                    selected = [SelectedPeak("peak_top_1", timestamp, 10.0)]
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
                selected = [SelectedPeak("peak_top_1", timestamp, 10.0)]

                with self.assertRaises(InputDataError) as context:
                    validate_timestamps(selected, 10.0)

                message = str(context.exception)
                self.assertIn(str(timestamp), message)
                self.assertIn("10.0", message)

    def test_rejects_nonfinite_peak_timestamp(self):
        for timestamp in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timestamp=timestamp):
                selected = [SelectedPeak("peak_top_1", timestamp, 10.0)]

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
                selected = [SelectedPeak("peak_top_1", 1.0, 10.0)]

                with self.assertRaises(InputDataError) as context:
                    validate_timestamps(selected, duration)

                self.assertIn(str(duration), str(context.exception))


class SummaryTests(unittest.TestCase):
    def test_summary_uses_stable_source_for_temporary_rpa_download(self):
        summary = build_summary(
            "m-123",
            "rpa://connector/m-123",
            "/temporary/download/m-123.mp4",
            [SelectedPeak("zero_second", 0.0, 5.0)],
            ["first.jpg"],
            video_source="rpa-temporary-download",
        )

        self.assertEqual(summary["videoSource"], "rpa-temporary-download")
        self.assertNotIn("videoPath", summary)

    def test_formats_integer_fractional_and_submicrosecond_seconds_safely(self):
        self.assertEqual(format_seconds_token(7.0), "0007s")
        self.assertEqual(format_seconds_token(7.5), "0007p5s")
        self.assertEqual(format_seconds_token(0.0000001), "0000p0000001s")
        self.assertEqual(
            format_frame_filename("zero_second", 7.0, 18.0),
            "zero_second_0007s_18.jpg",
        )
        self.assertEqual(
            format_frame_filename("peak_top_1", 7.5, 12.25),
            "peak_top_1_0007p5s_12p25.jpg",
        )

    def test_summary_warns_when_top_peaks_are_missing(self):
        selected = [
            SelectedPeak("zero_second", 0.0, 1.0),
            SelectedPeak("peak_top_1", 3.0, 4.0),
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
            ["2 top peak frame(s) unavailable"],
        )
        self.assertEqual(
            [frame["rankType"] for frame in summary["frames"]],
            ["zero_second", "peak_top_1"],
        )

    def test_summary_rejects_mismatched_peaks_and_paths(self):
        selected = [SelectedPeak("zero_second", 0.0, 5.0)]

        with self.assertRaisesRegex(ValueError, "equal length"):
            build_summary(
                "m-123", "input.json", "video.mp4", selected, []
            )

    def test_summary_stores_portable_frame_filenames(self):
        selected = [SelectedPeak("zero_second", 0.0, 5.0)]
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
            SelectedPeak("zero_second", 0.0, 1.0),
            SelectedPeak("peak_top_1", 3.0, 4.0),
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
            self.assertIn("video_timestamp_0_seconds", markdown)
            self.assertIn(
                "top_3_local_peaks_by_metric_desc_then_time_asc", markdown
            )
            self.assertIn("collapse_to_earliest_second", markdown)
            self.assertIn("boundariesExcluded: true", markdown)
            self.assertIn("请求截帧秒数", markdown)
            self.assertIn("| zero_second | 0 | 1 | 0 | first.jpg |", markdown)
            self.assertLess(
                markdown.index("zero_second"), markdown.index("peak_top_1")
            )
            self.assertIn("2 top peak frame(s) unavailable", markdown)
            self.assertEqual(list(result_dir.glob("*.tmp*")), [])

    def test_refuses_to_write_summaries_for_missing_or_empty_frame(self):
        selected = [SelectedPeak("zero_second", 0.0, 5.0)]
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


class ProcessingPipelineTests(unittest.TestCase):
    def test_process_records_passes_configured_timeout_to_probe_and_frames(self):
        records = [
            click_record(second, value)
            for second, value in enumerate([0, 2, 0, 9, 0, 7, 0, 8, 0])
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "material.mp4"
            video.touch()

            def write_frame(_ffmpeg, _video, _timestamp, output, **_kwargs):
                Path(output).write_bytes(DECODABLE_JPEG)

            with patch(
                "peak_frame_lib.resolve_ffmpeg", return_value="ffmpeg"
            ), patch(
                "peak_frame_lib.probe_video_duration", return_value=9.0
            ) as probe_video, patch(
                "peak_frame_lib.extract_frame", side_effect=write_frame
            ) as extract:
                process_records(
                    records,
                    "records.json",
                    video,
                    root / "output",
                    ffmpeg_timeout_sec=120,
                )

        probe_video.assert_called_once_with("ffmpeg", video, timeout_sec=120)
        self.assertEqual(extract.call_count, 4)
        self.assertEqual(
            [call.kwargs["timeout_sec"] for call in extract.call_args_list],
            [120, 120, 120, 120],
        )


class CliTests(unittest.TestCase):

    def test_rpa_wave_barrier_defaults_enabled_and_can_be_disabled_explicitly(self):
        parser = extract_peak_frames.build_parser()

        self.assertTrue(parser.parse_args([]).rpa_wave_barrier)
        self.assertFalse(
            parser.parse_args(["--no-rpa-wave-barrier"]).rpa_wave_barrier
        )

    def test_rpa_isolation_uses_spawn_on_windows(self):
        with patch.object(extract_peak_frames.os, "name", "nt"):
            self.assertEqual(
                extract_peak_frames._rpa_multiprocessing_context_name(), "spawn"
            )

    def test_rpa_isolation_keeps_fork_on_unix(self):
        with patch.object(extract_peak_frames.os, "name", "posix"):
            self.assertEqual(
                extract_peak_frames._rpa_multiprocessing_context_name(), "fork"
            )

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

    def _run_local_csv(
        self, input_csv, video, output, ffmpeg, diagnosis_records_dir=None
    ):
        command = [
            sys.executable,
            str(ROOT / "scripts" / "extract_peak_frames.py"),
            "--input-csv",
            str(input_csv),
            "--video",
            str(video),
            "--output-dir",
            str(output),
            "--ffmpeg-bin",
            str(ffmpeg),
        ]
        if diagnosis_records_dir:
            command.extend(["--diagnosis-records-dir", str(diagnosis_records_dir)])
        return subprocess.run(
            command,
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

    def _write_batch_result(self, output, material_id):
        result_dir = Path(output) / material_id
        result_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for index in range(4):
            frame_path = result_dir / f"frame-{index}.jpg"
            frame_path.write_bytes(DECODABLE_JPEG)
            frames.append(
                {
                    "rankType": f"peak-{index}",
                    "durationSec": float(index),
                    "metricValue": float(index + 1),
                    "framePath": frame_path.name,
                }
            )
        summary_json = result_dir / "summary.json"
        summary_md = result_dir / "summary.md"
        summary_json.write_text(
            json.dumps(
                {
                    "materialId": material_id,
                    "frames": frames,
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        summary_md.write_text("# summary\n", encoding="utf-8")
        return result_dir, summary_json, summary_md

    def _batch_args(self, top_materials, output, *extra):
        return [
            "--source",
            "rpa",
            "--shop-id",
            "shop-1",
            "--top-materials-json",
            str(top_materials),
            "--date-range-type",
            "CUSTOM",
            "--custom-start-date",
            "2026-06-01",
            "--custom-end-date",
            "2026-06-02",
            "--output-dir",
            str(output),
            *extra,
        ]

    def _top_materials_payload(self, material_ids, **overrides):
        payload = {
            "actualStartDate": "2026-06-01",
            "actualEndDate": "2026-06-02",
            "materialCount": len(material_ids),
            "materials": [
                {"materialId": material_id} for material_id in material_ids
            ],
        }
        payload.update(overrides)
        return payload

    def test_streaming_batch_manifest_records_channel_configuration(self):
        args = extract_peak_frames.build_parser().parse_args([])

        self.assertEqual(args.rpa_concurrency, 3)
        self.assertEqual(args.postprocess_concurrency, 3)

    def test_batch_run_lock_rejects_active_lock_and_releases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".batch-run.lock"
            manifest_path = Path(temp_dir) / "batch-manifest.json"
            first = acquire_run_lock(lock_path, manifest_path)
            try:
                with self.assertRaises(BatchLockError) as raised:
                    acquire_run_lock(lock_path, manifest_path)
                self.assertFalse(raised.exception.stale)
            finally:
                first.release()
            second = acquire_run_lock(lock_path, manifest_path)
            second.release()

    def test_batch_run_lock_reports_stale_lock_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".batch-run.lock"
            manifest_path = Path(temp_dir) / "batch-manifest.json"
            lock_path.write_text(
                json.dumps(
                    {
                        "runId": "old-run",
                        "pid": 999999,
                        "hostname": "test-host",
                        "startedAt": 1,
                        "manifestPath": str(manifest_path),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(BatchLockError) as raised:
                acquire_run_lock(lock_path, manifest_path)
            self.assertTrue(raised.exception.stale)
            self.assertIn("old-run", lock_path.read_text(encoding="utf-8"))

    def test_report_ready_is_marked_only_for_complete_report_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "batch-manifest.json"
            data = root / "final-report-data.json"
            html = root / "final-report.html"
            manifest.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "runId": "run-1",
                        "selectedMaterials": [
                            {
                                "materialId": "m-1",
                                "postprocessStatus": "succeeded",
                            }
                        ],
                        "stageCounts": {"reportReadyCount": 0},
                    }
                ),
                encoding="utf-8",
            )
            data.write_text(json.dumps({"status": "partial"}), encoding="utf-8")
            html.write_text("<html></html>", encoding="utf-8")
            self.assertFalse(mark_report_ready(manifest, data, html))
            report_data = {
                "schemaVersion": 1,
                "status": "complete",
                "runId": "run-1",
                "materials": [{"materialId": "m-1", "highlightFrames": []}],
                "pipelineSummary": {"runId": "run-1"},
            }
            data.write_text(json.dumps(report_data), encoding="utf-8")
            html.write_text(
                '<script type="application/json" id="report-data">'
                + json.dumps(report_data)
                + "</script>",
                encoding="utf-8",
            )
            self.assertTrue(mark_report_ready(manifest, data, html))
            updated = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(updated["stageCounts"]["reportReadyCount"], 1)

    def test_streaming_batch_manifest_records_selected_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])),
                encoding="utf-8",
            )
            records = [
                {
                    "recordType": "material_info",
                    "materialId": "m-1",
                    "videoUrl": "https://video.example/m-1.mp4",
                }
            ]

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                return_value={
                    "materialId": "m-1",
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": "https://video.example/m-1.mp4",
                    "records": records,
                    "diagnosisCsvPath": None,
                },
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda _args, entry: {
                    "result": self._write_batch_result(output, entry["materialId"]),
                    "error": None,
                    "timingsSec": entry["timingsSec"],
                },
            ):
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--target-top-n", "1")
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertTrue(manifest["runId"])
        self.assertEqual(manifest["targetTopN"], 1)
        self.assertEqual(manifest["candidateLimit"], 1)
        self.assertEqual(manifest["rpaConcurrency"], 3)
        self.assertEqual(manifest["postprocessConcurrency"], 3)
        self.assertEqual(
            [item["materialId"] for item in manifest["submittedMaterials"]], ["m-1"]
        )
        self.assertEqual(
            [item["materialId"] for item in manifest["selectedMaterials"]], ["m-1"]
        )
        self.assertEqual(manifest["rejectedMaterials"], [])
        self.assertEqual(manifest["cancelledMaterials"], [])
        self.assertEqual(
            manifest["stageCounts"],
            {
                "rpaSelectedCount": 1,
                "recordsPersistedCount": 1,
                "postprocessSucceededCount": 1,
                "reportReadyCount": 0,
            },
        )

    def _streaming_outcome(self, material_id):
        return {
            "materialId": material_id,
            "rpaAvailable": True,
            "failureReason": None,
            "videoUrl": f"https://video.example/{material_id}.mp4",
            "records": [
                {
                    "recordType": "material_info",
                    "materialId": material_id,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                }
            ],
            "diagnosisCsvPath": None,
        }

    def _streaming_postprocess(self, output, _args, entry):
        return {
            "result": self._write_batch_result(output, entry["materialId"]),
            "error": None,
            "timingsSec": entry["timingsSec"],
        }

    def test_streaming_top9_all_success_submits_only_first_n(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = [f"m-{index}" for index in range(1, 28)]
            top_materials.write_text(
                json.dumps(
                    self._top_materials_payload(
                        material_ids,
                        materials=[
                            {"materialId": material_id, "totalCost": 28 - index}
                            for index, material_id in enumerate(material_ids, start=1)
                        ],
                    )
                ),
                encoding="utf-8",
            )
            submitted = []

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                submitted.append(material_id)
                return self._streaming_outcome(material_id)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "9",
                        "--candidate-top-n",
                        "27",
                    )
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(submitted), 9)
        self.assertEqual(set(submitted), set(material_ids[:9]))
        self.assertEqual(
            [item["materialId"] for item in manifest["submittedMaterials"]],
            material_ids[:9],
        )
        self.assertEqual(
            [item["materialId"] for item in manifest["selectedMaterials"]],
            material_ids[:9],
        )

    def test_streaming_top9_two_rejections_submit_only_rank10_and_rank11(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = [f"m-{index}" for index in range(1, 28)]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )
            submitted = []

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                submitted.append(material_id)
                if material_id in {"m-8", "m-9"}:
                    return {
                        "materialId": material_id,
                        "rpaAvailable": False,
                        "failureReason": "missing_video_url",
                        "videoUrl": None,
                    }
                return self._streaming_outcome(material_id)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "9",
                        "--candidate-top-n",
                        "27",
                    )
                )

        self.assertEqual(result, 0)
        self.assertEqual(len(submitted), 11)
        self.assertEqual(set(submitted), set(material_ids[:11]))
        self.assertNotIn(material_ids[11], submitted)

    def test_streaming_rpa_channel_never_exceeds_three_in_flight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = [f"m-{index}" for index in range(1, 10)]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )
            lock = threading.Lock()
            active = 0
            max_active = 0

            def fetch(**kwargs):
                nonlocal active, max_active
                material_id = kwargs["material_id"]
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
                return self._streaming_outcome(material_id)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "9",
                        "--rpa-concurrency",
                        "3",
                    )
                )

        self.assertEqual(result, 0)
        self.assertEqual(max_active, 3)
        self.assertLessEqual(max_active, 3)

    def test_streaming_defaults_to_rpa_wave_barrier(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = ["m-1", "m-2", "m-3", "m-4"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )
            lock = threading.Lock()
            active = set()
            starts = []

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                with lock:
                    starts.append((material_id, set(active)))
                    active.add(material_id)
                time.sleep(0.03 if material_id in {"m-1", "m-3"} else 0.005)
                with lock:
                    active.remove(material_id)
                return self._streaming_outcome(material_id)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "4",
                        "--candidate-top-n",
                        "4",
                        "--rpa-concurrency",
                        "2",
                    )
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        first_wave = [item for item in starts if item[0] in {"m-1", "m-2"}]
        second_wave = [item for item in starts if item[0] in {"m-3", "m-4"}]
        self.assertEqual({item[0] for item in first_wave}, {"m-1", "m-2"})
        self.assertEqual({item[0] for item in second_wave}, {"m-3", "m-4"})
        self.assertTrue(
            all(not active & {"m-1", "m-2"} for _material_id, active in second_wave)
        )
        self.assertTrue(manifest["waveBarrier"])
        self.assertEqual(
            [item["waveIndex"] for item in manifest["selectedMaterials"]],
            [0, 0, 1, 1],
        )

    def test_streaming_starts_postprocess_before_other_rpa_jobs_finish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = ["m-1", "m-2", "m-3"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )
            postprocess_started = threading.Event()
            release_rpa = threading.Event()
            saw_postprocess_before_release = []

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                if material_id != "m-1":
                    release_rpa.wait(timeout=1)
                return self._streaming_outcome(material_id)

            def postprocess(_args, entry):
                postprocess_started.set()
                return self._streaming_postprocess(output, _args, entry)

            def release_after_delay():
                time.sleep(0.05)
                saw_postprocess_before_release.append(postprocess_started.is_set())
                release_rpa.set()

            releaser = threading.Thread(target=release_after_delay)
            releaser.start()
            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=postprocess,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "3",
                        "--rpa-concurrency",
                        "3",
                    )
                )
            releaser.join()

        self.assertEqual(result, 0)
        self.assertEqual(saw_postprocess_before_release, [True])

    def test_streaming_rebuilds_selected_order_after_out_of_order_rpa(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = ["m-1", "m-2", "m-3", "m-4"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                if material_id == "m-1":
                    time.sleep(0.03)
                elif material_id == "m-2":
                    time.sleep(0.01)
                    return {
                        "materialId": material_id,
                        "rpaAvailable": False,
                        "failureReason": "missing_material_info",
                        "videoUrl": None,
                    }
                return self._streaming_outcome(material_id)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "3",
                        "--rpa-concurrency",
                        "3",
                    )
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )
            ready = json.loads(
                (output / "top_materials_rpa_ready.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            [item["materialId"] for item in manifest["selectedMaterials"]],
            ["m-1", "m-3", "m-4"],
        )
        self.assertEqual(
            [item["materialId"] for item in ready["materials"]],
            ["m-1", "m-3", "m-4"],
        )
        self.assertEqual(
            [item["materialId"] for item in manifest["rejectedMaterials"]], ["m-2"]
        )

    def test_streaming_retains_partial_artifacts_when_candidate_pool_exhausts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            records_dir = root / "diagnosis-records"
            top_materials = root / "top_materials.json"
            material_ids = [f"m-{index}" for index in range(1, 7)]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                if material_id in {"m-1", "m-3"}:
                    return self._streaming_outcome(material_id)
                return {
                    "materialId": material_id,
                    "rpaAvailable": False,
                    "failureReason": "missing_video_url",
                    "videoUrl": None,
                }

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "3",
                        "--candidate-top-n",
                        "6",
                        "--diagnosis-records-dir",
                        str(records_dir),
                    )
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

            self.assertTrue((records_dir / "m-1.json").is_file())
            self.assertTrue((records_dir / "m-3.json").is_file())

        self.assertEqual(result, 5)
        self.assertEqual(
            [item["materialId"] for item in manifest["selectedMaterials"]],
            ["m-1", "m-3"],
        )
        self.assertEqual(len(manifest["rejectedMaterials"]), 4)
        self.assertEqual(manifest["cancelledMaterials"], [])

    def test_streaming_postprocess_failure_is_not_rpa_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = ["m-1", "m-2", "m-3"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )
            submitted = []

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                submitted.append(material_id)
                return self._streaming_outcome(material_id)

            def postprocess(args, entry):
                if entry["materialId"] == "m-1":
                    return {
                        "result": None,
                        "error": InputDataError("video processing failed"),
                        "timingsSec": entry["timingsSec"],
                    }
                return self._streaming_postprocess(output, args, entry)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=postprocess,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--target-top-n", "2")
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 5)
        self.assertEqual(set(submitted), {"m-1", "m-2"})
        first = manifest["selectedMaterials"][0]
        self.assertEqual(first["materialId"], "m-1")
        self.assertEqual(first["rpaStatus"], "selected")
        self.assertEqual(first["postprocessStatus"], "failed")
        self.assertEqual(first["errorType"], "InputDataError")
        self.assertEqual(manifest["rejectedMaterials"], [])

    def test_streaming_resume_reuses_existing_records_and_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            records_dir = root / "diagnosis-records"
            records_dir.mkdir()
            top_materials = root / "top_materials.json"
            material_ids = ["m-1", "m-2", "m-3"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)), encoding="utf-8"
            )
            records_dir.joinpath("m-1.json").write_text(
                json.dumps(self._streaming_outcome("m-1")["records"]),
                encoding="utf-8",
            )
            _result_dir, summary_json, summary_md = self._write_batch_result(
                output, "m-1"
            )
            output.mkdir(exist_ok=True)
            (output / "batch-manifest.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "source": "rpa",
                        "shopId": "shop-1",
                        "targetTopN": 2,
                        "candidateCount": 3,
                        "candidateLimit": 3,
                        "rpaConcurrency": 3,
                        "postprocessConcurrency": 3,
                        "dateRange": {
                            "type": "CUSTOM",
                            "customStartDate": "2026-06-01",
                            "customEndDate": "2026-06-02",
                        },
                        "materialIds": material_ids,
                        "materials": [
                            {
                                **extract_peak_frames._stream_entry(
                                    {"materialId": "m-1"}, 1
                                ),
                                "rpaStatus": "submitted",
                                "status": "running",
                            },
                            extract_peak_frames._stream_entry({"materialId": "m-2"}, 2),
                            extract_peak_frames._stream_entry({"materialId": "m-3"}, 3),
                        ],
                        "submittedMaterials": [],
                        "selectedMaterials": [],
                        "rejectedMaterials": [],
                        "cancelledMaterials": [],
                        "timingsSec": {"totalSec": 1.0},
                    }
                ),
                encoding="utf-8",
            )
            fetched = []

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                fetched.append(material_id)
                return self._streaming_outcome(material_id)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=lambda args, entry: self._streaming_postprocess(
                    output, args, entry
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "2",
                        "--candidate-top-n",
                        "3",
                        "--diagnosis-records-dir",
                        str(records_dir),
                        "--resume",
                    )
                )

            resumed = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertNotIn("m-1", fetched)
        self.assertEqual(fetched, ["m-2"])
        self.assertEqual(
            [item["materialId"] for item in resumed["selectedMaterials"]],
            ["m-1", "m-2"],
        )
        first = resumed["selectedMaterials"][0]
        self.assertEqual(first["diagnosisRecordsPath"], str(records_dir / "m-1.json"))
        self.assertEqual(first["summaryJson"], str(summary_json))
        self.assertEqual(first["summaryMd"], str(summary_md))

    def test_streaming_material_total_timing_includes_rpa_and_postprocess(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])), encoding="utf-8"
            )

            def rpa_attempt(_args, entry):
                updated = dict(entry)
                updated.update(
                    {
                        "rpaStatus": "selected",
                        "postprocessStatus": "queued",
                        "status": "running",
                        "records": self._streaming_outcome("m-1")["records"],
                        "timingsSec": {
                            "rpaFetchSec": 5.0,
                            "diagnosisRecordWriteSec": 0.0,
                            "videoDownloadSec": 0.0,
                            "frameExtractionSec": 0.0,
                            "totalSec": 5.0,
                        },
                    }
                )
                return updated

            def postprocess_attempt(_args, entry):
                timings = dict(entry["timingsSec"])
                timings["videoDownloadSec"] = 0.75
                timings["frameExtractionSec"] = 1.25
                timings["totalSec"] = 2.0
                return {
                    "result": self._write_batch_result(output, entry["materialId"]),
                    "error": None,
                    "timingsSec": timings,
                }

            with patch.object(
                extract_peak_frames, "_attempt_stream_rpa", side_effect=rpa_attempt
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=postprocess_attempt,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--target-top-n", "1")
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        timings = manifest["selectedMaterials"][0]["timingsSec"]
        self.assertEqual(timings["postprocessSec"], 2.0)
        self.assertEqual(timings["endToEndSec"], 7.0)
        self.assertEqual(timings["totalSec"], 7.0)

    def test_rpa_preflight_backfills_after_failed_top_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(
                    self._top_materials_payload(
                        ["m-bad", "m-1", "m-2", "m-3"]
                    )
                ),
                encoding="utf-8",
            )
            preflighted = []
            processed = []

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                preflighted.append(material_id)
                return {
                    "materialId": material_id,
                    "rpaAvailable": material_id != "m-bad",
                    "failureReason": (
                        "missing_material_info" if material_id == "m-bad" else None
                    ),
                    "videoUrl": (
                        None
                        if material_id == "m-bad"
                        else f"https://video.example/{material_id}.mp4"
                    ),
                }

            def process_material(_args, entry):
                material_id = entry["materialId"]
                processed.append(material_id)
                return {
                    "result": self._write_batch_result(output, material_id),
                    "error": None,
                    "timingsSec": entry["timingsSec"],
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
                create=True,
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=process_material,
            ):
                try:
                    result = extract_peak_frames.main(
                        self._batch_args(
                            top_materials,
                            output,
                            "--target-top-n",
                            "3",
                        )
                    )
                except SystemExit as exc:
                    result = exc.code

            ready_payload = json.loads(
                (output / "top_materials_rpa_ready.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(set(preflighted), {"m-bad", "m-1", "m-2", "m-3"})
        self.assertEqual(set(processed), {"m-1", "m-2", "m-3"})
        self.assertEqual(
            [item["materialId"] for item in ready_payload["materials"]],
            ["m-1", "m-2", "m-3"],
        )

    def test_rpa_preflight_stops_before_formal_batch_when_candidates_exhausted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-bad", "m-1"])),
                encoding="utf-8",
            )

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                return {
                    "materialId": material_id,
                    "rpaAvailable": material_id == "m-1",
                    "failureReason": (
                        None if material_id == "m-1" else "missing_material_info"
                    ),
                    "videoUrl": (
                        f"https://video.example/{material_id}.mp4"
                        if material_id == "m-1"
                        else None
                    ),
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ), patch.object(
                extract_peak_frames,
                "_process_rpa_material",
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "3",
                        "--preflight-only",
                    )
                )

            preflight_receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 4)
        process_material.assert_not_called()
        self.assertFalse((output / "batch-manifest.json").exists())
        self.assertFalse(preflight_receipt["satisfied"])
        self.assertEqual(preflight_receipt["requestedTopN"], 3)
        self.assertEqual(
            [item["materialId"] for item in preflight_receipt["selectedMaterials"]],
            ["m-1"],
        )

    def test_rpa_preflight_writes_incremental_receipt_before_interrupt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-bad", "m-hangs"])),
                encoding="utf-8",
            )

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                if material_id == "m-hangs":
                    raise KeyboardInterrupt()
                return {
                    "materialId": material_id,
                    "rpaAvailable": False,
                    "failureReason": "rpa_request_failed",
                    "videoUrl": None,
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ), self.assertRaises(KeyboardInterrupt):
                extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "1",
                        "--preflight-only",
                        "--rpa-concurrency",
                        "1",
                        "--rpa-late-artifact-grace-sec",
                        "0.001",
                    )
                )

            receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )
        self.assertFalse(receipt["satisfied"])
        self.assertEqual(receipt["candidateCount"], 2)
        self.assertEqual(receipt["selectedMaterials"], [])
        self.assertEqual(
            [(item["materialId"], item["failureReason"]) for item in receipt["rejectedMaterials"]],
            [("m-bad", "rpa_request_failed")],
        )

    def test_rpa_preflight_deadline_rejects_hanging_material_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-hangs", "m-ready"])),
                encoding="utf-8",
            )

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                if material_id == "m-hangs":
                    time.sleep(1)
                return {
                    "materialId": material_id,
                    "rpaAvailable": material_id == "m-ready",
                    "failureReason": None if material_id == "m-ready" else "unused",
                    "videoUrl": (
                        "https://video.example/m-ready.mp4"
                        if material_id == "m-ready"
                        else None
                    ),
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ), patch.object(
                extract_peak_frames,
                "_process_rpa_material",
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "1",
                        "--preflight-only",
                        "--rpa-preflight-timeout-sec",
                        "0.01",
                    )
                )

            receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result, 0)
        process_material.assert_not_called()
        self.assertEqual(
            [(item["materialId"], item["failureReason"]) for item in receipt["rejectedMaterials"]],
            [("m-hangs", "rpa_request_failed")],
        )
        self.assertEqual(
            [item["materialId"] for item in receipt["selectedMaterials"]],
            ["m-ready"],
        )

    def test_isolated_rpa_preflight_kills_worker_after_deadline(self):
        def hanging_worker(_params):
            time.sleep(1)
            return {
                "materialId": "m-hangs",
                "rpaAvailable": True,
                "failureReason": None,
                "videoUrl": "https://video.example/m-hangs.mp4",
            }

        result = extract_peak_frames._fetch_material_preflight_isolated(
            {"material_id": "m-hangs"},
            timeout_sec=0.01,
            worker=hanging_worker,
        )

        self.assertEqual(
            result,
            {
                "materialId": "m-hangs",
                "rpaAvailable": False,
                "failureReason": "rpa_request_failed",
                "videoUrl": None,
            },
        )

    def test_isolated_rpa_preflight_persists_large_records_without_queue_deadlock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            records_dir = Path(temp_dir) / "diagnosis-records"
            records = [
                {
                    "recordType": "material_info",
                    "materialId": "m-big",
                    "videoUrl": "https://video.example/m-big.mp4",
                }
            ] + [
                click_record(index, index % 100, material_id="m-big")
                for index in range(20000)
            ]

            def large_records_worker(_params):
                return {
                    "materialId": "m-big",
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": "https://video.example/m-big.mp4",
                    "records": records,
                    "diagnosisCsvPath": str(Path(temp_dir) / "m-big.csv"),
                }

            result = extract_peak_frames._fetch_material_preflight_isolated(
                {
                    "material_id": "m-big",
                    "diagnosis_records_dir": records_dir,
                },
                timeout_sec=0.5,
                worker=large_records_worker,
            )

            retained = json.loads(
                (records_dir / "m-big.json").read_text(encoding="utf-8")
            )

        self.assertTrue(result["rpaAvailable"])
        self.assertNotIn("records", result)
        self.assertEqual(
            result["diagnosisRecordsPath"], str(records_dir / "m-big.json")
        )
        self.assertEqual(retained, records)

    def test_preflight_worker_does_not_pass_internal_records_dir_to_adapter(self):
        calls = []

        def strict_fetch_material_preflight(
            *,
            shop_id,
            material_id,
            date_range_type,
            custom_start_date=None,
            custom_end_date=None,
            diagnosis_csv_dir=None,
            timeout_sec=300,
        ):
            calls.append(
                {
                    "shop_id": shop_id,
                    "material_id": material_id,
                    "date_range_type": date_range_type,
                    "custom_start_date": custom_start_date,
                    "custom_end_date": custom_end_date,
                    "diagnosis_csv_dir": diagnosis_csv_dir,
                    "timeout_sec": timeout_sec,
                }
            )
            return {
                "materialId": material_id,
                "rpaAvailable": False,
                "failureReason": "missing_material_info",
                "videoUrl": None,
            }

        with patch.object(
            extract_peak_frames,
            "fetch_material_preflight",
            side_effect=strict_fetch_material_preflight,
        ):
            result = extract_peak_frames._fetch_material_preflight_worker(
                {
                    "shop_id": "shop-1",
                    "material_id": "m-1",
                    "date_range_type": "CUSTOM",
                    "custom_start_date": "2026-06-01",
                    "custom_end_date": "2026-06-02",
                    "diagnosis_csv_dir": "/tmp/csv",
                    "diagnosis_records_dir": "/tmp/records",
                    "timeout_sec": 123,
                }
            )

        self.assertEqual(result["failureReason"], "missing_material_info")
        self.assertEqual(calls[0]["material_id"], "m-1")

    def test_streaming_replenishes_rpa_slots_before_postprocess_finishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = [f"m-{index}" for index in range(1, 10)]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)),
                encoding="utf-8",
            )
            submitted = []
            postprocess_started = threading.Event()
            all_target_submitted_while_postprocessing = threading.Event()

            def fetch(**kwargs):
                material_id = kwargs["material_id"]
                submitted.append(material_id)
                if (
                    postprocess_started.is_set()
                    and set(submitted) >= set(material_ids[:6])
                ):
                    all_target_submitted_while_postprocessing.set()
                return self._streaming_outcome(material_id)

            def postprocess(args, entry):
                postprocess_started.set()
                all_target_submitted_while_postprocessing.wait(1)
                return self._streaming_postprocess(output, args, entry)

            with patch.object(
                extract_peak_frames, "fetch_material_preflight", side_effect=fetch
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=postprocess,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "6",
                        "--candidate-top-n",
                        "9",
                        "--rpa-concurrency",
                        "3",
                        "--postprocess-concurrency",
                        "1",
                    )
                )

        self.assertEqual(result, 0)
        self.assertTrue(all_target_submitted_while_postprocessing.is_set())
        self.assertEqual(set(submitted), set(material_ids[:6]))

    def test_rpa_formal_batch_consumes_only_ready_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(
                    self._top_materials_payload(
                        ["m-bad", "m-1", "m-2", "m-unused"]
                    )
                ),
                encoding="utf-8",
            )
            processed = []

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                available = material_id != "m-bad"
                return {
                    "materialId": material_id,
                    "rpaAvailable": available,
                    "failureReason": None if available else "missing_video_url",
                    "videoUrl": (
                        f"https://video.example/{material_id}.mp4"
                        if available
                        else None
                    ),
                }

            def process_material(_args, entry):
                material_id = entry["materialId"]
                processed.append(material_id)
                return {
                    "result": self._write_batch_result(output, material_id),
                    "error": None,
                    "timingsSec": entry["timingsSec"],
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ), patch.object(
                extract_peak_frames,
                "_attempt_stream_postprocess",
                side_effect=process_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "2",
                    )
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(set(processed), {"m-1", "m-2"})
        self.assertEqual(manifest["materialIds"], ["m-bad", "m-1", "m-2", "m-unused"])
        self.assertEqual(
            [item["materialId"] for item in manifest["selectedMaterials"]],
            ["m-1", "m-2"],
        )
        self.assertEqual(
            [item["materialId"] for item in manifest["cancelledMaterials"]],
            ["m-unused"],
        )

    def test_rpa_preflight_writes_preflight_receipt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-bad", "m-ready"])),
                encoding="utf-8",
            )

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                available = material_id == "m-ready"
                return {
                    "materialId": material_id,
                    "rpaAvailable": available,
                    "failureReason": None if available else "missing_material_info",
                    "videoUrl": (
                        "https://video.example/m-ready.mp4" if available else None
                    ),
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ), patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "1",
                        "--preflight-only",
                    )
                )

            receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            sorted(receipt),
            [
                "cancelledMaterials",
                "candidateCount",
                "candidateLimit",
                "dateRange",
                "preflightConcurrency",
                "rejectedMaterials",
                "requestedTopN",
                "satisfied",
                "schemaVersion",
                "selectedMaterials",
                "shopId",
                "stoppedAfterSatisfied",
                "timingsSec",
                "waveBarrier",
            ],
        )
        self.assertEqual(receipt["requestedTopN"], 1)
        self.assertEqual(receipt["candidateCount"], 2)
        self.assertEqual(receipt["shopId"], "shop-1")
        self.assertEqual(
            receipt["dateRange"],
            {
                "type": "CUSTOM",
                "customStartDate": "2026-06-01",
                "customEndDate": "2026-06-02",
                "actualStartDate": "2026-06-01",
                "actualEndDate": "2026-06-02",
            },
        )
        self.assertTrue(receipt["satisfied"])
        self.assertEqual(
            [item["materialId"] for item in receipt["selectedMaterials"]],
            ["m-ready"],
        )
        self.assertEqual(receipt["rejectedMaterials"][0]["materialId"], "m-bad")
        self.assertEqual(
            receipt["rejectedMaterials"][0]["failureReason"],
            "missing_material_info",
        )

    def test_rpa_preflight_writes_ready_top_materials_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            candidate_payload = self._top_materials_payload(
                ["m-bad", "m-1", "m-2"],
                sourceTable="asset-table",
                filters={"campaign": "summer"},
                materials=[
                    {"materialId": "m-bad", "rank": 1, "cost": 99.0},
                    {"materialId": "m-1", "rank": 2, "cost": 80.0},
                    {"materialId": "m-2", "rank": 3, "cost": 70.0},
                ],
            )
            top_materials.write_text(
                json.dumps(candidate_payload),
                encoding="utf-8",
            )

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                available = material_id != "m-bad"
                return {
                    "materialId": material_id,
                    "rpaAvailable": available,
                    "failureReason": None if available else "missing_video_url",
                    "videoUrl": (
                        f"https://video.example/{material_id}.mp4"
                        if available
                        else None
                    ),
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ), patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "2",
                        "--preflight-only",
                    )
                )

            ready_payload = json.loads(
                (output / "top_materials_rpa_ready.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(ready_payload["actualStartDate"], "2026-06-01")
        self.assertEqual(ready_payload["actualEndDate"], "2026-06-02")
        self.assertEqual(ready_payload["sourceTable"], "asset-table")
        self.assertEqual(ready_payload["filters"], {"campaign": "summer"})
        self.assertEqual(ready_payload["materialCount"], 2)
        self.assertEqual(
            ready_payload["materials"],
            [
                {"materialId": "m-1", "rank": 2, "cost": 80.0},
                {"materialId": "m-2", "rank": 3, "cost": 70.0},
            ],
        )

    def test_rpa_preflight_persists_reusable_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            records_dir = root / "diagnosis-records"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-ready"])),
                encoding="utf-8",
            )
            records = [{
                "recordType": "material_info",
                "materialId": "m-ready",
                "videoUrl": "https://video.example/m-ready.mp4",
            }]

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                return_value={
                    "materialId": "m-ready",
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": "https://video.example/m-ready.mp4",
                    "records": records,
                    "diagnosisCsvPath": None,
                },
            ), patch.object(
                extract_peak_frames, "_process_rpa_material"
            ) as process_material, patch.object(
                extract_peak_frames, "download_video"
            ) as download, patch.object(
                extract_peak_frames, "process_records"
            ) as process_records:
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n", "1", "--preflight-only",
                        "--diagnosis-records-dir", str(records_dir),
                    )
                )

            receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )
            saved_records = json.loads(
                (records_dir / "m-ready.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(receipt["schemaVersion"], 2)
        self.assertEqual(receipt["preflightConcurrency"], 3)
        selected = receipt["selectedMaterials"][0]
        self.assertEqual(selected["diagnosisRecordsPath"], "diagnosis-records/m-ready.json")
        self.assertEqual(selected["preflightStatus"], "selected")
        self.assertIn("timingsSec", selected)
        self.assertEqual(saved_records, records)
        process_material.assert_not_called()
        download.assert_not_called()
        process_records.assert_not_called()

    def test_rpa_preflight_parallelism_keeps_candidate_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-a", "m-b", "m-c"])),
                encoding="utf-8",
            )
            lock = threading.Lock()
            active = 0
            max_active = 0

            def preflight_material(**kwargs):
                nonlocal active, max_active
                material_id = kwargs["material_id"]
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.03 if material_id == "m-a" else 0.01)
                with lock:
                    active -= 1
                return {
                    "materialId": material_id,
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                    "records": [{
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://video.example/{material_id}.mp4",
                    }],
                    "diagnosisCsvPath": None,
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n", "2", "--preflight-only",
                        "--concurrency", "2",
                    )
                )

            receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )
            ready = json.loads(
                (output / "top_materials_rpa_ready.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result, 0)
        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 2)
        self.assertEqual(
            [item["materialId"] for item in receipt["selectedMaterials"]],
            ["m-a", "m-b"],
        )
        self.assertEqual(
            [item["materialId"] for item in ready["materials"]],
            ["m-a", "m-b"],
        )

    def test_rpa_preflight_parallelism_does_not_submit_beyond_in_flight_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = ["m-1", "m-2", "m-3", "m-4"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)),
                encoding="utf-8",
            )
            submitted = []

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                submitted.append(material_id)
                return {
                    "materialId": material_id,
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                    "records": [{
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://video.example/{material_id}.mp4",
                    }],
                    "diagnosisCsvPath": None,
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n", "2", "--preflight-only",
                        "--candidate-top-n", "4",
                        "--concurrency", "3",
                    )
                )

            receipt = json.loads(
                (output / "rpa-preflight.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(set(submitted), {"m-1", "m-2"})
        self.assertEqual(len(submitted), 2)
        self.assertEqual(
            [item["materialId"] for item in receipt["selectedMaterials"]],
            ["m-1", "m-2"],
        )
        self.assertEqual(
            [item["materialId"] for item in receipt["cancelledMaterials"]],
            ["m-3", "m-4"],
        )

    def test_rpa_preflight_defaults_to_wave_barrier(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1", "m-2", "m-3", "m-4"])),
                encoding="utf-8",
            )
            lock = threading.Lock()
            active = set()
            starts = []

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                with lock:
                    starts.append((material_id, set(active)))
                    active.add(material_id)
                time.sleep(0.03 if material_id in {"m-1", "m-3"} else 0.005)
                with lock:
                    active.remove(material_id)
                return {
                    "materialId": material_id,
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                    "records": [{
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://video.example/{material_id}.mp4",
                    }],
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n", "4", "--preflight-only",
                        "--candidate-top-n", "4",
                    )
                )
                receipt = json.loads(
                    (output / "rpa-preflight.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, 0)
        first_wave = [item for item in starts if item[0] in {"m-1", "m-2", "m-3"}]
        second_wave = [item for item in starts if item[0] == "m-4"]
        self.assertEqual({item[0] for item in first_wave}, {"m-1", "m-2", "m-3"})
        self.assertEqual(second_wave, [("m-4", set())])
        self.assertTrue(receipt["waveBarrier"])
        self.assertEqual(
            [item["waveIndex"] for item in receipt["selectedMaterials"]], [0, 0, 0, 1]
        )
        self.assertEqual(
            [item["waveSize"] for item in receipt["selectedMaterials"]], [3, 3, 3, 1]
        )

    def test_rpa_preflight_concurrency_one_records_single_item_waves(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1", "m-2"])),
                encoding="utf-8",
            )

            def preflight_material(**kwargs):
                material_id = kwargs["material_id"]
                return {
                    "materialId": material_id,
                    "rpaAvailable": True,
                    "failureReason": None,
                    "videoUrl": f"https://video.example/{material_id}.mp4",
                    "records": [{
                        "recordType": "material_info",
                        "materialId": material_id,
                        "videoUrl": f"https://video.example/{material_id}.mp4",
                    }],
                }

            with patch.object(
                extract_peak_frames,
                "fetch_material_preflight",
                side_effect=preflight_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n", "2", "--preflight-only",
                        "--candidate-top-n", "2",
                        "--rpa-concurrency", "1",
                    )
                )
                receipt = json.loads(
                    (output / "rpa-preflight.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, 0)
        self.assertEqual(
            [item["waveIndex"] for item in receipt["selectedMaterials"]], [0, 1]
        )
        self.assertEqual(
            [item["waveSize"] for item in receipt["selectedMaterials"]], [1, 1]
        )
        self.assertTrue(all(
            item["waveFinishedAtSec"] >= item["waveStartedAtSec"]
            for item in receipt["selectedMaterials"]
        ))

    def test_rpa_batch_rejects_exported_dates_that_differ_from_custom_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(
                    self._top_materials_payload(
                        ["m-1"], actualStartDate="2026-05-31"
                    )
                ),
                encoding="utf-8",
            )
            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output)
                )

        self.assertEqual(result, 4)
        process_material.assert_not_called()

    def test_rpa_batch_verify_writes_receipt_without_processing_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])),
                encoding="utf-8",
            )
            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                self.assertEqual(
                    extract_peak_frames.main(self._batch_args(top_materials, output)),
                    0,
                )

            with patch.object(
                extract_peak_frames, "_process_rpa_material"
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--verify")
                )
            receipt = json.loads(
                (output / "batch-verification.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        process_material.assert_not_called()
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["materialCount"], 1)
        self.assertEqual(receipt["warningCount"], 0)

    def test_streaming_batch_verify_accepts_schema_v2_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials_rpa_ready.json"
            material_ids = ["m-1", "m-3"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)),
                encoding="utf-8",
            )
            selected = []
            for rank, material_id in enumerate(material_ids, start=1):
                _result_dir, summary_json, summary_md = self._write_batch_result(
                    output, material_id
                )
                selected.append(
                    {
                        "rank": rank,
                        "materialId": material_id,
                        "rpaStatus": "selected",
                        "postprocessStatus": "succeeded",
                        "status": "succeeded",
                        "summaryJson": str(summary_json),
                        "summaryMd": str(summary_md),
                    }
                )
            manifest = {
                "schemaVersion": 2,
                "source": "rpa",
                "shopId": "shop-1",
                "targetTopN": 2,
                "candidateCount": 3,
                "candidateLimit": 3,
                "rpaConcurrency": 3,
                "postprocessConcurrency": 3,
                "dateRange": {
                    "type": "CUSTOM",
                    "customStartDate": "2026-06-01",
                    "customEndDate": "2026-06-02",
                },
                "materialIds": ["m-1", "m-2", "m-3"],
                "submittedMaterials": selected,
                "selectedMaterials": selected,
                "rejectedMaterials": [
                    {
                        "rank": 2,
                        "materialId": "m-2",
                        "rpaStatus": "rejected",
                        "failureReason": "rpa_request_failed",
                    }
                ],
                "cancelledMaterials": [],
                "timingsSec": {"totalSec": 12.0},
            }
            (output / "batch-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with patch.object(
                extract_peak_frames, "_process_rpa_material"
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--verify")
                )
            receipt = json.loads(
                (output / "batch-verification.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        process_material.assert_not_called()
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["schemaVersion"], 2)
        self.assertEqual(receipt["manifestSchemaVersion"], 2)
        self.assertEqual(receipt["materialCount"], 2)
        self.assertEqual(receipt["rejectedCount"], 1)

    def test_streaming_batch_verify_allows_completed_manifest_with_target_top_n(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials_rpa_ready.json"
            material_ids = ["m-1", "m-3"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)),
                encoding="utf-8",
            )
            selected = []
            for rank, material_id in enumerate(material_ids, start=1):
                _result_dir, summary_json, summary_md = self._write_batch_result(
                    output, material_id
                )
                selected.append(
                    {
                        "rank": rank,
                        "materialId": material_id,
                        "rpaStatus": "selected",
                        "postprocessStatus": "succeeded",
                        "status": "succeeded",
                        "summaryJson": str(summary_json),
                        "summaryMd": str(summary_md),
                    }
                )
            manifest = {
                "schemaVersion": 2,
                "source": "rpa",
                "shopId": "shop-1",
                "targetTopN": 2,
                "candidateCount": 3,
                "candidateLimit": 3,
                "rpaConcurrency": 3,
                "postprocessConcurrency": 3,
                "dateRange": {
                    "type": "CUSTOM",
                    "customStartDate": "2026-06-01",
                    "customEndDate": "2026-06-02",
                },
                "materialIds": ["m-1", "m-2", "m-3"],
                "submittedMaterials": selected,
                "selectedMaterials": selected,
                "rejectedMaterials": [
                    {
                        "rank": 2,
                        "materialId": "m-2",
                        "rpaStatus": "rejected",
                        "failureReason": "rpa_request_failed",
                    }
                ],
                "cancelledMaterials": [],
                "timingsSec": {"totalSec": 12.0},
            }
            (output / "batch-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with patch.object(
                extract_peak_frames, "_process_rpa_material"
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "2",
                        "--candidate-top-n",
                        "3",
                        "--verify",
                    )
                )
            receipt = json.loads(
                (output / "batch-verification.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        process_material.assert_not_called()
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["schemaVersion"], 2)
        self.assertEqual(receipt["manifestSchemaVersion"], 2)
        self.assertEqual(receipt["materialCount"], 2)
        self.assertEqual(receipt["rejectedCount"], 1)

    def test_streaming_batch_verify_accepts_candidate_top_materials_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            candidate_ids = ["m-1", "m-2", "m-3"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(candidate_ids)),
                encoding="utf-8",
            )
            selected = []
            for rank, material_id in enumerate(["m-1", "m-3"], start=1):
                _result_dir, summary_json, summary_md = self._write_batch_result(
                    output, material_id
                )
                selected.append(
                    {
                        "rank": rank,
                        "materialId": material_id,
                        "rpaStatus": "selected",
                        "postprocessStatus": "succeeded",
                        "status": "succeeded",
                        "summaryJson": str(summary_json),
                        "summaryMd": str(summary_md),
                    }
                )
            manifest = {
                "schemaVersion": 2,
                "source": "rpa",
                "shopId": "shop-1",
                "targetTopN": 2,
                "candidateCount": 3,
                "candidateLimit": 3,
                "rpaConcurrency": 3,
                "postprocessConcurrency": 3,
                "dateRange": {
                    "type": "CUSTOM",
                    "customStartDate": "2026-06-01",
                    "customEndDate": "2026-06-02",
                },
                "materialIds": candidate_ids,
                "submittedMaterials": selected,
                "selectedMaterials": selected,
                "rejectedMaterials": [],
                "cancelledMaterials": [
                    {
                        "rank": 2,
                        "materialId": "m-2",
                        "rpaStatus": "cancelled",
                        "postprocessStatus": "not_started",
                        "status": "cancelled",
                        "failureReason": "target_satisfied",
                    }
                ],
                "timingsSec": {"totalSec": 12.0},
            }
            (output / "batch-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with patch.object(
                extract_peak_frames, "_process_rpa_material"
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--target-top-n",
                        "2",
                        "--candidate-top-n",
                        "3",
                        "--verify",
                    )
                )
            receipt = json.loads(
                (output / "batch-verification.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        process_material.assert_not_called()
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["materialCount"], 2)
        self.assertEqual(receipt["selectedCount"], 2)
        self.assertEqual(receipt["cancelledCount"], 1)

    def test_streaming_batch_verify_infers_target_top_n_from_manifest_for_candidate_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            candidate_ids = ["m-1", "m-2", "m-3", "m-4"]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(candidate_ids)),
                encoding="utf-8",
            )
            selected = []
            for rank, material_id in enumerate(["m-1", "m-3"], start=1):
                _result_dir, summary_json, summary_md = self._write_batch_result(
                    output, material_id
                )
                selected.append(
                    {
                        "rank": rank,
                        "materialId": material_id,
                        "rpaStatus": "selected",
                        "postprocessStatus": "succeeded",
                        "status": "succeeded",
                        "summaryJson": str(summary_json),
                        "summaryMd": str(summary_md),
                    }
                )
            manifest = {
                "schemaVersion": 2,
                "source": "rpa",
                "shopId": "shop-1",
                "targetTopN": 2,
                "candidateCount": 4,
                "candidateLimit": 4,
                "rpaConcurrency": 3,
                "postprocessConcurrency": 3,
                "dateRange": {
                    "type": "CUSTOM",
                    "customStartDate": "2026-06-01",
                    "customEndDate": "2026-06-02",
                },
                "materialIds": candidate_ids,
                "submittedMaterials": selected,
                "selectedMaterials": selected,
                "rejectedMaterials": [
                    {
                        "rank": 2,
                        "materialId": "m-2",
                        "rpaStatus": "rejected",
                        "postprocessStatus": "pending",
                        "status": "failed",
                        "failureReason": "missing_video_url",
                    }
                ],
                "cancelledMaterials": [
                    {
                        "rank": 4,
                        "materialId": "m-4",
                        "rpaStatus": "cancelled",
                        "postprocessStatus": "not_started",
                        "status": "cancelled",
                        "failureReason": "target_satisfied",
                    }
                ],
                "timingsSec": {"totalSec": 12.0},
            }
            (output / "batch-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with patch.object(
                extract_peak_frames, "_process_rpa_material"
            ) as process_material:
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--verify")
                )
            receipt = json.loads(
                (output / "batch-verification.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        process_material.assert_not_called()
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["materialCount"], 2)
        self.assertEqual(receipt["materialIds"], ["m-1", "m-3"])
        self.assertEqual(receipt["rejectedCount"], 1)
        self.assertEqual(receipt["cancelledCount"], 1)

    def test_rpa_batch_verify_rejects_corrupt_retained_jpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])),
                encoding="utf-8",
            )
            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                self.assertEqual(
                    extract_peak_frames.main(self._batch_args(top_materials, output)),
                    0,
                )
            (output / "m-1" / "frame-0.jpg").write_bytes(b"not-a-jpeg")

            result = extract_peak_frames.main(
                self._batch_args(top_materials, output, "--verify")
            )
            receipt = json.loads(
                (output / "batch-verification.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 6)
        self.assertFalse(receipt["valid"])
        self.assertIn("not a structurally valid JPEG", receipt["errors"][0])

    def test_rpa_batch_continues_after_material_failure_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1", "m-2", "m-3"])),
                encoding="utf-8",
            )
            processed = []

            def process_material(_args, material_id):
                processed.append(material_id)
                if material_id == "m-2":
                    raise FfmpegError("timed out")
                return self._write_batch_result(output, material_id)

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=process_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output)
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 5)
        self.assertEqual(processed, ["m-1", "m-2", "m-3"])
        self.assertEqual(
            [manifest["materials"][material_id]["status"] for material_id in manifest["materialIds"]],
            ["succeeded", "failed", "succeeded"],
        )
        self.assertEqual(manifest["materials"]["m-2"]["errorType"], "FfmpegError")

    def test_rpa_batch_records_material_timing_segments_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])),
                encoding="utf-8",
            )

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                result = extract_peak_frames.main(
                    self._batch_args(top_materials, output)
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        timings = manifest["materials"]["m-1"]["timingsSec"]
        self.assertEqual(
            sorted(timings),
            [
                "diagnosisRecordWriteSec",
                "frameExtractionSec",
                "rpaFetchSec",
                "totalSec",
                "videoDownloadSec",
            ],
        )
        self.assertGreaterEqual(timings["totalSec"], 0)

    def test_rpa_batch_concurrency_limits_parallel_material_processing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            material_ids = [f"m-{index}" for index in range(6)]
            top_materials.write_text(
                json.dumps(self._top_materials_payload(material_ids)),
                encoding="utf-8",
            )
            lock = threading.Lock()
            active = 0
            max_active = 0
            processed = []

            def process_material(_args, material_id):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                    processed.append(material_id)
                time.sleep(0.02)
                with lock:
                    active -= 1
                return self._write_batch_result(output, material_id)

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=process_material,
            ):
                result = extract_peak_frames.main(
                    self._batch_args(
                        top_materials,
                        output,
                        "--concurrency",
                        "3",
                    )
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(sorted(processed), material_ids)
        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 3)
        self.assertEqual(
            [manifest["materials"][material_id]["status"] for material_id in material_ids],
            ["succeeded"] * len(material_ids),
        )

    def test_rpa_batch_resume_retries_failed_material_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1", "m-2", "m-3"])),
                encoding="utf-8",
            )

            def first_attempt(_args, material_id):
                if material_id == "m-2":
                    raise FfmpegError("timed out")
                return self._write_batch_result(output, material_id)

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=first_attempt,
            ):
                first_result = extract_peak_frames.main(
                    self._batch_args(top_materials, output)
                )

            resumed = []

            def resumed_attempt(_args, material_id):
                resumed.append(material_id)
                return self._write_batch_result(output, material_id)

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=resumed_attempt,
            ):
                resumed_result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--resume")
                )

            manifest = json.loads(
                (output / "batch-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(first_result, 5)
        self.assertEqual(resumed_result, 0)
        self.assertEqual(resumed, ["m-2"])
        self.assertEqual(
            [manifest["materials"][material_id]["status"] for material_id in manifest["materialIds"]],
            ["succeeded", "succeeded", "succeeded"],
        )

    def test_rpa_batch_resume_reprocesses_invalid_retained_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])),
                encoding="utf-8",
            )

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                first_result = extract_peak_frames.main(
                    self._batch_args(top_materials, output)
                )

            (output / "m-1" / "frame-0.jpg").unlink()
            resumed = []

            def resumed_attempt(_args, material_id):
                resumed.append(material_id)
                return self._write_batch_result(output, material_id)

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=resumed_attempt,
            ):
                resumed_result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--resume")
                )

        self.assertEqual(first_result, 0)
        self.assertEqual(resumed_result, 0)
        self.assertEqual(resumed, ["m-1"])

    def test_rpa_batch_resume_rejects_changed_material_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            top_materials = root / "top_materials.json"
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-1"])),
                encoding="utf-8",
            )

            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
                side_effect=lambda _args, material_id: self._write_batch_result(
                    output, material_id
                ),
            ):
                self.assertEqual(
                    extract_peak_frames.main(self._batch_args(top_materials, output)),
                    0,
                )

            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-2"])),
                encoding="utf-8",
            )
            with patch.object(
                extract_peak_frames,
                "_process_rpa_material",
            ) as process_material:
                resumed_result = extract_peak_frames.main(
                    self._batch_args(top_materials, output, "--resume")
                )

        self.assertEqual(resumed_result, 4)
        process_material.assert_not_called()

    def test_cli_passes_ffmpeg_timeout_to_local_pipeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_dir = root / "output" / "m-123"
            summary_json = result_dir / "summary.json"
            summary_md = result_dir / "summary.md"
            with patch.object(
                extract_peak_frames,
                "process_local",
                return_value=(result_dir, summary_json, summary_md),
            ) as process_local:
                result = extract_peak_frames.main(
                    [
                        "--input-json",
                        str(root / "input.json"),
                        "--video",
                        str(root / "video.mp4"),
                        "--output-dir",
                        str(root / "output"),
                        "--ffmpeg-timeout-sec",
                        "75",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertEqual(process_local.call_args.kwargs["ffmpeg_timeout_sec"], 75.0)

    def test_rpa_preflight_defaults_to_gateway_hard_timeout_and_soft_warning(self):
        args = extract_peak_frames.build_parser().parse_args([])

        self.assertEqual(args.rpa_preflight_timeout_sec, 3600)
        self.assertEqual(args.rpa_preflight_soft_timeout_sec, 600)
        self.assertEqual(args.rpa_late_artifact_grace_sec, 30)

    def test_isolated_rpa_preflight_allows_success_after_soft_timeout(self):
        def slow_success_worker(_params):
            time.sleep(0.05)
            return {
                "materialId": "m-slow-success",
                "rpaAvailable": True,
                "failureReason": None,
                "videoUrl": "https://video.example/m-slow-success.mp4",
            }

        result = extract_peak_frames._fetch_material_preflight_isolated(
            {"material_id": "m-slow-success"},
            timeout_sec=0.2,
            soft_timeout_sec=0.01,
            worker=slow_success_worker,
        )

        self.assertTrue(result["rpaAvailable"])
        self.assertTrue(result["softTimeoutExceeded"])

    def test_streaming_rpa_recovers_failed_request_from_late_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            records_dir = root / "diagnosis-records"
            csv_dir = root / "diagnosis-csv"
            csv_dir.mkdir(parents=True)
            material_id = "m-late"
            csv_path = csv_dir / f"{material_id}.csv"
            csv_path.write_text(
                "recordType\x01materialId\x01videoUrl\n"
                f"material_info\x01{material_id}\x01https://video.example/{material_id}.mp4\n",
                encoding="utf-8",
            )
            args = type("Args", (), {
                "output_dir": str(output),
                "diagnosis_records_dir": str(records_dir),
            })()
            args.rpa_late_artifact_grace_sec = 0
            failed = {
                "materialId": material_id,
                "rpaAvailable": False,
                "failureReason": "rpa_request_failed",
                "videoUrl": None,
            }

            recovered = extract_peak_frames._recover_late_rpa_artifacts(
                args, material_id, failed
            )

            self.assertTrue(recovered["rpaAvailable"])
            self.assertTrue(recovered["lateArtifactRecovered"])
            self.assertEqual(recovered["diagnosisCsvPath"], str(csv_path))
            self.assertTrue((records_dir / f"{material_id}.json").is_file())

    def test_preflight_path_recovers_failed_request_from_late_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_dir = root / "diagnosis-csv"
            records_dir = root / "diagnosis-records"
            csv_dir.mkdir(parents=True)
            material_id = "m-preflight-late"
            csv_path = csv_dir / f"{material_id}.csv"
            csv_path.write_text(
                "recordType\x01materialId\x01videoUrl\n"
                f"material_info\x01{material_id}\x01https://video.example/{material_id}.mp4\n",
                encoding="utf-8",
            )
            args = type("Args", (), {
                "output_dir": str(root / "peak-frames"),
                "diagnosis_records_dir": str(records_dir),
                "rpa_late_artifact_grace_sec": 0,
            })()
            failed = {
                "materialId": material_id,
                "rpaAvailable": False,
                "failureReason": "rpa_request_failed",
                "videoUrl": None,
            }

            def isolated_failure(_params, _timeout, **_kwargs):
                csv_path.touch()
                return failed

            with patch.object(
                extract_peak_frames,
                "_fetch_material_preflight_isolated",
                side_effect=isolated_failure,
            ):
                args.shop_id = "shop"
                args.date_range_type = "CUSTOM"
                args.custom_start_date = "2026-07-25"
                args.custom_end_date = "2026-07-31"
                args.rpa_preflight_timeout_sec = 1
                args.rpa_preflight_soft_timeout_sec = 1
                recovered = extract_peak_frames._fetch_material_preflight_for_cli(
                    args, material_id
                )

        self.assertTrue(recovered["rpaAvailable"])
        self.assertTrue(recovered["lateArtifactRecovered"])
        self.assertEqual(recovered["videoUrl"], f"https://video.example/{material_id}.mp4")

    def test_late_artifact_recovery_keeps_request_failure_when_csv_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "peak-frames"
            csv_dir = root / "diagnosis-csv"
            csv_dir.mkdir(parents=True)
            material_id = "m-invalid-late"
            (csv_dir / f"{material_id}.csv").write_text("not,a,diagnosis,csv\n", encoding="utf-8")
            args = type("Args", (), {
                "output_dir": str(output),
                "diagnosis_records_dir": str(root / "diagnosis-records"),
            })()
            args.rpa_late_artifact_grace_sec = 0

            recovered = extract_peak_frames._recover_late_rpa_artifacts(
                args, material_id, {"rpaAvailable": False, "failureReason": "rpa_request_failed"}
            )

            self.assertFalse(recovered["rpaAvailable"])
            self.assertEqual(recovered["failureReason"], "rpa_request_failed")

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
                    "zero_second",
                    "peak_top_1",
                    "peak_top_2",
                    "peak_top_3",
                ],
            )
            self.assertEqual(summary["warnings"], [])
            self.assertIn(f"result_dir={result_dir}", completed.stdout)

    def test_local_csv_cli_reuses_peak_frame_pipeline(self):
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            input_csv = root / "diagnosis.csv"
            output = root / "output"
            diagnosis_records = root / "diagnosis-records"
            self._make_video(ffmpeg, video, duration=9)
            rows = [
                "recordType\x01materialId\x01interactionMetric\x01durationSec\x01metricValue",
                *(
                    f"\x02interaction_series\x01m-csv\x01CLICK_COUNT\x01{second}\x01{value}"
                    for second, value in enumerate([0, 2, 0, 9, 0, 7, 0, 8, 0])
                ),
            ]
            input_csv.write_text("\n".join(rows) + "\n", encoding="utf-8")

            completed = self._run_local_csv(
                input_csv,
                video,
                output,
                ffmpeg,
                diagnosis_records_dir=diagnosis_records,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(
                (output / "m-csv" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(summary["frames"]), 4)
            saved_records = json.loads(
                (diagnosis_records / "m-csv.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_records[0]["durationSec"], 0.0)

    def test_local_csv_cli_downloads_video_when_video_is_not_provided(self):
        records = [
            click_record(0, 0, material_id="m-csv"),
            click_record(1, 2, material_id="m-csv"),
            click_record(2, 0, material_id="m-csv"),
            {
                "recordType": "material_info",
                "materialId": "m-csv",
                "videoUrl": "https://video.example/material.mp4",
            },
        ]
        output = Path(tempfile.gettempdir()) / "csv-output"
        with patch.object(
            extract_peak_frames, "parse_diagnosis_csv", return_value=records
        ), patch.object(
            extract_peak_frames,
            "download_video",
        ) as download_video, patch.object(
            extract_peak_frames,
            "process_records",
            return_value=(output, output / "summary.json", output / "summary.md"),
        ) as process_records:
            result = extract_peak_frames.main(
                ["--input-csv", "diagnosis.csv", "--output-dir", str(output)]
            )

        self.assertEqual(result, 0)
        self.assertEqual(download_video.call_args.args[0], records[-1]["videoUrl"])
        self.assertEqual(process_records.call_args.args[0], records)

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
            stale_peak_frames = {
                path for path in stale_owned_frames if not path.name.startswith("zero_second_")
            }
            user_frame = result_dir / "user-reference.jpg"
            user_frame.write_bytes(DECODABLE_JPEG)

            self._write_series(
                input_json, list(enumerate([0, 5, 0, 4, 0]))
            )
            second_run = self._run_local(input_json, video, output, ffmpeg)

            self.assertEqual(second_run.returncode, 0, second_run.stderr)
            self.assertTrue(user_frame.is_file())
            self.assertTrue(stale_peak_frames.isdisjoint(result_dir.glob("*.jpg")))
            summary = json.loads(
                (result_dir / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(summary["frames"]), 3)

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
            self.assertEqual(len(summary["frames"]), 3)

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
            self.assertEqual(len(summary["frames"]), 3)
            self.assertEqual(
                summary["warnings"],
                ["1 top peak frame(s) unavailable"],
            )

    def test_rpa_source_auto_resolves_single_shop_id(self):
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

            def fake_download(_video_url, target_path):
                Path(target_path).write_bytes(source_video.read_bytes())
                return Path(target_path)

            with patch(
                "extract_peak_frames.list_accounts",
                return_value={"data": [{"account": "only@example.com", "shop_id": "shop-1"}]},
            ) as list_mock, patch(
                "extract_peak_frames.fetch_material_diagnosis",
                return_value=records,
            ) as fetch_mock, patch(
                "extract_peak_frames.download_video",
                side_effect=fake_download,
                create=True,
            ):
                completed = extract_peak_frames.main(
                    [
                        "--source",
                        "rpa",
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

            self.assertEqual(completed, 0)
            list_mock.assert_called_once()
            fetch_mock.assert_called_once_with(
                shop_id="shop-1",
                material_id="764",
                date_range_type="LAST_7_DAYS",
                custom_start_date=None,
                custom_end_date=None,
                diagnosis_csv_dir=output / "diagnosis-csv",
            )

    def test_rpa_source_rejects_multiple_auto_resolved_shop_ids(self):
        import extract_peak_frames

        stderr = io.StringIO()
        with patch(
            "extract_peak_frames.list_accounts",
            return_value={
                "data": [
                    {"account": "first@example.com", "shop_id": "shop-1"},
                    {"account": "second@example.com", "shop_id": "shop-2"},
                ]
            },
        ), patch("sys.stderr", stderr):
            completed = extract_peak_frames.main(
                [
                    "--source",
                    "rpa",
                    "--material-id",
                    "m-123",
                    "--date-range-type",
                    "LAST_7_DAYS",
                    "--output-dir",
                    ".test-output",
                ]
            )

        self.assertEqual(completed, 4)
        self.assertIn("multiple authorized RPA accounts", stderr.getvalue())
        self.assertIn("shop-1", stderr.getvalue())
        self.assertIn("shop-2", stderr.getvalue())

    def test_list_shop_accounts_prints_authorized_accounts(self):
        import extract_peak_frames

        stdout = io.StringIO()
        with patch(
            "extract_peak_frames.list_accounts",
            return_value={
                "data": [
                    {"account": "first@example.com", "shop_id": "shop-1"},
                    {"account": "second@example.com", "shop_id": "shop-2"},
                ]
            },
        ) as list_mock, patch("sys.stdout", stdout):
            completed = extract_peak_frames.main(["--list-shop-accounts"])

        self.assertEqual(completed, 0)
        list_mock.assert_called_once()
        self.assertIn("first@example.com", stdout.getvalue())
        self.assertIn("shop-1", stdout.getvalue())
        self.assertIn("second@example.com", stdout.getvalue())
        self.assertIn("shop-2", stdout.getvalue())

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
                diagnosis_csv_dir=output / "diagnosis-csv",
            )
            self.assertEqual(downloaded[0][0], "https://cdn.example/material.mp4")
            summary = json.loads(
                (output / "764" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["materialId"], "764")
            self.assertEqual(
                [frame["rankType"] for frame in summary["frames"]],
                [
                    "zero_second",
                    "peak_top_1",
                    "peak_top_2",
                    "peak_top_3",
                ],
            )

    def test_rpa_top_materials_cli_processes_retained_files_response(self):
        ffmpeg = resolve_ffmpeg()
        envelope = {
            "success": True,
            "code": "200",
            "data": {
                "result_type": "files",
                "result_content": {
                    "status": "COMPLETED",
                    "failedInstances": 0,
                    "files": [
                        {"fileUrl": "https://files.example/diagnosis.csv"}
                    ],
                },
            },
        }
        rows = [
            "recordType\x01materialId\x01interactionMetric\x01durationSec\x01metricValue\x01videoUrl",
            *(
                f"\x02interaction_series\x01m-files\x01CLICK_COUNT\x01{second}\x01{value}\x01"
                for second, value in enumerate([0, 2, 0, 9, 0, 7, 0, 8, 0])
            ),
            "\x02material_info\x01m-files\x01\x01\x01\x01https://video.example/m-files.mp4",
        ]
        csv_bytes = ("\n".join(rows) + "\n").encode("utf-8")
        session = RecordingSession(
            FakeResponse(envelope), FakeResponse(None, content=csv_bytes)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_video = root / "source.mp4"
            top_materials = root / "top_materials.json"
            diagnosis_records = root / "diagnosis-records"
            output = root / "peak-frames"
            self._make_video(ffmpeg, source_video)
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["m-files"])),
                encoding="utf-8",
            )

            def fake_download(_video_url, target_path):
                Path(target_path).write_bytes(source_video.read_bytes())
                return Path(target_path)

            with patch.dict(
                os.environ,
                {
                    "ENV_BACKEND_HOST": "https://gateway.example/",
                    "YUCE_AUTHORIZATION": "auth-token",
                    "YUCE_SESSION_ID": "session-123",
                },
                clear=False,
            ), patch.object(
                rpa_adapter, "_default_session", return_value=session
            ), patch.object(
                extract_peak_frames, "download_video", side_effect=fake_download
            ):
                result = extract_peak_frames.main(
                    [
                        "--source",
                        "rpa",
                        "--shop-id",
                        "shop-1",
                        "--top-materials-json",
                        str(top_materials),
                        "--date-range-type",
                        "CUSTOM",
                        "--custom-start-date",
                        "2026-06-01",
                        "--custom-end-date",
                        "2026-06-02",
                        "--diagnosis-records-dir",
                        str(diagnosis_records),
                        "--output-dir",
                        str(output),
                        "--ffmpeg-bin",
                        ffmpeg,
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(len(list((output / "m-files").glob("*.jpg"))), 4)
            self.assertTrue((diagnosis_records / "m-files.json").is_file())
            self.assertEqual(
                (root / "diagnosis-csv" / "m-files.csv").read_bytes(),
                csv_bytes,
            )

    def test_rpa_cli_processes_top_materials_json(self):
        import extract_peak_frames

        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_video = root / "source.mp4"
            output = root / "output"
            diagnosis_records = root / "diagnosis-records"
            top_materials = root / "top_materials.json"
            self._make_video(ffmpeg, source_video)
            top_materials.write_text(
                json.dumps(self._top_materials_payload(["764", "765"])),
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
                        "--diagnosis-records-dir",
                        str(diagnosis_records),
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
            self.assertEqual(
                json.loads(
                    (diagnosis_records / "764.json").read_text(
                        encoding="utf-8"
                    )
                ),
                records_for("764"),
            )
            self.assertEqual(
                json.loads(
                    (diagnosis_records / "765.json").read_text(
                        encoding="utf-8"
                    )
                ),
                records_for("765"),
            )

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
