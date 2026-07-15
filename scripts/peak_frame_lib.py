from dataclasses import dataclass
import math


class PeakFrameError(Exception):
    """Base error for expected peak-frame failures."""


class InputDataError(PeakFrameError):
    """Raised when RPA input violates the expected data contract."""


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


def _finite_number(value, field_name):
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

        material_id = record.get("materialId")
        if material_id:
            material_ids.add(str(material_id))

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
