import io
import logging
from pathlib import Path
import struct
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_adapter  # noqa: E402


def mp4_box(box_type, payload=b""):
    return struct.pack(
        ">I4s", 8 + len(payload), box_type
    ) + payload


class ProbeResponse:
    def __init__(self, body=b"", *, status_code=206, headers=None, error=None):
        self.body = body
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.error = error
        self.closed = False

    def raise_for_status(self):
        if self.error is not None:
            raise self.error
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class ProbeSession:
    def __init__(self, head_response, get_responses):
        self.head_response = head_response
        self.get_responses = list(get_responses)
        self.head_calls = []
        self.get_calls = []

    def head(self, url, **kwargs):
        self.head_calls.append((url, kwargs))
        if isinstance(self.head_response, BaseException):
            raise self.head_response
        return self.head_response

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        response = self.get_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class UrlCapabilityProbeTests(unittest.TestCase):
    URL = "https://video.example/material.mp4?token=do-not-log&sig=secret-value"

    def test_head_failure_uses_range_get_and_parses_head_moov(self):
        body = mp4_box(b"ftyp", b"isom") + mp4_box(b"moov", b"metadata")
        session = ProbeSession(
            RuntimeError("HEAD is not supported"),
            [
                ProbeResponse(
                    body,
                    status_code=206,
                    headers={
                        "Content-Length": str(len(body)),
                        "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}",
                        "Accept-Ranges": "bytes",
                        "Content-Type": "video/mp4",
                    },
                )
            ],
        )

        capabilities = rpa_adapter.probe_url_capabilities(
            self.URL, session=session, probe_bytes=1024
        )

        self.assertEqual(capabilities.probe_method, "GET")
        self.assertEqual(capabilities.status_code, 206)
        self.assertEqual(capabilities.content_length, len(body))
        self.assertTrue(capabilities.accept_ranges)
        self.assertEqual(capabilities.moov_position, "head")
        self.assertEqual(capabilities.moov_offset, len(mp4_box(b"ftyp", b"isom")))
        self.assertEqual(capabilities.bytes_read, len(body))
        self.assertEqual(session.get_calls[0][1]["headers"]["Range"], "bytes=0-1023")
        self.assertTrue(session.get_responses == [])

    def test_range_probe_reads_tail_when_moov_is_not_in_prefix(self):
        prefix = mp4_box(b"ftyp", b"isom") + mp4_box(b"free", b"x" * 160)
        moov = mp4_box(b"moov", b"metadata")
        full_body = prefix + moov
        probe_bytes = 64
        prefix_end = probe_bytes - 1
        tail_start = len(full_body) - probe_bytes
        session = ProbeSession(
            ProbeResponse(
                headers={
                    "Content-Length": str(len(full_body)),
                    "Accept-Ranges": "bytes",
                },
                status_code=200,
            ),
            [
                ProbeResponse(
                    full_body[:probe_bytes],
                    status_code=206,
                    headers={
                        "Content-Length": str(probe_bytes),
                        "Content-Range": f"bytes 0-{prefix_end}/{len(full_body)}",
                        "Accept-Ranges": "bytes",
                    },
                ),
                ProbeResponse(
                    full_body[tail_start:],
                    status_code=206,
                    headers={
                        "Content-Length": str(probe_bytes),
                        "Content-Range": f"bytes {tail_start}-{len(full_body) - 1}/{len(full_body)}",
                        "Accept-Ranges": "bytes",
                    },
                ),
            ],
        )

        capabilities = rpa_adapter.probe_url_capabilities(
            self.URL, session=session, probe_bytes=probe_bytes
        )

        self.assertEqual(capabilities.probe_method, "HEAD+GET")
        self.assertEqual(capabilities.content_length, len(full_body))
        self.assertEqual(capabilities.moov_position, "tail")
        self.assertEqual(capabilities.moov_offset, len(prefix))
        self.assertEqual(len(session.get_calls), 2)
        self.assertEqual(
            session.get_calls[1][1]["headers"]["Range"],
            f"bytes={tail_start}-{len(full_body) - 1}",
        )

    def test_partial_get_without_content_range_keeps_head_content_length(self):
        body = mp4_box(b"ftyp", b"isom") + mp4_box(b"moov", b"metadata")
        session = ProbeSession(
            ProbeResponse(
                status_code=200,
                headers={
                    "Content-Length": "987654",
                    "Accept-Ranges": "bytes",
                },
            ),
            [
                ProbeResponse(
                    body,
                    status_code=206,
                    headers={
                        "Content-Length": str(len(body)),
                        "Accept-Ranges": "bytes",
                    },
                )
            ],
        )

        capabilities = rpa_adapter.probe_url_capabilities(
            self.URL, session=session, probe_bytes=1024
        )

        self.assertEqual(capabilities.content_length, 987654)

    def test_server_without_range_is_reported_without_reading_full_response(self):
        body = mp4_box(b"ftyp", b"isom") + mp4_box(b"moov", b"metadata") + b"x" * 128
        session = ProbeSession(
            ProbeResponse(
                headers={"Content-Length": "999999999", "Content-Type": "video/mp4"},
                status_code=200,
            ),
            [
                ProbeResponse(
                    body,
                    status_code=200,
                    headers={
                        "Content-Length": "999999999",
                        "Content-Type": "video/mp4",
                    },
                )
            ],
        )

        capabilities = rpa_adapter.probe_url_capabilities(
            self.URL, session=session, probe_bytes=32
        )

        self.assertFalse(capabilities.accept_ranges)
        self.assertEqual(capabilities.content_length, 999999999)
        self.assertEqual(capabilities.moov_position, "head")
        self.assertEqual(capabilities.bytes_read, 32)
        self.assertEqual(len(session.get_calls), 1)

    def test_signed_url_never_appears_in_probe_log_or_error(self):
        secret = "do-not-log"
        url = f"https://video.example/material.mp4?token={secret}&sig=secret-value"
        session = ProbeSession(
            RuntimeError(f"request failed for {url}"),
            [
                ProbeResponse(
                    status_code=403,
                    headers={},
                )
            ],
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("url-capability-probe-test")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            with self.assertRaises(rpa_adapter.RpaAdapterError) as context:
                rpa_adapter.probe_url_capabilities(url, session=session, logger=logger)
        finally:
            logger.removeHandler(handler)

        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, stream.getvalue())
        self.assertIn("video.example", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
