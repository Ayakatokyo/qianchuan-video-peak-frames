from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_adapter  # noqa: E402


class FakeResponse:
    def __init__(self, chunks, *, status_code=200, status_error=None, headers=None, events=None):
        self.chunks = list(chunks)
        self.status_code = status_code
        self.status_error = status_error
        self.headers = {} if headers is None else headers
        self.events = [] if events is None else events
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
        return None

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
    def __init__(self, stdin):
        self.stdin = stdin
        self.terminated = False

    def terminate(self):
        self.terminated = True


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
    clock = SequenceClock([0.0, 0.1, 2.0])
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
