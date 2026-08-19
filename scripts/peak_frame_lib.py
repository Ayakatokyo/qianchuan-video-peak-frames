from dataclasses import dataclass
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from typing import Optional


DEFAULT_FFMPEG_TIMEOUT_SEC = 120.0


class PeakFrameError(Exception):
    """Base error for expected peak-frame failures."""


class InputDataError(PeakFrameError):
    """Raised when RPA input violates the expected data contract."""


class FfmpegError(PeakFrameError):
    """Raised when FFmpeg cannot be resolved or inspect a video."""


@dataclass(frozen=True)
class SeriesPoint:
    duration_sec: float
    metric_value: float


@dataclass(frozen=True)
class Peak:
    duration_sec: float
    metric_value: float


@dataclass(frozen=True)
class SelectedPeak:
    rank_type: str
    duration_sec: float
    metric_value: Optional[float]


def resolve_ffmpeg(
    explicit=None,
    environ=None,
    imageio_resolver=None,
    which=None,
):
    environment = os.environ if environ is None else environ

    if explicit is not None:
        try:
            explicit_path = Path(explicit)
            explicit_is_file = explicit_path.is_file()
        except (OSError, TypeError, ValueError) as exc:
            raise FfmpegError(
                f"Unable to check --ffmpeg-bin path {explicit!r}: {exc}"
            ) from exc
        if not explicit_is_file:
            raise FfmpegError(
                f"--ffmpeg-bin does not point to an existing regular file: "
                f"{explicit_path}"
            )
        return str(explicit_path)

    environment_value = environment.get("IMAGEIO_FFMPEG_EXE")
    if environment_value is not None:
        try:
            environment_path = Path(environment_value)
            environment_is_file = environment_path.is_file()
        except (OSError, TypeError, ValueError) as exc:
            raise FfmpegError(
                "Unable to check IMAGEIO_FFMPEG_EXE path "
                f"{environment_value!r}: {exc}"
            ) from exc
        if not environment_is_file:
            raise FfmpegError(
                "IMAGEIO_FFMPEG_EXE does not point to an existing regular "
                f"file: {environment_path}"
            )
        return str(environment_path)

    imageio_error = None
    if imageio_resolver is None:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
        except ImportError as exc:
            imageio_resolver = None
            imageio_error = exc
        else:
            imageio_resolver = get_ffmpeg_exe

    imageio_path = None
    if imageio_resolver is not None:
        try:
            resolved = imageio_resolver()
            if resolved:
                candidate = Path(resolved)
                if candidate.is_file():
                    imageio_path = candidate
        except Exception as exc:
            imageio_error = exc
    if imageio_path is not None:
        return str(imageio_path)

    path_resolver = shutil.which if which is None else which
    try:
        system_result = path_resolver("ffmpeg")
        if system_result:
            system_path = Path(system_result)
            if system_path.is_file():
                return str(system_path)
    except Exception as exc:
        raise FfmpegError(
            "Unable to find FFmpeg because PATH lookup failed: "
            f"{exc}. Install imageio-ffmpeg or provide --ffmpeg-bin."
        ) from exc

    message = (
        "Unable to find FFmpeg. Install imageio-ffmpeg or provide "
        "--ffmpeg-bin with the path to an FFmpeg executable."
    )
    if imageio_error is not None:
        raise FfmpegError(
            f"{message} imageio resolver failed: {imageio_error}"
        ) from imageio_error
    raise FfmpegError(message)


