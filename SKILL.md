---
name: qianchuan-video-peak-frames
description: Use when extracting or reviewing frames at CLICK_COUNT peaks from 巨量千川素材分析、视频推直播单条诊断、Qianchuan RPA JSON, either from local JSON/video files or the Yuce RPA connector.
---

# Qianchuan Video Peak Frames

## Overview

Extract the first chronological click peak and the later top three click peaks, then save their JPEG frames and JSON/Markdown summaries. Use the bundled Python workflow so peak and frame rules remain deterministic across macOS and Windows.

## Workflow

1. Read `references/rpa-output-contract.md` when validating fields or preparing connector parameters.
2. Use local mode when a matching video is already available.
3. Create an isolated Python 3.9+ environment and install `requirements.txt`.
4. For an online TOP-material batch, confirm `ENV_BACKEND_HOST`, `YUCE_AUTHORIZATION`, `YUCE_SESSION_ID`, and the target `shop_id`.
5. Select TOP materials from the asset table before invoking RPA. Use one explicit inclusive start/end date range for both operations and do not reuse a selection JSON from another run or period.
6. Review `top_materials.json`, every path in each `summary.json`, the corresponding JPEG, `summary.md`, and all warnings before reporting success.

## Local Run

```bash
python scripts/extract_peak_frames.py \
  --input-json "/path/to/rpa-result.json" \
  --video "/path/to/material.mp4" \
  --output-dir "/path/to/output"
```

Pass `--ffmpeg-bin` when production manages FFmpeg separately. Keep paths as separate arguments; quoted paths containing spaces work on macOS and Windows.

## Batch TOP Materials Run

Choose one empty run directory. The selection JSON is a required, retained batch artifact.

```bash
python scripts/export_top_materials.py \
  --start-date 2026-06-01 \
  --end-date 2026-06-07 \
  --top-n 50 \
  --output "/path/to/run/top_materials.json"
```

Before RPA, inspect `top_materials.json`. Stop when `materials` is empty, an entry lacks `materialId`, or `actualStartDate`/`actualEndDate` differs from the requested range.

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/peak-frames"
```

Retain `top_materials.json`, normalized records, peak-frame summaries, JPEGs, and warnings. The current RPA `result_type=files` CSV-envelope response is blocking until its adapter integration is completed; do not report it as successful.

## Single-Material RPA Debug Run

Use this only to diagnose or replay one already known material. `--material-id` is not an alternative selection method for a TOP-material batch run.

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --material-id "7647499280192372763" \
  --date-range-type "LAST_7_DAYS" \
  --output-dir "/path/to/output"
```

Use `CUSTOM` with both `--custom-start-date` and `--custom-end-date`. The adapter calls the fixed Yuce connector `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis`, downloads `material_info.videoUrl` to a temporary video file, then reuses the same local peak and frame pipeline.

## Selection Contract

- Read only `interaction_series` records whose metric is `CLICK_COUNT`.
- Exclude the first and last time-series points.
- Collapse an equal-valued plateau into one peak at its earliest second.
- Select the earliest peak first.
- Rank only later peaks by clicks descending, then second ascending; take three.
- If fewer later peaks exist, return available frames with a warning. Never fabricate peaks.

## Quick Reference

| Need | Action |
| --- | --- |
| Default FFmpeg | Install `imageio-ffmpeg` from `requirements.txt` |
| Managed FFmpeg | Add `--ffmpeg-bin "/path/to/ffmpeg"` |
| Local result | Inspect `<output>/<material-id>/summary.json` |
| RPA accounts | Use `scripts/rpa_adapter.py` helpers or the Yuce connector account list to find `shop_id` |
| TOP-material batch | Run asset-table selection first, then pass its run-local `top_materials.json` to RPA with the same CUSTOM dates |
| Single-material debug | Pass `--source rpa --shop-id ... --material-id ... --date-range-type ...` |
| Regression check | Run `python -m unittest tests/test_peak_frames.py -v` |

## Common Mistakes

- Do not rank all peaks together; the earliest peak is fixed before later ranking.
- Do not treat the first or last sample as a peak.
- Do not fail merely because fewer than three later peaks exist; surface the warning.
- Do not claim success from filenames alone; inspect summaries and JPEGs.
- Do not handwrite `platform` or `function_code`; the adapter fixes them to the 巨量千川 single-material diagnosis connector.
- Do not call batch RPA before asset-table selection or use `--material-id` to bypass it.
- Do not use different ranking and diagnosis date ranges.
- Do not overwrite or discard the run-local `top_materials.json`.
- Treat an RPA `result_type=files` response as a blocking adapter limitation until CSV-envelope integration is available.
