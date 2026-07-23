import ast
import csv
import io
import json
import math
from pathlib import Path


FIELD_DELIMITER = "\x01"
RECORD_PREFIX = "\x02"
STRUCTURED_FIELDS = ("Dimensions", "Metrics", "Fields")


class RpaCsvError(ValueError):
    """Raised when an RPA diagnostic CSV cannot form usable records."""


def _error(source, row_number, message):
    return RpaCsvError(f"RPA CSV {source}, row {row_number}: {message}")


def _parse_number(value, field_name, source, row_number):
    if value in (None, ""):
        return value
    if isinstance(value, bool):
        raise _error(source, row_number, f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error(
            source, row_number, f"{field_name} must be a finite number"
        ) from exc
    if not math.isfinite(number):
        raise _error(source, row_number, f"{field_name} must be a finite number")
    return number


def _parse_structured_cell(value, field_name, source, row_number):
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError) as exc:
            raise _error(
                source, row_number, f"{field_name} must be a structured object"
            ) from exc
    if not isinstance(parsed, dict):
        raise _error(source, row_number, f"{field_name} must be a structured object")
    return parsed


def _normalize_crowd_metric(record, source, row_number):
    if (
        record.get("recordType") != "crowd_analysis"
        or record.get("displayMetric") != "SHOW_COUNT"
        or record.get("metricValue") not in (None, "")
    ):
        return

    metric_values = []
    for metric in record.get("Metrics", {}).values():
        if isinstance(metric, dict) and metric.get("Value") not in (None, ""):
            metric_values.append(
                _parse_number(metric["Value"], "Metrics.Value", source, row_number)
            )
    if not metric_values:
        raise _error(source, row_number, "SHOW_COUNT crowd record has no numeric Metrics.Value")
    if len(metric_values) > 1:
        raise _error(source, row_number, "multiple numeric Metrics.Value entries")
    record["metricValue"] = metric_values[0]


def _normalize_record(record, source, row_number):
    for field_name in STRUCTURED_FIELDS:
        if field_name in record:
            record[field_name] = _parse_structured_cell(
                record[field_name], field_name, source, row_number
            )
    for field_name in ("durationSec", "metricValue"):
        if field_name in record and record[field_name] not in (None, ""):
            record[field_name] = _parse_number(
                record[field_name], field_name, source, row_number
            )
    _normalize_crowd_metric(record, source, row_number)
    return record


def _remove_record_prefixes(text):
    if text.startswith(RECORD_PREFIX):
        text = text[1:]
    return text.replace("\r\n" + RECORD_PREFIX, "\r\n").replace(
        "\n" + RECORD_PREFIX, "\n"
    )


def parse_diagnosis_csv(path):
    """Parse a Qianchuan RPA diagnostic CSV into normalized records."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise RpaCsvError(f"unable to read RPA CSV {source}: {exc}") from exc

    if FIELD_DELIMITER not in text.partition("\n")[0]:
        raise RpaCsvError(f"RPA CSV {source} must use the \\x01 field delimiter")

    reader = csv.DictReader(io.StringIO(_remove_record_prefixes(text)), delimiter=FIELD_DELIMITER)
    headers = reader.fieldnames
    if not headers or any(not header for header in headers):
        raise RpaCsvError(f"RPA CSV {source} has an invalid header")
    if len(headers) != len(set(headers)):
        raise RpaCsvError(f"RPA CSV {source} has duplicate header names")

    records = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise _error(source, row_number, "has more values than header columns")
        if not any(value not in (None, "") for value in row.values()):
            continue
        records.append(_normalize_record(dict(row), source, row_number))
    if not records:
        raise RpaCsvError(f"RPA CSV {source} has no records")
    return records
