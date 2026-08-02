"""Business use cases for turning RPA material facts into retained frames."""

import tempfile
import time
from pathlib import Path


def process_rpa_material(
    args,
    material_id,
    *,
    preflight_records=None,
    fetch_records,
    diagnosis_csv_dir,
    diagnosis_records_dir,
    write_records,
    find_video,
    download_video,
    process_records,
    artifact_path,
    function_code,
):
    timings = {
        "rpaFetchSec": 0.0,
        "diagnosisRecordWriteSec": 0.0,
        "videoDownloadSec": 0.0,
        "frameExtractionSec": 0.0,
        "totalSec": 0.0,
    }
    total_started_at = time.perf_counter()

    def timed(field, function, *call_args, **call_kwargs):
        started_at = time.perf_counter()
        try:
            return function(*call_args, **call_kwargs)
        finally:
            timings[field] = round(max(0.0, time.perf_counter() - started_at), 6)

    try:
        records = preflight_records
        if records is None:
            records = timed(
                "rpaFetchSec",
                fetch_records,
                shop_id=args.shop_id,
                material_id=material_id,
                date_range_type=args.date_range_type,
                custom_start_date=args.custom_start_date,
                custom_end_date=args.custom_end_date,
                diagnosis_csv_dir=diagnosis_csv_dir,
            )
        if diagnosis_records_dir:
            timed(
                "diagnosisRecordWriteSec",
                write_records,
                records,
                diagnosis_records_dir,
                material_id,
            )
        video_url = find_video(records, material_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = artifact_path(temp_dir, material_id, ".mp4")
            timed("videoDownloadSec", download_video, video_url, video_path)
            result = timed(
                "frameExtractionSec",
                process_records,
                records,
                f"rpa://{function_code}/{material_id}",
                video_path,
                args.output_dir,
                ffmpeg_bin=args.ffmpeg_bin,
                ffmpeg_timeout_sec=args.ffmpeg_timeout_sec,
                video_source="rpa-temporary-download",
            )
        return result, timings
    finally:
        timings["totalSec"] = round(
            max(0.0, time.perf_counter() - total_started_at), 6
        )
