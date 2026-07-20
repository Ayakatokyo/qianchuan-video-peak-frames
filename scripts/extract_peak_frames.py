#!/usr/bin/env python3
import argparse
import sys
import tempfile
from pathlib import Path

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
    parser.add_argument("--date-range-type", choices=DATE_RANGE_TYPES)
    parser.add_argument("--custom-start-date")
    parser.add_argument("--custom-end-date")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.source == "rpa":
            if not args.shop_id or not args.material_id or not args.date_range_type:
                parser.error(
                    "--shop-id, --material-id, and --date-range-type "
                    "are required for --source rpa"
                )
            if args.date_range_type == "CUSTOM" and not (
                args.custom_start_date and args.custom_end_date
            ):
                parser.error(
                    "CUSTOM requires --custom-start-date and "
                    "--custom-end-date"
                )
            records = fetch_material_diagnosis(
                shop_id=args.shop_id,
                material_id=args.material_id,
                date_range_type=args.date_range_type,
                custom_start_date=args.custom_start_date,
                custom_end_date=args.custom_end_date,
            )
            video_url = find_material_video_url(records, args.material_id)
            with tempfile.TemporaryDirectory() as temp_dir:
                video_path = Path(temp_dir) / f"{args.material_id}.mp4"
                download_video(video_url, video_path)
                result_dir, json_path, markdown_path = process_records(
                    records,
                    f"rpa://{FUNCTION_CODE}/{args.material_id}",
                    video_path,
                    args.output_dir,
                    ffmpeg_bin=args.ffmpeg_bin,
                )

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
