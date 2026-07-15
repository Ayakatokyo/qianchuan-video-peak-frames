from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shutil
import subprocess


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
    metric_value: float


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


def probe_video_duration(ffmpeg_bin, video_path, timeout_sec=30.0):
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


def _remove_partial_output(output_path):
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass


def extract_frame(
    ffmpeg_bin,
    video_path,
    timestamp_sec,
    output_path,
    timeout_sec=30.0,
):
    video = Path(video_path)
    output = Path(output_path)
    timestamp_text = f"{timestamp_sec:.6f}"
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
        str(output),
    ]

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        _remove_partial_output(output)
        raise FfmpegError(
            f"FFmpeg timed out after {timeout_sec} seconds while extracting "
            f"frame at {timestamp_text}s from {video} to {output}"
        ) from exc
    except OSError as exc:
        _remove_partial_output(output)
        raise FfmpegError(
            f"Unable to extract frame at {timestamp_text}s from {video} "
            f"to {output} with FFmpeg: {exc}"
        ) from exc

    if process.returncode != 0:
        _remove_partial_output(output)
        raise FfmpegError(
            f"FFmpeg failed to extract frame at {timestamp_text}s from "
            f"{video} to {output}; return code {process.returncode}; stderr "
            f"excerpt: {_stderr_excerpt(process.stderr)}"
        )

    try:
        output_size = output.stat().st_size
    except FileNotFoundError:
        raise FfmpegError(
            f"FFmpeg reported success extracting frame at {timestamp_text}s "
            f"from {video}, but output is missing: {output}"
        )
    except OSError as exc:
        _remove_partial_output(output)
        raise FfmpegError(
            f"Unable to verify extracted frame at {timestamp_text}s from "
            f"{video} at {output}: {exc}"
        ) from exc

    if output_size == 0:
        _remove_partial_output(output)
        raise FfmpegError(
            f"FFmpeg reported success extracting frame at {timestamp_text}s "
            f"from {video}, but output is empty: {output}"
        )

    return output


def validate_timestamps(selected_peaks, video_duration):
    try:
        duration = _finite_number(
            video_duration, f"video duration {video_duration!r}"
        )
    except InputDataError:
        raise
    if duration <= 0:
        raise InputDataError(
            f"video duration {video_duration!r} must be greater than 0"
        )

    for selected_peak in selected_peaks:
        timestamp = selected_peak.duration_sec
        try:
            normalized_timestamp = _finite_number(
                timestamp, f"peak timestamp {timestamp!r}"
            )
        except InputDataError as exc:
            raise InputDataError(
                f"peak timestamp {timestamp!r} is invalid for video "
                f"duration {duration}"
            ) from exc
        if normalized_timestamp < 0 or normalized_timestamp >= duration:
            raise InputDataError(
                f"peak timestamp {timestamp!r} must satisfy 0 <= timestamp "
                f"< video duration {duration}"
            )


def _finite_number(value, field_name):
    if isinstance(value, bool):
        raise InputDataError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputDataError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise InputDataError(f"{field_name} must be a finite number")
    return number


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


def select_peak_frames(peaks, later_limit=3):
    if not peaks:
        raise InputDataError("No local peaks found")

    first_peak = min(peaks, key=lambda peak: peak.duration_sec)
    later_peaks = [
        peak for peak in peaks if peak.duration_sec > first_peak.duration_sec
    ]
    later_peaks.sort(key=lambda peak: (-peak.metric_value, peak.duration_sec))

    selected = [
        SelectedPeak(
            "first_peak",
            float(first_peak.duration_sec),
            float(first_peak.metric_value),
        )
    ]
    selected.extend(
        SelectedPeak(
            f"later_top_{rank}",
            float(peak.duration_sec),
            float(peak.metric_value),
        )
        for rank, peak in enumerate(later_peaks[:later_limit], start=1)
    )
    return selected
