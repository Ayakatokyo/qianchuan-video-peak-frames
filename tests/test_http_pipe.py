from pathlib import Path
import json
import sys
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_adapter  # noqa: E402
import peak_frame_lib  # noqa: E402
import benchmark_http_pipe  # noqa: E402


class FakeResponse:
    def __init__(
        self,
        chunks,
        *,
        status_code=200,
        status_error=None,
        headers=None,
        events=None,
        json_payload=None,
    ):
        self.chunks = list(chunks)
        self.status_code = status_code
        self.status_error = status_error
        self.headers = {} if headers is None else headers
        self.events = [] if events is None else events
        self.json_payload = json_payload
        self.json_calls = 0
        self.chunk_sizes = []
        self.closed = False

    def raise_for_status(self):
        if self.status_error is not None:
            raise self.status_error

    def iter_content(self, chunk_size):
        self.chunk_sizes.append(chunk_size)
        for chunk in self.chunks:
            self.events.append(("yield", chunk))
            yield chunk

    def json(self):
        self.json_calls += 1
        return self.json_payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.response


class FakeStdin:
    def __init__(self, *, events=None, write_error=None):
        self.events = [] if events is None else events
        self.write_error = write_error
        self.writes = []
        self.closed = False

    def write(self, chunk):
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(chunk)
        self.events.append(("write", chunk))
        return len(chunk)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stdin, *, terminate_error=None, wait_errors=None):
        self.stdin = stdin
        self.terminate_error = terminate_error
        self.wait_errors = [] if wait_errors is None else list(wait_errors)
        self.terminated = False
        self.terminate_calls = 0
        self.wait_calls = []
        self.killed = False

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_error is not None:
            raise self.terminate_error
        self.terminated = True

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        return 0

    def kill(self):
        self.killed = True
        self.terminated = True


class DelayedGetSession:
    def __init__(self, response, delay=0.25):
        self.response = response
        self.delay = delay
        self.release = threading.Event()
        self.get_calls = []

    def get(self, _url, **kwargs):
        self.get_calls.append(kwargs)
        self.release.wait(self.delay)
        return self.response


class DelayedReadResponse(FakeResponse):
    def __init__(self, delay=0.25):
        super().__init__([b"chunk"])
        self.delay = delay
        self.release = threading.Event()

    def iter_content(self, chunk_size):
        self.chunk_sizes.append(chunk_size)
        self.release.wait(self.delay)
        yield b"chunk"


class DelayedWriteStdin(FakeStdin):
    def __init__(self, delay=0.25):
        super().__init__()
        self.delay = delay
        self.release = threading.Event()

    def write(self, chunk):
        self.release.wait(self.delay)
        if self.closed:
            raise BrokenPipeError("ffmpeg stdin closed")
        return super().write(chunk)


class SlowCleanupProcess(FakeProcess):
    def __init__(self, stdin, delay=0.25):
        super().__init__(stdin)
        self.delay = delay
        self.wait_started = threading.Event()
        self.wait_finished = threading.Event()

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.wait_started.set()
        time.sleep(self.delay)
        self.wait_finished.set()
        return 0