def probe_video_duration(
    ffmpeg_bin, video_path, timeout_sec=DEFAULT_FFMPEG_TIMEOUT_SEC
):
    video = Path(video_path)
    if not video.is_file():
        raise InputDataError(f"video does not exist or is not a file: {video}")

    try:
        process = subprocess.run(
            [str(ffmpeg_bin), "-hide_banner", "-i", str(video)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(
            f"FFmpeg timed out after {timeout_sec} seconds while probing "
            f"video duration for {video}"
        ) from exc
    except OSError as exc:
        raise FfmpegError(
            f"Unable to decode video duration for {video}: {exc}"
        ) from exc

    match = re.search(
        r"^[ \t]*Duration:\s*(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)(?=,|$)",
        process.stderr,
        re.MULTILINE,
    )
    if match is None:
        stderr_excerpt = process.stderr[:500]
        if len(process.stderr) > 500:
            stderr_excerpt += "..."
        raise FfmpegError(
            f"Unable to decode video duration for {video}; FFmpeg return "
            f"code {process.returncode}; stderr excerpt: {stderr_excerpt}"
        )

    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _stderr_excerpt(stderr, limit=500):
    stderr = stderr or ""
    excerpt = stderr[:limit]
    if len(stderr) > limit:
        excerpt += "..."
    return excerpt


def _canonical_seconds(value, field_name, error_type):
    if isinstance(value, bool):
        raise error_type(f"{field_name} {value!r} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error_type(
            f"{field_name} {value!r} must be a finite number"
        ) from exc
    if not math.isfinite(number):
        raise error_type(f"{field_name} {value!r} must be a finite number")
    return number, format(Decimal(repr(number)), "f")


def _raise_frame_error(message, staging_path, cause=None):
    cleanup_error = None
    try:
        staging_path.unlink(missing_ok=True)
    except OSError as exc:
        cleanup_error = exc
        message += (
            f"; unable to clean staging file {staging_path}: {cleanup_error}"
        )

    error = FfmpegError(message)
    if cause is not None:
        raise error from cause
    if cleanup_error is not None:
        raise error from cleanup_error
    raise error


def extract_frame(
    ffmpeg_bin,
    video_path,
    timestamp_sec,
    output_path,
    timeout_sec=DEFAULT_FFMPEG_TIMEOUT_SEC,
):
    video = Path(video_path)
    output = Path(output_path)
    _, timestamp_text = _canonical_seconds(
        timestamp_sec, "frame timestamp", FfmpegError
    )
    timeout, _ = _canonical_seconds(timeout_sec, "timeout", FfmpegError)
    if timeout <= 0:
        raise FfmpegError(
            f"timeout {timeout_sec!r} must be greater than 0 seconds"
        )

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FfmpegError(
            f"Unable to create frame output directory {output.parent}: {exc}"
        ) from exc

    staging = output.with_name(
        f".{output.stem}.{uuid.uuid4().hex}{output.suffix or '.jpg'}"
    )
    command = [
        str(ffmpeg_bin),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-ss",
        timestamp_text,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        str(staging),
    ]

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _raise_frame_error(
            f"FFmpeg timed out after {timeout_sec} seconds while extracting "
            f"frame at {timestamp_text}s from {video} to {output}",
            staging,
            exc,
        )
    except OSError as exc:
        _raise_frame_error(
            f"Unable to extract frame at {timestamp_text}s from {video} "
            f"to {output} with FFmpeg: {exc}",
            staging,
            exc,
        )

    if process.returncode < 0:
        retry_command = [
            str(ffmpeg_bin),
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-i",
            str(video),
            "-ss",
            timestamp_text,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(staging),
        ]
        first_return_code = process.returncode
        try:
            process = subprocess.run(
                retry_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            _raise_frame_error(
                f"FFmpeg low-resource retry timed out after {timeout}s while "
                f"extracting frame at {timestamp_text}s from {video}; first "
                f"return code {first_return_code}",
                staging,
                cause=exc,
            )
        except OSError as exc:
            _raise_frame_error(
                f"Unable to start FFmpeg low-resource retry for frame at "
                f"{timestamp_text}s from {video}; first return code "
                f"{first_return_code}: {exc}",
                staging,
                cause=exc,
            )
        if process.returncode != 0:
            _raise_frame_error(
                f"FFmpeg low-resource retry failed to extract frame at "
                f"{timestamp_text}s from {video} to {output}; first return "
                f"code {first_return_code}; retry return code "
                f"{process.returncode}; stderr excerpt: "
                f"{_stderr_excerpt(process.stderr)}",
                staging,
            )

    if process.returncode != 0:
        _raise_frame_error(
            f"FFmpeg failed to extract frame at {timestamp_text}s from "
            f"{video} to {output}; return code {process.returncode}; stderr "
            f"excerpt: {_stderr_excerpt(process.stderr)}",
            staging,
        )

    try:
        output_size = staging.stat().st_size
    except FileNotFoundError as exc:
        _raise_frame_error(
            f"FFmpeg reported success extracting frame at {timestamp_text}s "
            f"from {video}, but staging output is missing: {staging}",
            staging,
            exc,
        )
    except OSError as exc:
        _raise_frame_error(
            f"Unable to verify extracted frame at {timestamp_text}s from "
            f"{video} at {staging}: {exc}",
            staging,
            exc,
        )

    if output_size == 0:
        _raise_frame_error(
            f"FFmpeg reported success extracting frame at {timestamp_text}s "
            f"from {video}, but staging output is empty: {staging}",
            staging,
        )

    try:
        with staging.open("rb") as frame_file:
            start_marker = frame_file.read(2)
            frame_file.seek(-2, os.SEEK_END)
            end_marker = frame_file.read(2)
    except OSError as exc:
        _raise_frame_error(
            f"Unable to validate JPEG frame at {timestamp_text}s from "
            f"{video} at {staging}: {exc}",
            staging,
            exc,
        )

    if start_marker != b"\xff\xd8" or end_marker != b"\xff\xd9":
        _raise_frame_error(
            f"FFmpeg output for frame at {timestamp_text}s from {video} "
            f"is not a valid JPEG: {staging}",
            staging,
        )

    decode_command = [
        str(ffmpeg_bin),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(staging),
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        decode_process = subprocess.run(
            decode_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _raise_frame_error(
            f"FFmpeg timed out after {timeout_sec} seconds while attempting "
            f"to decode staged frame at {timestamp_text}s from {video}: "
            f"{staging}",
            staging,
            exc,
        )
    except OSError as exc:
        _raise_frame_error(
            f"Unable to decode staged frame at {timestamp_text}s from "
            f"{video} with FFmpeg at {staging}: {exc}",
            staging,
            exc,
        )

    if decode_process.returncode != 0:
        _raise_frame_error(
            f"FFmpeg failed to decode staged frame at {timestamp_text}s "
            f"from {video}; return code {decode_process.returncode}; stderr "
            f"excerpt: {_stderr_excerpt(decode_process.stderr)}",
            staging,
        )

    try:
        os.replace(staging, output)
    except OSError as exc:
        _raise_frame_error(
            f"Unable to publish extracted frame at {timestamp_text}s from "
            f"{video} to {output}: {exc}",
            staging,
            exc,
        )

    return output


def extract_frames_batch(
    ffmpeg_bin,
    video_path,
    targets,
    timeout_sec=DEFAULT_FFMPEG_TIMEOUT_SEC,
):
    """Decode one video stream and emit exact requested timestamps together."""
    if not targets:
        return []
    video = Path(video_path)
    output_targets = [(float(timestamp), Path(output)) for timestamp, output in targets]
    staging_paths = []
    filter_parts = []
    split_labels = [f"split_{index}" for index in range(len(output_targets))]
    filter_parts.append(
        f"[0:v]split={len(output_targets)}" + "".join(f"[{label}]" for label in split_labels)
    )
    for index, (timestamp, _output) in enumerate(output_targets):
        filter_parts.append(
            f"[{split_labels[index]}]trim=start={timestamp!r},setpts=PTS-STARTPTS[out_{index}]"
        )
    max_timestamp = max(timestamp for timestamp, _output in output_targets)
    for index, (_timestamp, output) in enumerate(output_targets):
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.stage{output.suffix or '.jpg'}")
        staging_paths.append(staging)
    command = [
        str(ffmpeg_bin), "-hide_banner", "-loglevel", "error",
        "-threads", "1", "-filter_threads", "1",
        "-t", repr(max_timestamp + 1.0), "-i", str(video),
        "-filter_complex", ";".join(filter_parts),
    ]
    for index, staging in enumerate(staging_paths):
        command.extend(["-map", f"[out_{index}]", "-frames:v", "1", "-q:v", "2", "-y", str(staging)])
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout_sec
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        for staging in staging_paths:
            staging.unlink(missing_ok=True)
        raise FfmpegError(f"FFmpeg batch extraction failed for {video}: {exc}") from exc
    if process.returncode != 0:
        for staging in staging_paths:
            staging.unlink(missing_ok=True)
        raise FfmpegError(
            f"FFmpeg batch extraction failed for {video}; return code {process.returncode}; "
            f"stderr excerpt: {_stderr_excerpt(process.stderr)}"
        )
    for staging, (_timestamp, output) in zip(staging_paths, output_targets):
        try:
            if staging.stat().st_size == 0:
                raise OSError("empty JPEG")
            with staging.open("rb") as frame_file:
                if frame_file.read(2) != b"\xff\xd8":
                    raise OSError("invalid JPEG start marker")
                frame_file.seek(-2, os.SEEK_END)
                if frame_file.read(2) != b"\xff\xd9":
                    raise OSError("invalid JPEG end marker")
            os.replace(staging, output)
        except (OSError, ValueError) as exc:
            for pending in staging_paths:
                pending.unlink(missing_ok=True)
            raise FfmpegError(f"FFmpeg batch output validation failed for {output}: {exc}") from exc
    return [output for _timestamp, output in output_targets]


def validate_timestamps(selected_peaks, video_duration):
    duration, duration_text = _canonical_seconds(
        video_duration, "video duration", InputDataError
    )
    if duration <= 0:
        raise InputDataError(
            f"video duration {video_duration!r} must be greater than 0"
        )

    for selected_peak in selected_peaks:
        timestamp = selected_peak.duration_sec
        try:
            normalized_timestamp, timestamp_text = _canonical_seconds(
                timestamp, "peak timestamp", InputDataError
            )
        except InputDataError as exc:
            raise InputDataError(
                f"peak timestamp {timestamp!r} is invalid for video "
                f"duration {duration_text}"
            ) from exc
        if normalized_timestamp < 0 or normalized_timestamp >= duration:
            raise InputDataError(
                f"peak timestamp {timestamp_text} must satisfy 0 <= timestamp "
                f"< video duration {duration_text}"
            )


def format_seconds_token(duration_sec):
    """Return a stable, filesystem-safe timestamp token."""
    duration, duration_text = _canonical_seconds(
        duration_sec, "frame timestamp", InputDataError
    )
    if duration < 0:
        raise InputDataError("frame timestamp cannot be negative")
    if "." in duration_text:
        duration_text = duration_text.rstrip("0").rstrip(".")
    whole, separator, fraction = duration_text.partition(".")
    token = whole.zfill(4)
    if separator:
        token += f"p{fraction}"
    return f"{token}s"


def format_frame_filename(rank_type, duration_sec, metric_value):
    """Return the deterministic JPEG filename for one selected peak."""
    if not isinstance(rank_type, str) or re.fullmatch(
        r"[A-Za-z0-9_-]+", rank_type
    ) is None:
        raise InputDataError(f"invalid frame rank type: {rank_type!r}")
    if metric_value is None:
        metric_token = "na"
    else:
        _, metric_text = _canonical_seconds(
            metric_value, "metric value", InputDataError
        )
        if "." in metric_text:
            metric_text = metric_text.rstrip("0").rstrip(".")
        metric_token = metric_text.replace("-", "m").replace(".", "p")
    return (
        f"{rank_type}_{format_seconds_token(duration_sec)}_"
        f"{metric_token}.jpg"
    )


def build_summary(
    material_id,
    input_json_path,
    video_path,
    selected_peaks,
    frame_paths,
    video_source=None,
    expected_peaks=None,
    missing_frames=None,
    additional_warnings=None,
):
    """Build the JSON-compatible result shared by both summary formats."""
    if len(selected_peaks) != len(frame_paths):
        raise ValueError("selected_peaks and frame_paths must have equal length")
    expected = list(selected_peaks if expected_peaks is None else expected_peaks)
    missing = list(missing_frames or [])
    peak_count = sum(
        peak.rank_type.startswith("peak_top_") for peak in expected
    )
    warnings = list(additional_warnings or [])
    if peak_count < 5:
        warnings.append(
            f"click curve has {peak_count} local peak(s); up to 5 are targeted"
        )
    summary = {
        "materialId": str(material_id),
        "inputJsonPath": str(input_json_path),
        "selectionRule": {
            "zero": "video_timestamp_0_seconds",
            "peaks": "top_5_local_peaks_by_metric_desc_then_time_asc",
            "plateau": "collapse_to_earliest_second",
            "boundariesExcluded": True,
        },
        "frames": [
            {
                "rankType": peak.rank_type,
                "durationSec": peak.duration_sec,
                "metricValue": peak.metric_value,
                "requestedTimestampSec": peak.duration_sec,
                "framePath": Path(frame_path).name,
            }
            for peak, frame_path in zip(selected_peaks, frame_paths)
        ],
        "expectedFrames": [
            {
                "rankType": peak.rank_type,
                "durationSec": peak.duration_sec,
                "metricValue": peak.metric_value,
                "requestedTimestampSec": peak.duration_sec,
            }
            for peak in expected
        ],
        "missingFrames": missing,
        "frameCoverage": {
            "targetFrameCount": 6,
            "expectedFrameCount": len(expected),
            "succeededFrameCount": len(selected_peaks),
            "expectedPeakCount": peak_count,
            "availablePeakCount": sum(
                peak.rank_type.startswith("peak_top_") for peak in selected_peaks
            ),
        },
        "warnings": warnings,
    }
    if video_source is None:
        summary["videoPath"] = str(Path(video_path))
    else:
        summary["videoSource"] = str(video_source)
    return summary


def _render_summary_markdown(summary):
    selection_rule = summary["selectionRule"]
    lines = [
        f"# 千川点击峰值画面：{summary['materialId']}",
        "",
        f"- RPA JSON: {summary['inputJsonPath']}",
        (
            f"- 视频来源: {summary['videoSource']}"
            if "videoSource" in summary
            else f"- 视频: {summary['videoPath']}"
        ),
        "",
        "## 选择规则",
        "",
        f"- zero: {selection_rule['zero']}",
        f"- peaks: {selection_rule['peaks']}",
        f"- plateau: {selection_rule['plateau']}",
        "- boundariesExcluded: "
        f"{str(selection_rule['boundariesExcluded']).lower()}",
        "",
        "| 类型 | 秒数 | 点击次数 | 请求截帧秒数 | 画面 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for frame in summary["frames"]:
        metric_value = (
            f"{frame['metricValue']:g}"
            if frame["metricValue"] is not None
            else "-"
        )
        lines.append(
            f"| {frame['rankType']} | {frame['durationSec']:g} | "
            f"{metric_value} | "
            f"{frame['requestedTimestampSec']:g} | {frame['framePath']} |"
        )
    if summary["warnings"]:
        lines.extend(["", "## 警告", ""])
        lines.extend(
            f"- {warning}" for warning in summary["warnings"]
        )
    return "\n".join(lines) + "\n"


def _remove_summary_staging(paths):
    errors = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def write_summaries(summary, result_dir):
    """Atomically publish JSON and Markdown after every frame exists."""
    result = Path(result_dir)
    try:
        result.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InputDataError(
            f"unable to create summary directory {result}: {exc}"
        ) from exc

    for frame in summary.get("frames", []):
        frame_path = Path(frame["framePath"])
        if not frame_path.is_absolute():
            frame_path = result / frame_path
        try:
            frame_size = frame_path.stat().st_size
        except FileNotFoundError as exc:
            raise InputDataError(f"frame is missing: {frame_path}") from exc
        except OSError as exc:
            raise InputDataError(
                f"unable to inspect frame {frame_path}: {exc}"
            ) from exc
        if frame_size == 0:
            raise InputDataError(f"frame is empty: {frame_path}")

    json_path = result / "summary.json"
    markdown_path = result / "summary.md"
    unique = uuid.uuid4().hex
    json_staging = result / f".summary.{unique}.json.tmp"
    markdown_staging = result / f".summary.{unique}.md.tmp"
    staging_paths = (json_staging, markdown_staging)
    try:
        json_staging.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_staging.write_text(
            _render_summary_markdown(summary), encoding="utf-8"
        )
        os.replace(markdown_staging, markdown_path)
        os.replace(json_staging, json_path)
    except (OSError, TypeError, ValueError) as exc:
        cleanup_errors = _remove_summary_staging(staging_paths)
        cleanup_detail = (
            f"; staging cleanup failed: {'; '.join(cleanup_errors)}"
            if cleanup_errors
            else ""
        )
        raise InputDataError(
            f"unable to write summaries in {result}: {exc}{cleanup_detail}"
        ) from exc

    return json_path, markdown_path


def _safe_component(value):
    text = str(value)
    cleaned = "".join(
        character
        if character.isalnum() or character in "-_"
        else "_"
        for character in text
    )
    return cleaned.strip("_") or "unknown-material"


def _remove_owned_paths(paths, purpose):
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise InputDataError(
                f"unable to remove stale {purpose} {path}: {exc}"
            ) from exc


def _read_owned_frame_paths(summary_path, result_dir):
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    if not isinstance(summary, dict) or not isinstance(
        summary.get("frames", []), list
    ):
        return set()

    owned_paths = set()
    for frame in summary.get("frames", []):
        if not isinstance(frame, dict):
            continue
        try:
            expected_name = format_frame_filename(
                frame.get("rankType"),
                frame.get("durationSec"),
                frame.get("metricValue"),
            )
        except InputDataError:
            continue
        if frame.get("framePath") == expected_name:
            owned_paths.add(result_dir / expected_name)
    return owned_paths


def process_local(
    input_json_path,
    video_path,
    output_dir,
    ffmpeg_bin=None,
    ffmpeg_timeout_sec=DEFAULT_FFMPEG_TIMEOUT_SEC,
):
    """Run the complete local JSON/video peak-frame pipeline."""
    input_path = Path(input_json_path)
    if not input_path.is_file():
        raise InputDataError(f"input JSON does not exist: {input_path}")
    try:
        records = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputDataError(
            f"input must contain valid JSON: {input_path}"
        ) from exc
    return process_records(
        records,
        input_path,
        video_path,
        output_dir,
        ffmpeg_bin=ffmpeg_bin,
        ffmpeg_timeout_sec=ffmpeg_timeout_sec,
    )


def process_records(
    records,
    input_label,
    video_path,
    output_dir,
    ffmpeg_bin=None,
    ffmpeg_timeout_sec=DEFAULT_FFMPEG_TIMEOUT_SEC,
    video_source=None,
):
    """Run the peak-frame pipeline with already-loaded RPA records."""
    video = Path(video_path)
    if not video.is_file():
        raise InputDataError(f"video does not exist or is not a file: {video}")
    material_id, points = parse_click_series(records)
    selected_peaks = select_peak_frames(points)
    timeout_sec, _ = _canonical_seconds(
        ffmpeg_timeout_sec, "FFmpeg timeout", FfmpegError
    )
    if timeout_sec <= 0:
        raise FfmpegError(
            f"FFmpeg timeout {ffmpeg_timeout_sec!r} must be greater than 0 seconds"
        )
    ffmpeg = resolve_ffmpeg(explicit=ffmpeg_bin)
    video_duration = probe_video_duration(
        ffmpeg, video, timeout_sec=timeout_sec
    )
    out_of_range_peaks = [
        peak for peak in selected_peaks
        if peak.duration_sec < 0 or peak.duration_sec >= video_duration
    ]
    selected_peaks = [
        peak for peak in selected_peaks
        if 0 <= peak.duration_sec < video_duration
    ]
    validate_timestamps(selected_peaks, video_duration)

    summary_material_id = material_id or video.stem
    result_dir = Path(output_dir) / _safe_component(summary_material_id)
    try:
        result_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InputDataError(
            f"unable to create result directory {result_dir}: {exc}"
        ) from exc

    summary_markers = (
        result_dir / "summary.json",
        result_dir / "summary.md",
    )
    previously_owned_frames = _read_owned_frame_paths(
        summary_markers[0], result_dir
    )
    _remove_owned_paths(summary_markers, "summary")

    successful_peaks = []
    frame_paths = []
    missing_frames = []
    target_paths = [
        (
            peak,
            result_dir / format_frame_filename(
                peak.rank_type, peak.duration_sec, peak.metric_value
            ),
        )
        for peak in selected_peaks
    ]
    try:
        extract_frames_batch(
            ffmpeg,
            video,
            [(peak.duration_sec, path) for peak, path in target_paths],
            timeout_sec=timeout_sec,
        )
    except FfmpegError as batch_error:
        for peak, frame_path in target_paths:
            try:
                extract_frame(ffmpeg, video, peak.duration_sec, frame_path, timeout_sec=timeout_sec)
            except FfmpegError as exc:
                missing_frames.append(
                    {
                        "rankType": peak.rank_type,
                        "durationSec": peak.duration_sec,
                        "metricValue": peak.metric_value,
                        "requestedTimestampSec": peak.duration_sec,
                        "errorType": type(exc).__name__,
                        "errorMessage": _stderr_excerpt(str(exc)),
                    }
                )
            else:
                successful_peaks.append(peak)
                frame_paths.append(frame_path)
        if not successful_peaks:
            missing_frames.append({"errorType": type(batch_error).__name__, "errorMessage": str(batch_error)})
    else:
        successful_peaks = [peak for peak, _path in target_paths]
        frame_paths = [path for _peak, path in target_paths]

    cover_succeeded = any(
        peak.rank_type == "zero_second" for peak in successful_peaks
    )
    peak_expected = any(
        peak.rank_type.startswith("peak_top_") for peak in selected_peaks
    )
    peak_succeeded = any(
        peak.rank_type.startswith("peak_top_") for peak in successful_peaks
    )
    if not cover_succeeded or (peak_expected and not peak_succeeded):
        raise FfmpegError(
            "frame coverage does not meet material success rule: "
            f"cover_succeeded={cover_succeeded}; "
            f"peak_expected={peak_expected}; peak_succeeded={peak_succeeded}"
        )

    current_frames = set(frame_paths)
    stale_frames = previously_owned_frames - current_frames
    _remove_owned_paths(stale_frames, "frame")

    summary = build_summary(
        summary_material_id,
        input_label,
        video,
        successful_peaks,
        frame_paths,
        video_source=video_source,
        expected_peaks=selected_peaks,
        missing_frames=missing_frames,
        additional_warnings=[
            "excluded %s curve peak(s) outside video duration %.6g seconds"
            % (len(out_of_range_peaks), video_duration)
        ] if out_of_range_peaks else None,
    )
    json_path, markdown_path = write_summaries(summary, result_dir)
    return result_dir, json_path, markdown_path


def _finite_number(value, field_name):
    return _canonical_seconds(value, field_name, InputDataError)[0]


def parse_click_series(records):
    if not isinstance(records, list):
        raise InputDataError("RPA root must be a list")

    values_by_second = {}
    material_ids = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InputDataError(f"RPA record at index {index} must be a dict")
        if (
            record.get("recordType") != "interaction_series"
            or record.get("interactionMetric") != "CLICK_COUNT"
        ):
            continue

        duration_sec = _finite_number(record.get("durationSec"), "durationSec")
        metric_value = _finite_number(record.get("metricValue"), "metricValue")
        if duration_sec < 0:
            raise InputDataError("durationSec cannot be negative")

        existing_value = values_by_second.get(duration_sec)
        if existing_value is not None and existing_value != metric_value:
            raise InputDataError(
                f"conflicting CLICK_COUNT values at second {duration_sec}"
            )
        values_by_second[duration_sec] = metric_value

        material_id = str(record.get("materialId") or "").strip()
        if material_id:
            material_ids.add(material_id)

    if not values_by_second:
        raise InputDataError("No CLICK_COUNT interaction series records found")
    if len(material_ids) > 1:
        raise InputDataError("multiple materialId values found in CLICK_COUNT records")

    points = [
        SeriesPoint(duration_sec, values_by_second[duration_sec])
        for duration_sec in sorted(values_by_second)
    ]
    material_id = next(iter(material_ids), "")
    return material_id, points


def find_local_peaks(points):
    plateaus = []
    for point in points:
        if plateaus and plateaus[-1].metric_value == point.metric_value:
            continue
        plateaus.append(
            Peak(float(point.duration_sec), float(point.metric_value))
        )

    peaks = []
    for index in range(1, len(plateaus) - 1):
        plateau = plateaus[index]
        if (
            plateau.metric_value > plateaus[index - 1].metric_value
            and plateau.metric_value > plateaus[index + 1].metric_value
        ):
            peaks.append(plateau)
    return peaks


def select_peak_frames(points, peak_limit=5):
    if not points:
        raise InputDataError("No local peaks found")
    zero_metric = next(
        (
            point.metric_value
            for point in points
            if point.duration_sec == 0
        ),
        None,
    )
    peaks = find_local_peaks(points)

    top_peaks = sorted(
        peaks, key=lambda peak: (-peak.metric_value, peak.duration_sec)
    )

    selected = [
        SelectedPeak(
            "zero_second",
            0.0,
            float(zero_metric) if zero_metric is not None else None,
        )
    ]
    selected.extend(
        SelectedPeak(
            f"peak_top_{rank}",
            float(peak.duration_sec),
            float(peak.metric_value),
        )
        for rank, peak in enumerate(top_peaks[:peak_limit], start=1)
    )
    return selected
