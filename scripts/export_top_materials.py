#!/usr/bin/env python3
"""Export ranked Qianchuan materials from one live-video report query."""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from asset_metrics_lib import (
    AssetMetricsAggregator,
    AssetMetricsError,
    build_asset_facts_payload,
)
from qianchuan_report_client import ReportClientError, fetch_material_report
from top_materials_lib import (
    MaterialAggregator,
    MaterialDataError,
    build_output,
    validate_material_origin_data,
)


def build_parser():
    parser = argparse.ArgumentParser(description="Export top Qianchuan materials by cost.")
    parser.add_argument("--shop-id", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-output-dir", required=True)
    parser.add_argument("--asset-facts-output")
    parser.add_argument("--name-regex")
    parser.add_argument("--material-type", action="append", default=[])
    parser.add_argument("--advertiser-id")
    parser.add_argument("--top-n", type=int, default=50)
    return parser


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("dates must use YYYY-MM-DD") from error


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
        asset_facts_output = (
            Path(args.asset_facts_output) if args.asset_facts_output else None
        )
        if asset_facts_output is not None and asset_facts_output.exists():
            raise ValueError("asset facts output path already exists")
        aggregator = MaterialAggregator(pattern, args.material_type, args.top_n)
        metrics_aggregator = (
            AssetMetricsAggregator(pattern, args.material_type)
            if asset_facts_output is not None else None
        )
        records = fetch_material_report(
            args.shop_id,
            args.advertiser_id,
            start_date.isoformat(),
            end_date.isoformat(),
            output_dir=Path(args.source_output_dir),
        )
        skipped_source_records = []
        for source_row_index, record in enumerate(records):
            raw_record = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            try:
                validate_material_origin_data(raw_record)
                if metrics_aggregator is not None:
                    metrics_aggregator.add_raw_origin_data(raw_record)
                aggregator.add_raw_origin_data(raw_record)
            except (AssetMetricsError, MaterialDataError) as error:
                skipped_source_records.append({
                    "sourceRowIndex": source_row_index,
                    "materialId": _record_material_id(record),
                    "reason": str(error),
                })
        materials, excluded = aggregator.select()
        payload = build_output(
            start_date.isoformat(), end_date.isoformat(), pattern,
            args.material_type, args.advertiser_id, materials, excluded,
            skipped_source_records,
        )
        _write_atomic_json(output, payload)
        if metrics_aggregator is not None:
            asset_payload = build_asset_facts_payload(
                start_date.isoformat(),
                end_date.isoformat(),
                pattern,
                args.material_type,
                args.advertiser_id,
                metrics_aggregator.facts(),
            )
            _write_atomic_json(asset_facts_output, asset_payload)
    except (
        AssetMetricsError,
        MaterialDataError,
        ReportClientError,
        ValueError,
        re.error,
    ) as error:
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


def _record_material_id(record):
    try:
        value = record["dimensions"]["material_id"]["Value"]
    except (KeyError, TypeError):
        return None
    if isinstance(value, bool) or value is None:
        return None
    material_id = str(value).strip()
    return material_id or None


if __name__ == "__main__":
    raise SystemExit(main())
