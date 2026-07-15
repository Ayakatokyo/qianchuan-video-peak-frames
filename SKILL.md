---
name: qianchuan-video-peak-frames
description: Use when extracting or reviewing frames at CLICK_COUNT peaks from 巨量千川素材分析、视频推直播单条诊断、Qianchuan RPA JSON, with a local video or a future connector-backed run.
---

# Qianchuan Video Peak Frames

## Overview

Extract the first chronological click peak and the later top three click peaks, then save their JPEG frames and JSON/Markdown summaries. Use the bundled Python workflow so peak and frame rules remain deterministic across macOS and Windows.

## Workflow

1. Read `references/rpa-output-contract.md` when validating fields or preparing connector parameters.
2. Use local mode while the online RPA adapter is unconfigured.
3. Create an isolated Python 3.9+ environment and install `requirements.txt`.
4. Run the CLI with RPA JSON, the matching local video, and an output directory.
5. Review every path in `summary.json`, the corresponding JPEG, `summary.md`, and all warnings before reporting success.

## Local Run

```bash
python scripts/extract_peak_frames.py \
  --input-json "/path/to/rpa-result.json" \
  --video "/path/to/material.mp4" \
  --output-dir "/path/to/output"
```

Pass `--ffmpeg-bin` when production manages FFmpeg separately. Keep paths as separate arguments; quoted paths containing spaces work on macOS and Windows.

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
| Online source | Do not simulate it; `--source rpa` is intentionally unconfigured |
| Regression check | Run `python -m unittest tests/test_peak_frames.py -v` |

## Common Mistakes

- Do not rank all peaks together; the earliest peak is fixed before later ranking.
- Do not treat the first or last sample as a peak.
- Do not fail merely because fewer than three later peaks exist; surface the warning.
- Do not claim success from filenames alone; inspect summaries and JPEGs.
- Do not download `videoUrl` until `scripts/rpa_adapter.py` is implemented.
