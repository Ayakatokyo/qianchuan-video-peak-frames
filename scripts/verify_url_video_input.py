#!/usr/bin/env python3
"""Validate direct FFmpeg URL reads against one retained RPA run.

This script only consumes persisted records/CSV/summary artifacts.  It never
starts an RPA request and deliberately keeps signed URLs out of its report.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
from urllib.parse import urlsplit

from peak_frame_lib import (
    DEFAULT_FFMPEG_TIMEOUT_SEC,
    FfmpegError,
    InputDataError,
    parse_click_series,
    process_records,
    select_peak_frames,
)
from rpa_adapter import (
    RpaAdapterError,
    find_material_video_url,
    probe_url_capabilities,
)
from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv


DEFAULT_PROBE_TIMEOUT_SEC = 30.0
DEFAULT_PROBE_BYTES = 1024 * 1024
REPORT_NAME = "url-validation-report.json"


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("value must be positive and finite") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        description="Validate direct FFmpeg URL frame extraction on retained RPA artifacts."
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="retained run directory containing diagnosis-records, diagnosis-csv, and peak-frames",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="separate directory for direct URL outputs and the redacted report",
    )
    parser.add_argument("--material-id")
    parser.add_argument("--ffmpeg-bin")
    parser.add_argument(
        "--ffmpeg-timeout-sec",
        type=_positive_float,
        default=DEFAULT_FFMPEG_TIMEOUT_SEC,
    )
    parser.add_argument(
        "--probe-timeout-sec",
        type=_positive_float,
        default=DEFAULT_PROBE_TIMEOUT_SEC,
    )
    parser.add_argument(
        "--probe-bytes",
        type=_positive_int,
        default=DEFAULT_PROBE_BYTES,
    )
    return parser


def _capability_value(capabilities, snake_name, camel_name, default=None):
    if isinstance(capabilities, dict):
        if snake_name in capabilities:
            return capabilities[snake_name]
        return capabilities.get(camel_name, default)
    return getattr(capabilities, snake_name, default)


def _safe_url_details(video_url, capabilities):
    """Return capability evidence without retaining the signed URL."""
    parsed = urlsplit(str(video_url or "").strip())
    supports_range = _capability_value(
        capabilities, "supports_range", "supportsRange"
    )
    if supports_range is None:
        supports_range = _capability_value(
            capabilities, "accept_ranges", "acceptRanges", False
        )
    return {
        "urlHost": parsed.hostname or "<invalid-host>",
        "statusCode": _capability_value(
            capabilities, "status_code", "statusCode", 0
        ),
        "contentLength": _capability_value(
            capabilities, "content_length", "contentLength"
        ),
        "acceptRanges": bool(
            _capability_value(capabilities, "accept_ranges", "acceptRanges", False)
        ),
        "supportsRange": bool(supports_range),
        "moovOffset": _capability_value(
            capabilities, "moov_offset", "moovOffset"
        ),
        "moovPosition": _capability_value(
            capabilities, "moov_position", "moovPosition", "unknown"
        ),
        "probeMethod": _capability_value(
            capabilities, "probe_method", "probeMethod", "unknown"
        ),
        "headStatusCode": _capability_value(
            capabilities, "head_status_code", "headStatusCode"
        ),
        "rangeStatusCode": _capability_value(
            capabilities, "range_status_code", "rangeStatusCode"
        ),
        "bytesRead": _capability_value(
            capabilities, "bytes_read", "bytesRead", 0
        ),
        "elapsedSec": _capability_value(
            capabilities, "elapsed_sec", "elapsedSec", 0.0
        ),
    }


def _frame_key(frame, include_filename=False):
    if not isinstance(frame, dict):
        return None
    timestamp = frame.get("requestedTimestampSec", frame.get("durationSec"))
    key = (frame.get("rankType"), timestamp)
    if include_filename:
        key += (frame.get("framePath"),)
    return key


def _compare_frame_metadata(retained_frames, direct_frames):
    retained = list(retained_frames or [])
    direct = list(direct_frames or [])
    return {
        "frameCount": len(direct),
        "timestampsMatch": len(retained) == len(direct)
        and [_frame_key(item) for item in retained]
        == [_frame_key(item) for item in direct],
        "filenamesMatch": len(retained) == len(direct)
        and [_frame_key(item, include_filename=True) for item in retained]
        == [_frame_key(item, include_filename=True) for item in direct],
    }


def _find_cleanup_leftovers(root):
    root = Path(root)
    if not root.exists():
        return []
    leftovers = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if path.suffix.lower() == ".mp4":
            leftovers.append(path)
            continue
        if ".stage" in name or ".tmp" in name or name.endswith(".part"):
            leftovers.append(path)
            continue
        if path.name.startswith(".") and path.suffix.lower() in {
            ".jpg",
            ".jpeg",
        }:
            leftovers.append(path)
    return sorted(leftovers)


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputDataError(f"unable to read JSON artifact {path}") from exc


def _load_material_artifacts(run_dir, material_id):
    run = Path(run_dir)
    records_path = run / "diagnosis-records" / f"{material_id}.json"
    csv_path = run / "diagnosis-csv" / f"{material_id}.csv"
    retained_summary_path = run / "peak-frames" / material_id / "summary.json"
    for path in (records_path, csv_path, retained_summary_path):
        if not path.is_file():
            raise InputDataError(f"retained artifact is missing: {path}")
    records = _load_json(records_path)
    retained_summary = _load_json(retained_summary_path)
    try:
        csv_records = parse_diagnosis_csv(csv_path)
    except (OSError, RpaCsvError) as exc:
        raise InputDataError(f"unable to parse retained diagnosis CSV {csv_path}") from exc
    video_url = find_material_video_url(records, material_id)
    csv_video_url = find_material_video_url(csv_records, material_id)
    return {
        "recordsPath": records_path,
        "csvPath": csv_path,
        "retainedSummaryPath": retained_summary_path,
        "records": records,
        "csvRecords": csv_records,
        "retainedSummary": retained_summary,
        "videoUrl": video_url,
        "csvVideoUrl": csv_video_url,
    }


def _selection_signature(selected):
    return [
        (peak.rank_type, peak.duration_sec, peak.metric_value)
        for peak in selected
    ]


def _rss_snapshot():
    snapshot = {}
    for label, scope in (
        ("self", resource.RUSAGE_SELF),
        ("children", resource.RUSAGE_CHILDREN),
    ):
        usage = resource.getrusage(scope)
        raw = int(usage.ru_maxrss or 0)
        # macOS reports bytes; Linux and most BSDs report KiB.
        snapshot[f"{label}MaxRssBytes"] = raw if sys.platform == "darwin" else raw * 1024
    return snapshot


def _safe_error(error, video_url):
    message = str(error)
    parsed = urlsplit(str(video_url or "").strip())
    safe = f"{parsed.scheme or 'unknown'}://{parsed.hostname or '<invalid-host>'}/[redacted]"
    return message.replace(str(video_url), safe)


def _pixel_signature(path):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            return {
                "size": list(rgb.size),
                "sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
            }
    except (OSError, ValueError):
        return None


def _compare_frame_pixels(retained_summary, direct_summary, retained_root, direct_root):
    retained_frames = retained_summary.get("frames", [])
    direct_frames = direct_summary.get("frames", [])
    comparisons = []
    for retained, direct in zip(retained_frames, direct_frames):
        retained_path = Path(retained_root) / retained.get("framePath", "")
        direct_path = Path(direct_root) / direct.get("framePath", "")
        retained_signature = _pixel_signature(retained_path)
        direct_signature = _pixel_signature(direct_path)
        comparisons.append(
            {
                "rankType": direct.get("rankType"),
                "match": retained_signature is not None
                and retained_signature == direct_signature,
                "retained": retained_signature,
                "direct": direct_signature,
            }
        )
    return {
        "comparedFrameCount": len(comparisons),
        "allMatch": bool(comparisons) and all(
            item["match"] for item in comparisons
        ),
        "frames": comparisons,
    }


def _artifact_contains_signed_url(root, video_url):
    parsed = urlsplit(str(video_url or "").strip())
    needles = [str(video_url), parsed.query]
    for path in Path(root).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".json",
            ".md",
            ".txt",
        }:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(needle and needle in content for needle in needles):
            return True
    return False


def validate_material(
    run_dir,
    material_id,
    output_dir,
    *,
    ffmpeg_bin=None,
    ffmpeg_timeout_sec=DEFAULT_FFMPEG_TIMEOUT_SEC,
    probe_timeout_sec=DEFAULT_PROBE_TIMEOUT_SEC,
    probe_bytes=DEFAULT_PROBE_BYTES,
):
    artifacts = _load_material_artifacts(run_dir, material_id)
    records = artifacts["records"]
    csv_records = artifacts["csvRecords"]
    retained_summary = artifacts["retainedSummary"]
    video_url = artifacts["videoUrl"]
    direct_root = Path(output_dir) / str(material_id)
    timings = {}
    started = time.perf_counter()
    rss_before = _rss_snapshot()
    report = {
        "materialId": str(material_id),
        "recordsPath": str(artifacts["recordsPath"]),
        "csvPath": str(artifacts["csvPath"]),
        "retainedSummaryPath": str(artifacts["retainedSummaryPath"]),
        "videoSource": "direct-url",
        "directRead": True,
    }
    try:
        record_material_id, record_points = parse_click_series(records)
        csv_material_id, csv_points = parse_click_series(csv_records)
        record_selected = select_peak_frames(record_points)
        csv_selected = select_peak_frames(csv_points)
        report["recordsCsvConsistency"] = {
            "materialIdMatch": record_material_id == csv_material_id == str(material_id),
            "selectedFramesMatch": _selection_signature(record_selected)
            == _selection_signature(csv_selected),
        }
        report["videoUrlHostMatch"] = urlsplit(video_url).hostname == urlsplit(
            artifacts["csvVideoUrl"]
        ).hostname

        capabilities = probe_url_capabilities(
            video_url,
            timeout=probe_timeout_sec,
            probe_bytes=probe_bytes,
        )
        report["urlCapabilities"] = _safe_url_details(video_url, capabilities)
        report["networkBytes"] = {
            "capabilityProbeBytes": report["urlCapabilities"]["bytesRead"],
            "declaredContentLength": report["urlCapabilities"]["contentLength"],
            "ffmpegBytesObserved": None,
            "ffmpegBytesNote": (
                "FFmpeg is given the signed URL directly; this validator records "
                "bounded HTTP probe bytes and declared length, not a fabricated "
                "whole-file download count."
            ),
        }
        timings["urlProbeSec"] = report["urlCapabilities"]["elapsedSec"]
        timings["urlProbeBytes"] = report["urlCapabilities"]["bytesRead"]

        extraction_started = time.perf_counter()
        result_dir, summary_path, _markdown_path = process_records(
            records,
            artifacts["recordsPath"],
            video_url,
            output_dir,
            ffmpeg_bin=ffmpeg_bin,
            ffmpeg_timeout_sec=ffmpeg_timeout_sec,
            source_kind="url",
            url_capabilities=capabilities,
            timings=timings,
        )
        timings["directExtractionSec"] = round(
            max(0.0, time.perf_counter() - extraction_started), 6
        )
        direct_summary = _load_json(summary_path)
        report["directSummaryPath"] = str(summary_path)
        report["frameMetadata"] = _compare_frame_metadata(
            retained_summary.get("frames", []), direct_summary.get("frames", [])
        )
        report["framePixels"] = _compare_frame_pixels(
            retained_summary,
            direct_summary,
            Path(artifacts["retainedSummaryPath"]).parent,
            result_dir,
        )
        report["summaryConsistency"] = {
            "expectedFramesMatch": retained_summary.get("expectedFrames")
            == direct_summary.get("expectedFrames"),
            "directSummaryHasNoSignedUrl": not _artifact_contains_signed_url(
                result_dir, video_url
            ),
        }
        report["checks"] = {
            "recordsCsvConsistent": all(
                report["recordsCsvConsistency"].values()
            ),
            "urlHostMatchesCsv": report["videoUrlHostMatch"],
            "sixFramesExtracted": report["frameMetadata"]["frameCount"] == 6,
            "timestampsMatch": report["frameMetadata"]["timestampsMatch"],
            "filenamesMatch": report["frameMetadata"]["filenamesMatch"],
            "pixelsMatch": report["framePixels"]["allMatch"],
            "expectedFramesMatch": report["summaryConsistency"][
                "expectedFramesMatch"
            ],
            "summaryHasNoSignedUrl": report["summaryConsistency"][
                "directSummaryHasNoSignedUrl"
            ],
        }
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
    except (InputDataError, FfmpegError, RpaAdapterError, RpaCsvError, OSError) as exc:
        report["status"] = "FAIL"
        report["errorType"] = type(exc).__name__
        report["errorMessage"] = _safe_error(exc, video_url)
    finally:
        rss_after = _rss_snapshot()
        report["timingsSec"] = dict(timings)
        report["timingsSec"]["totalSec"] = round(
            max(0.0, time.perf_counter() - started), 6
        )
        report["rss"] = {
            "before": rss_before,
            "after": rss_after,
            "selfDeltaBytes": max(
                0,
                rss_after["selfMaxRssBytes"] - rss_before["selfMaxRssBytes"],
            ),
            "childrenDeltaBytes": max(
                0,
                rss_after["childrenMaxRssBytes"]
                - rss_before["childrenMaxRssBytes"],
            ),
        }
        report["cleanup"] = {
            "leftovers": [
                str(path.relative_to(Path(output_dir)))
                for path in _find_cleanup_leftovers(direct_root)
            ],
            "noMp4": not any(
                path.suffix.lower() == ".mp4"
                for path in _find_cleanup_leftovers(direct_root)
            ),
        }
        report["checks"] = dict(report.get("checks", {}))
        report["checks"]["cleanupClear"] = not report["cleanup"]["leftovers"]
        if report.get("status") == "PASS" and not report["checks"]["cleanupClear"]:
            report["status"] = "FAIL"
        report["status"] = report.get("status", "FAIL")
    return report


def _material_ids(run_dir, requested=None):
    if requested:
        return [str(requested)]
    records_dir = Path(run_dir) / "diagnosis-records"
    if not records_dir.is_dir():
        raise InputDataError(f"diagnosis records directory is missing: {records_dir}")
    return sorted(path.stem for path in records_dir.glob("*.json"))


def _write_report(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        staging.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        material_ids = _material_ids(args.run_dir, args.material_id)
        if not material_ids:
            raise InputDataError("no retained diagnosis record JSON files found")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        materials = []
        for material_id in material_ids:
            result = validate_material(
                args.run_dir,
                material_id,
                output_dir,
                ffmpeg_bin=args.ffmpeg_bin,
                ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
                probe_timeout_sec=args.probe_timeout_sec,
                probe_bytes=args.probe_bytes,
            )
            materials.append(result)
            capabilities = result.get("urlCapabilities", {})
            frame_metadata = result.get("frameMetadata", {})
            print(
                f"material={material_id} status={result['status']} "
                f"host={capabilities.get('urlHost', '<unknown>')} "
                f"frames={frame_metadata.get('frameCount', 0)} "
                f"range={capabilities.get('supportsRange', False)} "
                f"moov={capabilities.get('moovPosition', 'unknown')}"
            )
        report = {
            "runDir": str(Path(args.run_dir)),
            "outputDir": str(output_dir),
            "materialCount": len(materials),
            "materials": materials,
            "status": "PASS" if all(item["status"] == "PASS" for item in materials) else "FAIL",
        }
        report_path = output_dir / REPORT_NAME
        _write_report(report_path, report)
        print(f"report={report_path}")
        return 0 if report["status"] == "PASS" else 1
    except (InputDataError, RpaAdapterError, RpaCsvError, OSError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