def wait_until(predicate, timeout=1.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


def stream_records(material_id="m-transport-failure"):
    return [
        {
            "recordType": "material_info",
            "materialId": material_id,
            "videoUrl": "https://cdn.example/video.mp4?X-Amz-Signature=secret",
            "videoDuration": "3.0",
        },
        {
            "recordType": "interaction_series",
            "interactionMetric": "CLICK_COUNT",
            "durationSec": 0,
            "metricValue": 0,
            "materialId": material_id,
        },
        {
            "recordType": "interaction_series",
            "interactionMetric": "CLICK_COUNT",
            "durationSec": 1,
            "metricValue": 9,
            "materialId": material_id,
        },
        {
            "recordType": "interaction_series",
            "interactionMetric": "CLICK_COUNT",
            "durationSec": 2,
            "metricValue": 0,
            "materialId": material_id,
        },
    ]


class SequenceClock:
    def __init__(self, values):
        self.values = iter(values)
        self.last = None

    def monotonic(self):
        self.last = next(self.values, self.last)
        return self.last


def test_stream_video_uses_fixed_chunks_backpressure_and_returns_stats(monkeypatch):
    events = []
    response = FakeResponse([b"abc", b"", b"defg"], events=events)
    session = FakeSession(response)
    stdin = FakeStdin(events=events)
    process = FakeProcess(stdin)
    clock = SequenceClock([100.0, 100.01, 100.02, 100.03, 100.50])
    monkeypatch.setattr(rpa_adapter.time, "monotonic", clock.monotonic)

    stats = rpa_adapter.stream_video_to_ffmpeg(
        "https://video.example/video.mp4",
        process,
        session=session,
        timeout=7,
        max_bytes=20,
        deadline_sec=60,
    )

    assert session.get_calls == [
        (
            "https://video.example/video.mp4",
            {"stream": True, "timeout": 7},
        )
    ]
    assert response.chunk_sizes == [1024 * 1024]
    assert events == [
        ("yield", b"abc"),
        ("write", b"abc"),
        ("yield", b""),
        ("yield", b"defg"),
        ("write", b"defg"),
    ]
    assert stats == {"bytes": 7, "networkSec": 0.5}
    assert stdin.closed
    assert response.closed
    assert not process.terminated


def test_stream_video_reorders_eof_moov_with_bounded_ranges(monkeypatch):
    def box(kind, payload):
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    prefix = box(b"ftyp", b"")
    mdat = box(b"mdat", b"payload")
    moov = box(b"moov", b"x")
    total_size = len(prefix) + len(mdat) + len(moov)
    responses = [
        FakeResponse(
            [prefix + mdat],
            status_code=206,
            headers={"Content-Range": f"bytes 0-{len(prefix) + len(mdat) - 1}/{total_size}"},
        ),
        FakeResponse(
            [moov],
            status_code=206,
            headers={"Content-Range": f"bytes {len(prefix) + len(mdat)}-{total_size - 1}/{total_size}"},
        ),
        FakeResponse(
            [mdat],
            status_code=206,
            headers={"Content-Range": f"bytes {len(prefix)}-{len(prefix) + len(mdat) - 1}/{total_size}"},
        ),
    ]

    class RangeSession:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.get_calls = []

        def get(self, url, **kwargs):
            self.get_calls.append((url, kwargs))
            return next(self.responses)

    session = RangeSession(responses)
    stdin = FakeStdin()
    process = FakeProcess(stdin)
    monkeypatch.setattr(rpa_adapter.time, "monotonic", SequenceClock([0.0]).monotonic)

    stats = rpa_adapter.stream_video_to_ffmpeg(
        "https://video.example/video.mp4",
        process,
        session=session,
        max_bytes=total_size,
        deadline_sec=60,
        normalize_mp4=True,
    )

    assert [call[1]["headers"]["Range"] for call in session.get_calls] == [
        f"bytes=0-{rpa_adapter.MP4_METADATA_PROBE_BYTES - 1}",
        f"bytes={len(prefix) + len(mdat)}-{total_size - 1}",
        f"bytes={len(prefix)}-{len(prefix) + len(mdat) - 1}",
    ]
    assert stdin.writes == [prefix, moov, mdat]
    assert stats["bytes"] == total_size
    assert all(response.closed for response in responses)


def test_stream_video_rejects_bytes_over_limit_before_writing_excess(monkeypatch):
    response = FakeResponse([b"1234", b"56"])
    session = FakeSession(response)
    stdin = FakeStdin()
    process = FakeProcess(stdin)
    clock = SequenceClock([0.0, 0.1, 0.2])
    monkeypatch.setattr(rpa_adapter.time, "monotonic", clock.monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError, match="maximum size"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=5,
            deadline_sec=60,
        )

    assert stdin.writes == [b"1234"]
    assert stdin.closed
    assert response.closed
    assert process.terminated


def test_stream_video_rejects_declared_size_over_limit_without_writing(monkeypatch):
    response = FakeResponse([b"1234"], headers={"Content-Length": "5"})
    session = FakeSession(response)
    stdin = FakeStdin()
    process = FakeProcess(stdin)
    monkeypatch.setattr(rpa_adapter.time, "monotonic", SequenceClock([0.0]).monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError, match="maximum size"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=4,
            deadline_sec=60,
        )

    assert stdin.writes == []
    assert stdin.closed
    assert response.closed
    assert process.terminated


def test_stream_video_stops_at_total_deadline_and_closes_everything(monkeypatch):
    response = FakeResponse([b"first", b"second"])
    session = FakeSession(response)
    stdin = FakeStdin()
    process = FakeProcess(stdin)
    clock = SequenceClock([0.0, 0.1, 0.15, 0.2, 2.0])
    monkeypatch.setattr(rpa_adapter.time, "monotonic", clock.monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError, match="total deadline"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=100,
            deadline_sec=1,
        )

    assert stdin.writes == [b"first"]
    assert stdin.closed
    assert response.closed
    assert process.terminated


def test_stream_video_http_error_terminates_ffmpeg_and_closes_response(monkeypatch):
    response = FakeResponse(
        [],
        status_code=503,
        status_error=RuntimeError("503 service unavailable"),
        headers={"X-Request-Id": "req-123"},
    )
    session = FakeSession(response)
    stdin = FakeStdin()
    process = FakeProcess(stdin)
    monkeypatch.setattr(rpa_adapter.time, "monotonic", SequenceClock([0.0]).monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError) as raised:
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=100,
            deadline_sec=60,
        )

    assert raised.value.diagnostics == {
        "gatewayPhase": "response",
        "httpStatus": 503,
        "rpaFailureClass": "gateway_server_error",
        "requestId": "req-123",
    }
    assert stdin.closed
    assert response.closed
    assert process.terminated


