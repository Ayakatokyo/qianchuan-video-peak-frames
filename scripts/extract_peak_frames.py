#!/usr/bin/env python3
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
import uuid

from peak_frame_lib import (
    FfmpegError,
    InputDataError,
    process_local,
    process_records,
)
from rpa_adapter import (
    FUNCTION_CODE,
    RpaAdapterError,
    download_video,
    fetch_material_diagnosis,
    find_material_video_url,
)


DATE_RANGE_TYPES = (
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_15_DAYS",
    "LAST_WEEK",
    "THIS_MONTH",
    "LAST_MONTH",
    "CUSTOM",
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Extract video frames at Qianchuan CLICK_COUNT peaks."
    )
    parser.add_argument("--source", choices=("local", "rpa"), default="local")
    parser.add_argument("--input-json")
    parser.add_argument("--video")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg-bin")
    parser.add_argument("--shop-id")
    parser.add_argument("--material-id")
    parser.add_argument("--top-materials-json")
    parser.add_argument("--diagnosis-records-dir")
    parser.add_argument("--date-range-type", choices=DATE_RANGE_TYPES)
    parser.add_argument("--custom-start-date")
    parser.add_argument("--custom-end-date")
    return parser


def _top_material_ids(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RpaAdapterError(f"unable to read top materials JSON: {exc}") from exc
    materials = payload.get("materials") if isinstance(payload, dict) else None
    if not isinstance(materials, list) or not materials:
        raise RpaAdapterError(
            "top materials JSON must contain a nonempty materials list"
        )

    material_ids = []
    for material in materials:
        if not isinstance(material, dict):
            raise RpaAdapterError("top materials entries must be objects")
        material_id = str(material.get("materialId") or "").strip()
        if not material_id:
            raise RpaAdapterError("top materials entries require materialId")
        material_ids.append(material_id)
    return material_ids


def _write_diagnosis_records(records, records_dir, material_id):
    target_dir = Path(records_dir)
    target = target_dir / f"{material_id}.json"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpaAdapterError(
            f"unable to create diagnosis records directory {target_dir}: {exc}"
        ) from exc

    staging = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.json.tmp")
    try:
        staging.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except (OSError, TypeError, ValueError) as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise RpaAdapterError(
            f"unable to write diagnosis records for material {material_id}: {exc}"
        ) from exc
    return target


def _process_rpa_material(args, material_id):
    records = fetch_material_diagnosis(
        shop_id=args.shop_id,
        material_id=material_id,
        date_range_type=args.date_range_type,
        custom_start_date=args.custom_start_date,
        custom_end_date=args.custom_end_date,
    )
    if args.diagnosis_records_dir:
        _write_diagnosis_records(
            records, args.diagnosis_records_dir, material_id
        )
    video_url = find_material_video_url(records, material_id)
    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = Path(temp_dir) / f"{material_id}.mp4"
        download_video(video_url, video_path)
        return process_records(
            records,
            f"rpa://{FUNCTION_CODE}/{material_id}",
            video_path,
            args.output_dir,
            ffmpeg_bin=args.ffmpeg_bin,
        )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.source == "rpa":
            if not args.shop_id or not args.date_range_type or not (
                args.material_id or args.top_materials_json
            ):
                parser.error(
                    "--shop-id, --date-range-type, and either --material-id "
                    "or --top-materials-json are required for --source rpa"
                )
            if args.date_range_type == "CUSTOM" and not (
                args.custom_start_date and args.custom_end_date
            ):
                parser.error(
                    "CUSTOM requires --custom-start-date and "
                    "--custom-end-date"
                )
            material_ids = (
                _top_material_ids(args.top_materials_json)
                if args.top_materials_json
                else [args.material_id]
            )
            results = [
                _process_rpa_material(args, material_id)
                for material_id in material_ids
            ]
            result_dir, json_path, markdown_path = results[-1]

        else:
            if not args.input_json or not args.video:
                parser.error(
                    "--input-json and --video are required for local source"
                )
            result_dir, json_path, markdown_path = process_local(
                args.input_json,
                args.video,
                args.output_dir,
                ffmpeg_bin=args.ffmpeg_bin,
            )
    except InputDataError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FfmpegError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except RpaAdapterError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    print(f"result_dir={result_dir}")
    print(f"summary_json={json_path}")
    print(f"summary_md={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
