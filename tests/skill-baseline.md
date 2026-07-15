# Skill RED Baseline

## Evaluation prompt

Analyze /Users/shuzida/Desktop/FDE客户/小仙炖/千川短视频/千川-素材分析-视频推直播单条诊断.md.
Design the exact local command and deterministic processing rules you would use to take the first chronological CLICK_COUNT peak and the later top three peaks by click count, then extract JPEG frames from a local MP4 on both macOS and Windows.
Do not modify the workspace.
State dependencies, tie/plateau behavior, FFmpeg lookup, output files, and failure behavior.

## Baseline output

## Proposed CLI

The Markdown defines the connector result as a top-level `List[Dict]`; its embedded sample has only one `CLICK_COUNT` point, so the command must consume the real connector export JSON, not the documentation file.

macOS:

```bash
python3 "/path/to/extract_click_peak_frames.py" \
  --input-json "/absolute/path/diagnosis.json" \
  --video "/absolute/path/material.mp4" \
  --output-dir "/absolute/path/peak-frames"
```

Windows PowerShell:

```powershell
py -3 "C:\path\to\extract_click_peak_frames.py" `
  --input-json "C:\absolute\path\diagnosis.json" `
  --video "C:\absolute\path\material.mp4" `
  --output-dir "C:\absolute\path\peak-frames"
```

Optional explicit binary override on either platform:

```text
--ffmpeg "/absolute/path/to/ffmpeg[.exe]"
```

## Dependencies and binary lookup

- Python 3.10+ using only the standard library.
- FFmpeg, including `ffprobe`.
- Lookup order:
  1. `--ffmpeg`
  2. `FFMPEG` environment variable
  3. `shutil.which("ffmpeg")`
  4. macOS: `/opt/homebrew/bin/ffmpeg`, then `/usr/local/bin/ffmpeg`
  5. Windows: `%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe`, then `C:\ffmpeg\bin\ffmpeg.exe`
- `ffprobe` must exist beside the selected `ffmpeg`, or be discoverable through `PATH`. Missing either binary is a fatal error before output creation.

Typical installation:

```bash
brew install python ffmpeg
```

```powershell
winget install --id Python.Python.3.12 --exact
winget install --id Gyan.FFmpeg --exact
```

## Deterministic selection rules

1. Require the JSON root to be an array.
2. Retain only records where:
   - `recordType == "interaction_series"`
   - `interactionMetric == "CLICK_COUNT"`
3. Parse `durationSec` and `metricValue` as finite decimal numbers. Reject booleans, negative durations, negative click counts, nulls, NaN, and infinities.
4. For duplicate durations:
   - Identical `(durationSec, metricValue)` rows are deduplicated.
   - Different values at the same duration are a fatal ambiguity.
5. Sort points by ascending `durationSec`. Do not smooth, interpolate, or round values before peak detection.
6. Collapse every adjacent run of equal `metricValue` into one plateau. A plateau is a peak when its value is strictly greater than each existing neighboring value.
   - A boundary plateau may be a peak if it is strictly greater than its sole neighbor.
   - A series containing only one plateau has no peak.
   - The plateau’s representative frame time is its earliest `durationSec`.
7. The “first chronological peak” is the peak with the smallest plateau start time.
8. “Later” means a peak whose plateau starts strictly after the first peak plateau ends.
9. Rank later peaks by:
   1. descending click count;
   2. ascending plateau start time for equal click counts;
   3. ascending plateau end time as the final deterministic tie-break.
10. Select exactly the first chronological peak plus the first three ranked later peaks. If fewer than three later peaks exist, fail rather than silently returning fewer than four frames.

## Frame extraction

First validate the MP4 with `ffprobe` and obtain its duration. Every selected representative time must satisfy `0 <= time < video_duration`; an out-of-range time is fatal.

For each selected time `T`, invoke FFmpeg as an argument array, not through shell interpolation:

```text
ffmpeg -nostdin -hide_banner -loglevel error -i VIDEO -ss T -map 0:v:0 -an -sn -dn -frames:v 1 -q:v 2 -y TEMP.jpg
```

Placing `-ss` after `-i` uses accurate decoding. The selected image is the first decoded video frame at or after `T`. Pass `T` as the canonical decimal string from the JSON parser, avoiding float-derived formatting drift.

## Outputs

Write into a temporary sibling directory, then rename into the requested directory only after all four extractions succeed:

```text
01_first_chronological.jpg
02_later_top_1.jpg
03_later_top_2.jpg
04_later_top_3.jpg
peaks.json
```

`peaks.json` records, for each JPEG:

```json
{
  "file": "02_later_top_1.jpg",
  "selection": "later_top_1",
  "durationSec": "12.5",
  "plateauEndSec": "13.0",
  "metricValue": "87"
}
```

The later JPEG numbering reflects click-count rank, not chronological order.

## Failure behavior

Exit nonzero and leave no new final output directory when:

- JSON is malformed or has the wrong root type.
- No valid `CLICK_COUNT` series exists.
- Duplicate durations conflict.
- Fewer than one first peak plus three later peaks exist.
- The MP4 is absent, unreadable, has no video stream, or cannot be probed.
- A target timestamp is outside the video.
- FFmpeg/ffprobe cannot be found.
- Any FFmpeg invocation fails or produces an empty/non-JPEG output.
- The requested output directory already exists, unless an explicit `--overwrite` flag is supplied.

On failure, delete the temporary directory and print the precise validation or FFmpeg error to stderr.

## Observable gaps against the approved design

- Boundary points: the approved design excludes boundary points, while the baseline allows a boundary plateau to qualify as a peak.
- Sparse later peaks: the approved design emits all available frames with a warning when fewer than three later peaks exist, while the baseline fails the run.
- Runtime and dependencies: the approved design targets Python 3.9+ with `imageio-ffmpeg`, while the baseline requires Python 3.10+ plus separately installed `ffmpeg` and `ffprobe` binaries.
- FFmpeg lookup: the approved lookup order is `--ffmpeg-bin`, `IMAGEIO_FFMPEG_EXE`, `imageio_ffmpeg.get_ffmpeg_exe()`, then system `PATH`; the baseline uses a different flag, environment variable, lookup sequence, and platform-specific fallback paths.
- Output contract: the approved design produces deterministic named JPEGs plus `summary.json` and `summary.md`; the baseline emits JPEGs plus `peaks.json` and omits the Markdown summary.
- Execution boundary: the approved design makes local mode primary and reserves an explicit unconfigured online RPA adapter; the baseline does not describe that adapter boundary.