def test_stream_video_wraps_stdin_write_error_and_closes_response(monkeypatch):
    response = FakeResponse([b"chunk"])
    session = FakeSession(response)
    stdin = FakeStdin(write_error=BrokenPipeError("ffmpeg stdin closed"))
    process = FakeProcess(stdin)
    monkeypatch.setattr(rpa_adapter.time, "monotonic", SequenceClock([0.0, 0.1]).monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError, match="stream") as raised:
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=100,
            deadline_sec=60,
        )

    assert isinstance(raised.value.__cause__, BrokenPipeError)
    assert stdin.closed
    assert response.closed
    assert process.terminated


def test_stream_video_status_error_does_not_expose_signed_url_or_read_body(monkeypatch):
    signed_url = "https://cdn.example/video.mp4?X-Amz-Signature=secret-signature"
    response = FakeResponse(
        [],
        status_code=403,
        status_error=RuntimeError(f"GET {signed_url} returned 403"),
        headers={"X-Request-Id": "req-signed"},
        json_payload={"error": signed_url},
    )
    process = FakeProcess(FakeStdin())
    monkeypatch.setattr(rpa_adapter.time, "monotonic", SequenceClock([0.0]).monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError) as raised:
        rpa_adapter.stream_video_to_ffmpeg(
            signed_url,
            process,
            session=FakeSession(response),
            max_bytes=100,
            deadline_sec=60,
        )

    assert signed_url not in str(raised.value)
    assert raised.value.__cause__ is None
    assert response.json_calls == 0
    assert raised.value.diagnostics == {
        "gatewayPhase": "response",
        "httpStatus": 403,
        "rpaFailureClass": "gateway_client_error",
        "requestId": "req-signed",
    }
    assert response.closed
    assert process.terminated


