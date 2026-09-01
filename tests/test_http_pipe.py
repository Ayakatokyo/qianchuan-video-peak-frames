from pathlib import Path
import sys
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_adapter  # noqa: E402


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
