#!/usr/bin/env python3
"""Export ranked Qianchuan materials from the read-only asset table."""

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from asset_table_client import AssetTableClient, AssetTableError
from top_materials_lib import MaterialAggregator, MaterialDataError, build_output


TABLE_NAME = "ods_api_jlqc_uni_overall_roi_live_video_info_du"


def build_parser():
    parser = argparse.ArgumentParser(description="Export top Qianchuan materials by cost.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--name-regex")
    parser.add_argument("--material-type", action="append", default=[])
    parser.add_argument("--top-n", type=int, default=50)
    return parser


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("dates must use YYYY-MM-DD") from error


def iter_dates(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current.isoformat()
        current += timedelta(days=1)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        start_date = parse_date(args.start_date)
        end_date = parse_date(args.end_date)
        if start_date > end_date:
            raise ValueError("start date must not be after end date")
        if args.top_n <= 0:
            raise ValueError("top-n must be positive")
        pattern = re.compile(args.name_regex) if args.name_regex else None
        output = Path(args.output)
        if output.exists():
            raise ValueError("output path already exists")
        base_url = os.environ.get("ENV_BACKEND_HOST", "")
        session_token = os.environ.get("YCSESSIONID") or os.environ.get("YUCE_AUTHORIZATION", "")
        if not base_url or not session_token:
            raise ValueError("ENV_BACKEND_HOST and a session environment variable are required")

        client = AssetTableClient(base_url, session_token)
        physical_table = client.discover_table(TABLE_NAME)
        aggregator = MaterialAggregator(pattern, args.material_type, args.top_n)
        for partition_date in iter_dates(start_date, end_date):
            for origin_data in client.iter_origin_data(physical_table, partition_date):
                aggregator.add_raw_origin_data(origin_data)
        materials, excluded = aggregator.select()
        payload = build_output(
            start_date.isoformat(), end_date.isoformat(), pattern,
            args.material_type, materials, excluded,
        )
        _write_atomic_json(output, payload)
    except (AssetTableError, MaterialDataError, ValueError, re.error) as error:
        print("top material export failed: %s" % error, file=sys.stderr)
        return 1
    print("top_materials_json=%s" % output)
    return 0


def _write_atomic_json(output, payload):
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".%s." % output.name, suffix=".tmp", dir=str(output.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary_path, output)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
