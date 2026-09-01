#!/usr/bin/env python3
"""Benchmark HTTP-to-FFmpeg pipe extraction against a temporary local baseline.

This script consumes already-persisted diagnosis records. It never invokes the
RPA gateway and never keeps the downloaded local-baseline MP4 after a run.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import resource
import sys
import tempfile
import time
from urllib.parse import urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from peak_frame_lib import extract_frames_from_stream, process_records  # noqa: E402
from path_safety import validate_material_id  # noqa: E402
from rpa_adapter import download_video, find_material_video_url  # noqa: E402


BENCHMARK_CHUNK_SIZE = 1024 * 1024
ERROR_MESSAGE_MAX_CHARS = 512


def _rss_bytes(usage):
    value = int(getattr(usage, "ru_maxrss", 0) or 0)
    return value if sys.platform == "darwin" else value * 1024


def _rss_snapshot(who):
    return _rss_bytes(resource.getrusage(who))


def _safe_error(error, redacted_url=None):
    message = str(error)
    if redacted_url:
        message = message.replace(redacted_url, "<redacted-url>")
    message = re.sub(r"https?://[^\s)]+", "<redacted-url>", message)
    return message[:ERROR_MESSAGE_MAX_CHARS]


def _video_metadata(video_url):
    parsed = urlsplit(str(video_url))
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
    }


def _load_records(records_dir, material_id):
    try:
        material_id = validate_material_id(material_id)
    except ValueError as exc:
        raise RuntimeError(f"invalid material ID {material_id!r}: {exc}") from exc
    source = Path(records_dir) / f"{material_id}.json"
    try:
        records = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to read retained records {source}: {exc}") from exc
    if not isinstance(records, list):
        raise RuntimeError(f"retained records must be a list: {source}")
    return records, source


def _summary_and_hashes(result):
    result_dir, summary_json, _summary_md = result
    summary_path = Path(summary_json)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    frame_hashes = {}
    for frame in summary.get("frames", []):
        rank_type = str(frame.get("rankType") or "")
        frame_path = Path(result_dir) / str(frame.get("framePath") or "")
        digest = hashlib.sha256()
        with frame_path.open("rb") as source:
            while True:
                chunk = source.read(BENCHMARK_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        frame_hashes[rank_type] = digest.hexdigest()
    return summary, frame_hashes


def _run_pipe_case(records, video_url, material_id, output_dir, ffmpeg_bin=None, timeout_sec=120.0):
    timings = {}
    started = time.perf_counter()
    python_before = _rss_snapshot(resource.RUSAGE_SELF)
    children_before = _rss_snapshot(resource.RUSAGE_CHILDREN)
    try:
        result = extract_frames_from_stream(
            records,
            video_url,
            Path(output_dir) / "pipe",
            ffmpeg_bin=ffmpeg_bin,
            ffmpeg_timeout_sec=timeout_sec,
            video_source="benchmark-http-pipe",
            timings=timings,
        )
        summary, frame_hashes = _summary_and_hashes(result)
    except Exception as exc:
        return {
            "status": "failed",
            "elapsedSec": round(time.perf_counter() - started, 6),
            "pythonRssBytes": max(python_before, _rss_snapshot(resource.RUSAGE_SELF)),
            "ffmpegRssBytes": max(children_before, _rss_snapshot(resource.RUSAGE_CHILDREN)),
            "videoStreamBytes": int(timings.get("videoStreamBytes", 0) or 0),
            "videoStreamSec": float(timings.get("videoStreamSec", 0.0) or 0.0),
            "errorType": type(exc).__name__,
            "errorMessage": _safe_error(exc, video_url),
            "persistentMp4Files": _persistent_mp4_files(Path(output_dir) / "pipe"),
        }
    return {
        "status": "succeeded",
        "elapsedSec": round(time.perf_counter() - started, 6),
        "pythonRssBytes": max(python_before, _rss_snapshot(resource.RUSAGE_SELF)),
        "ffmpegRssBytes": max(children_before, _rss_snapshot(resource.RUSAGE_CHILDREN)),
        "videoStreamBytes": int(timings.get("videoStreamBytes", 0) or 0),
        "videoStreamSec": float(timings.get("videoStreamSec", 0.0) or 0.0),
        "summary": summary,
        "frameHashes": frame_hashes,
        "persistentMp4Files": _persistent_mp4_files(Path(output_dir) / "pipe"),
    }


def _run_local_case(records, video_url, material_id, output_dir, ffmpeg_bin=None, timeout_sec=120.0):
    timings = {}
    started = time.perf_counter()
    python_before = _rss_snapshot(resource.RUSAGE_SELF)
    children_before = _rss_snapshot(resource.RUSAGE_CHILDREN)
    try:
        with tempfile.TemporaryDirectory(prefix=f"qianchuan-local-{material_id}-") as temp_dir:
            video_path = Path(temp_dir) / f"{material_id}.mp4"
            download_started = time.perf_counter()
            download_video(video_url, video_path)
            timings["videoDownloadSec"] = round(
                time.perf_counter() - download_started, 6
            )
            video_bytes = video_path.stat().st_size
            frame_started = time.perf_counter()
            result = process_records(
                records,
                "benchmark-retained-records",
                video_path,
                Path(output_dir) / "local",
                ffmpeg_bin=ffmpeg_bin,
                ffmpeg_timeout_sec=timeout_sec,
                video_source="benchmark-local-download",
                timings=timings,
            )
            timings["frameExtractionSec"] = round(
                time.perf_counter() - frame_started, 6
            )
        summary, frame_hashes = _summary_and_hashes(result)
    except Exception as exc:
        return {
            "status": "failed",
            "elapsedSec": round(time.perf_counter() - started, 6),
            "pythonRssBytes": max(python_before, _rss_snapshot(resource.RUSAGE_SELF)),
            "ffmpegRssBytes": max(children_before, _rss_snapshot(resource.RUSAGE_CHILDREN)),
            "videoBytes": int(locals().get("video_bytes", 0) or 0),
            "videoDownloadSec": float(timings.get("videoDownloadSec", 0.0) or 0.0),
            "errorType": type(exc).__name__,
            "errorMessage": _safe_error(exc, video_url),
            "persistentMp4Files": _persistent_mp4_files(Path(output_dir) / "local"),
        }
    return {
        "status": "succeeded",
        "elapsedSec": round(time.perf_counter() - started, 6),
        "pythonRssBytes": max(python_before, _rss_snapshot(resource.RUSAGE_SELF)),
        "ffmpegRssBytes": max(children_before, _rss_snapshot(resource.RUSAGE_CHILDREN)),
        "videoBytes": video_bytes,
        "videoDownloadSec": float(timings.get("videoDownloadSec", 0.0) or 0.0),
        "videoProbeSec": float(timings.get("videoProbeSec", 0.0) or 0.0),
        "frameExtractionSec": float(timings.get("frameExtractionSec", 0.0) or 0.0),
        "summary": summary,
        "frameHashes": frame_hashes,
        "persistentMp4Files": _persistent_mp4_files(Path(output_dir) / "local"),
    }


def _persistent_mp4_files(root):
    if not Path(root).exists():
        return []
    return sorted(str(path.relative_to(root)) for path in Path(root).rglob("*.mp4"))


def _frame_timestamps(case):
    summary = case.get("summary") or {}
    return [
        {
            "rankType": frame.get("rankType"),
            "durationSec": frame.get("durationSec"),
            "requestedTimestampSec": frame.get("requestedTimestampSec"),
        }
        for frame in summary.get("frames", [])
    ]


def build_comparison_report(material_id, video_url, pipe_case, local_case):
    pipe_timestamps = _frame_timestamps(pipe_case)
    local_timestamps = _frame_timestamps(local_case)
    pipe_hashes = pipe_case.get("frameHashes", {})
    local_hashes = local_case.get("frameHashes", {})
    return {
        "materialId": str(material_id),
        "video": _video_metadata(video_url),
        "pipe": pipe_case,
        "local": local_case,
        "frameConsistency": {
            "sameTimestamps": pipe_timestamps == local_timestamps,
            "sameFrameHashes": bool(pipe_hashes) and pipe_hashes == local_hashes,
            "pipeTimestamps": pipe_timestamps,
            "localTimestamps": local_timestamps,
        },
        "noPersistentMp4": not (
            pipe_case.get("persistentMp4Files")
            or local_case.get("persistentMp4Files")
        ),
    }


def benchmark_material(
    records_dir,
    material_id,
    output_dir,
    ffmpeg_bin=None,
    timeout_sec=120.0,
):
    records, records_path = _load_records(records_dir, material_id)
    video_url = find_material_video_url(records, material_id)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    pipe_case = _run_pipe_case(
        records, video_url, material_id, output_root, ffmpeg_bin, timeout_sec
    )
    local_case = _run_local_case(
        records, video_url, material_id, output_root, ffmpeg_bin, timeout_sec
    )
    report = build_comparison_report(material_id, video_url, pipe_case, local_case)
    report["recordsPath"] = str(records_path)
    return report


def _find_records_dir(run_dir):
    root = Path(run_dir)
    for name in ("records", "diagnosis-records"):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    raise RuntimeError(
        f"retained records directory is missing under {root}; expected records/ or diagnosis-records/"
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Benchmark retained HTTP pipe frame extraction against a temporary local baseline."
    )
    parser.add_argument("--run-dir", default="runs/20260901-rpa6")
    parser.add_argument("--records-dir")
    parser.add_argument("--material-id")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg-bin")
    parser.add_argument("--ffmpeg-timeout-sec", type=float, default=120.0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not math.isfinite(args.ffmpeg_timeout_sec) or args.ffmpeg_timeout_sec <= 0:
        raise SystemExit("--ffmpeg-timeout-sec must be positive and finite")
    records_dir = Path(args.records_dir) if args.records_dir else _find_records_dir(args.run_dir)
    material_id = args.material_id
    if not material_id:
        candidates = sorted(records_dir.glob("*.json"))
        if not candidates:
            raise SystemExit(f"no retained record JSON files found in {records_dir}")
        material_id = candidates[0].stem
    report = benchmark_material(
        records_dir,
        material_id,
        args.output_dir,
        ffmpeg_bin=args.ffmpeg_bin,
        timeout_sec=args.ffmpeg_timeout_sec,
    )
    report_path = Path(args.output_dir) / f"benchmark-{material_id}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"report={report_path}")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["pipe"]["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