def test_stream_video_deadline_cancels_delayed_request(monkeypatch):
    response = FakeResponse([b"chunk"])
    session = DelayedGetSession(response)
    process = FakeProcess(FakeStdin())
    monkeypatch.setattr(rpa_adapter.time, "monotonic", time.monotonic)

    started = time.perf_counter()
    with pytest.raises(rpa_adapter.RpaAdapterError, match="total deadline"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=100,
            deadline_sec=0.02,
        )
    elapsed = time.perf_counter() - started
    session.release.set()

    assert elapsed < 0.15
    assert session.get_calls[0]["timeout"] <= 0.02
    assert process.terminated
    assert wait_until(lambda: response.closed)


def test_stream_video_deadline_cancels_delayed_read(monkeypatch):
    response = DelayedReadResponse()
    session = FakeSession(response)
    process = FakeProcess(FakeStdin())
    monkeypatch.setattr(rpa_adapter.time, "monotonic", time.monotonic)

    started = time.perf_counter()
    with pytest.raises(rpa_adapter.RpaAdapterError, match="total deadline"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=session,
            max_bytes=100,
            deadline_sec=0.02,
        )
    elapsed = time.perf_counter() - started
    response.release.set()

    assert elapsed < 0.15
    assert process.terminated
    assert process.stdin.writes == []
    assert response.closed


def test_stream_video_deadline_cancels_delayed_stdin_write(monkeypatch):
    response = FakeResponse([b"chunk"])
    stdin = DelayedWriteStdin()
    process = FakeProcess(stdin)
    monkeypatch.setattr(rpa_adapter.time, "monotonic", time.monotonic)

    started = time.perf_counter()
    with pytest.raises(rpa_adapter.RpaAdapterError, match="total deadline"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=FakeSession(response),
            max_bytes=100,
            deadline_sec=0.02,
        )
    elapsed = time.perf_counter() - started
    stdin.release.set()

    assert elapsed < 0.15
    assert stdin.closed
    assert process.terminated
    assert response.closed


def test_stream_video_deadline_returns_before_slow_ffmpeg_cleanup(monkeypatch):
    response = DelayedReadResponse()
    process = SlowCleanupProcess(FakeStdin())
    monkeypatch.setattr(rpa_adapter.time, "monotonic", time.monotonic)

    started = time.perf_counter()
    with pytest.raises(rpa_adapter.RpaAdapterError, match="total deadline"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=FakeSession(response),
            max_bytes=100,
            deadline_sec=0.02,
        )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.15
    assert process.terminated
    assert process.wait_started.wait(0.1)
    assert process.wait_finished.wait(1.0)
    assert process.stdin.closed
    assert response.closed


def test_stream_video_cleanup_uses_wait_and_kill_when_terminate_fails(monkeypatch):
    response = FakeResponse([b"chunk"])
    process = FakeProcess(
        FakeStdin(write_error=BrokenPipeError("ffmpeg stdin closed")),
        terminate_error=RuntimeError("terminate failed"),
        wait_errors=[RuntimeError("still running")],
    )
    monkeypatch.setattr(rpa_adapter.time, "monotonic", SequenceClock([0.0, 0.1]).monotonic)

    with pytest.raises(rpa_adapter.RpaAdapterError, match="stream"):
        rpa_adapter.stream_video_to_ffmpeg(
            "https://video.example/video.mp4",
            process,
            session=FakeSession(response),
            max_bytes=100,
            deadline_sec=60,
        )

    assert process.terminate_calls == 1
    assert process.wait_calls == [5, 5]
    assert process.killed
    assert response.closed


def test_stream_extractor_cleans_process_after_transport_failure(monkeypatch, tmp_path):
    records = stream_records()
    process_holder = {}

    class FailingProcess:
        def __init__(self, command):
            self.command = command
            self.stdin = FakeStdin()
            self.returncode = None
            self.terminated = False
            self.wait_calls = []

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            self.returncode = -15
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.terminated = True
            self.returncode = -9

    def fake_popen(command, **_kwargs):
        process_holder["process"] = FailingProcess(command)
        return process_holder["process"]

    def fail_transport(_url, _process, **_kwargs):
        raise rpa_adapter.RpaAdapterError(
            "stream transport failed",
            diagnostics={"gatewayPhase": "response", "httpStatus": 503},
        )

    monkeypatch.setattr(peak_frame_lib.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(peak_frame_lib, "resolve_ffmpeg", lambda **_kwargs: "ffmpeg")
    monkeypatch.setattr(
        peak_frame_lib, "stream_video_to_ffmpeg", fail_transport, raising=False
    )

    with pytest.raises(rpa_adapter.RpaAdapterError, match="stream transport failed") as raised:
        peak_frame_lib.extract_frames_from_stream(
            records,
            records[0]["videoUrl"],
            tmp_path / "output",
        )

    process = process_holder["process"]
    assert process.terminated
    assert process.stdin.closed
    assert raised.value.diagnostics == {
        "gatewayPhase": "response",
        "httpStatus": 503,
    }
    assert list((tmp_path / "output" / "m-transport-failure").glob(".stream-*.jpg")) == []


def test_stream_extractor_cleans_process_after_stream_deadline(monkeypatch, tmp_path):
    records = stream_records("m-stream-deadline")
    process_holder = {}

    class DeadlineProcess:
        def __init__(self, command):
            self.command = command
            self.stdin = FakeStdin()
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.terminated = True
            self.returncode = -9

    def fake_popen(command, **_kwargs):
        process_holder["process"] = DeadlineProcess(command)
        return process_holder["process"]

    def fail_transport(_url, _process, **_kwargs):
        raise rpa_adapter.RpaAdapterError(
            "video stream exceeded total deadline",
            diagnostics={"gatewayPhase": "stream"},
        )

    monkeypatch.setattr(peak_frame_lib.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(peak_frame_lib, "resolve_ffmpeg", lambda **_kwargs: "ffmpeg")
    monkeypatch.setattr(peak_frame_lib, "stream_video_to_ffmpeg", fail_transport)

    with pytest.raises(rpa_adapter.RpaAdapterError, match="total deadline"):
        peak_frame_lib.extract_frames_from_stream(
            records,
            records[0]["videoUrl"],
            tmp_path / "output",
        )

    process = process_holder["process"]
    assert process.terminated
    assert process.stdin.closed
    assert list((tmp_path / "output" / "m-stream-deadline").glob(".stream-*.jpg")) == []
    assert list((tmp_path / "output").rglob("*.mp4")) == []


def test_benchmark_report_compares_frames_without_persisting_signed_url(tmp_path):
    signed_url = "https://cdn.example/video.mp4?X-Amz-Signature=do-not-log"
    pipe_case = {
        "status": "succeeded",
        "elapsedSec": 2.5,
        "videoStreamBytes": 1234,
        "videoStreamSec": 1.25,
        "pythonRssBytes": 100,
        "ffmpegRssBytes": 200,
        "summary": {
            "frames": [
                {
                    "rankType": "zero_second",
                    "durationSec": 0.0,
                    "framePath": "zero_second_0000s_0.jpg",
                }
            ]
        },
        "frameHashes": {"zero_second": "abc"},
    }
    local_case = {
        "status": "succeeded",
        "elapsedSec": 4.0,
        "videoBytes": 1234,
        "pythonRssBytes": 110,
        "ffmpegRssBytes": 210,
        "summary": pipe_case["summary"],
        "frameHashes": {"zero_second": "abc"},
    }

    report = benchmark_http_pipe.build_comparison_report(
        "m-benchmark", signed_url, pipe_case, local_case
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert signed_url not in serialized
    assert report["video"]["host"] == "cdn.example"
    assert report["frameConsistency"]["sameTimestamps"] is True
    assert report["frameConsistency"]["sameFrameHashes"] is True
    assert report["pipe"]["videoStreamBytes"] == 1234
    assert report["local"]["videoBytes"] == 1234
