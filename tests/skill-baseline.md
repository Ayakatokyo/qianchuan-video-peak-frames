# Skill RED Baseline

## Evaluation prompt

Analyze /Users/shuzida/Desktop/FDE客户/小仙炖/千川短视频/千川-素材分析-视频推直播单条诊断.md.
Design the exact local command and deterministic processing rules you would use to take the first chronological CLICK_COUNT peak and the later top three peaks by click count, then extract JPEG frames from a local MP4 on both macOS and Windows. Do not modify the workspace. State dependencies, tie/plateau behavior, FFmpeg lookup, output files, and failure behavior.

## Baseline output

<!-- BEGIN VERBATIM BASELINE OUTPUT -->
Read-only analysis complete. Evidence: doc lines 164/171/173/174 define `recordType=interaction_series`, `interactionMetric=CLICK_COUNT`, `durationSec`, `metricValue`; lines 308 and 319-334 show multiple time points concept/sample. The doc does NOT define peak semantics.

Recommended approach (vs jq+shell or separate Bash/PowerShell, which drift cross-platform): one Python 3.10+ stdlib script that selects peaks and invokes FFmpeg with argv (never shell=True). Exact user-facing commands:
macOS:
`python3 "/absolute/path/extract_click_peak_frames.py" --input-json "/absolute/path/connector-output.json" --material-id "7647499280192372763" --video "/absolute/path/video.mp4" --output-dir "/absolute/path/peak_frames"`
Windows PowerShell:
`py -3 "C:\absolute\path\extract_click_peak_frames.py" --input-json "C:\absolute\path\connector-output.json" --material-id "7647499280192372763" --video "C:\absolute\path\video.mp4" --output-dir "C:\absolute\path\peak_frames"`
Optional exact override on either: `--ffmpeg <absolute executable>`.

Dependencies: readable UTF-8 JSON whose root is the connector's documented List/JSON array; readable local MP4; Python >=3.10 (stdlib only); FFmpeg (ffmpeg executable; ffprobe not necessary if post-extraction no-frame checking is used). Suggested installs: macOS `brew install ffmpeg`; Windows `winget install --id Gyan.FFmpeg -e`.

Deterministic data rules:
1. Parse JSON with `json.load(..., parse_int=Decimal, parse_float=Decimal)`; root MUST be list. Filter exact: `recordType == "interaction_series"`, `interactionMetric == "CLICK_COUNT"`, `materialId == CLI --material-id`. Do not fall back to nested Dimensions/Metrics, because top-level normalized fields are documented.
2. Require `durationSec` and `metricValue` to be JSON numbers (not bool/string), finite, >=0. Sort ascending by durationSec. Duplicate durationSec is an ambiguity => fail, even if values match. Require at least 2 samples.
3. Plateau: collapse each maximal adjacent run (in duration-sorted observations) having exactly equal Decimal metricValue. Keep `plateauStart`, `plateauEnd`, value. Representative/seek time is plateauStart (earliest time), so "first chronological" remains literal.
4. A run is a local peak iff it has >=1 neighboring run and its value is strictly greater than every existing neighbor. Thus internal runs must exceed both sides; start/end runs may be one-sided peaks; a single point or wholly constant series has no peak.
5. `first` = peak with smallest plateauStart. `later` = all remaining peaks (necessarily after first plateau); select exactly 3 ordered by `(-metricValue, plateauStart)`—click-count descending; equal counts break toward earlier time. A plateau is one peak and cannot occupy multiple slots.
6. Require first + 3 later peaks. If fewer, strict failure (do not silently emit fewer or duplicate frames).

Per-frame FFmpeg argv (same on both OS; Python substitutes resolved executable/paths/time):
`[FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", VIDEO, "-ss", DECIMAL_SECONDS, "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn", "-q:v", "2", "-f", "image2", OUTPUT_JPG]`
Putting `-ss` after `-i` makes FFmpeg decode/discard up to the time and choose the first decoded video frame at/after that timestamp (frame-accurate rather than keyframe-only fast seeking). Pass argv directly. Do not clamp out-of-range timestamps or reuse nearest frame. Semantic frame selection is deterministic; byte-identical JPEGs across machines require pinning the same FFmpeg version/build/codec.

FFmpeg lookup order: (1) `--ffmpeg`, which must resolve to an executable file; (2) `shutil.which("ffmpeg")`; (3) macOS `/opt/homebrew/bin/ffmpeg`, then `/usr/local/bin/ffmpeg`; (4) Windows `%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe`, `%ChocolateyInstall%\bin\ffmpeg.exe` (default `%ProgramData%\chocolatey\bin\ffmpeg.exe`), then `C:\ffmpeg\bin\ffmpeg.exe`. No recursive filesystem search. Run `<resolved> -version` and require exit 0.

Outputs (fixed stable names, selection metadata held in manifest):
`01_first_chronological.jpg`
`02_later_top01.jpg`
`03_later_top02.jpg`
`04_later_top03.jpg`
`peaks.json` (for each: role, clickRank where applicable, durationSec/seekTimeSec as canonical decimal string, metricValue, plateauStartSec, plateauEndSec; plus materialId, input/video canonical paths, and resolved ffmpeg/version).
Use output sibling staging dir `<output-dir>.tmp`; require both final and staging dirs absent. Extract all 4 there, require each subprocess exit 0 and each JPEG exists/nonempty (optionally check JPEG SOI/EOI), write UTF-8 `peaks.json` with sorted keys/newline, then rename staging dir atomically to final. On any exception/interrupt delete staging and exit nonzero; never leave partial final output or overwrite an existing output dir.

Suggested exact exit behavior: 2 = CLI/input/data validation; 3 = fewer than four qualifying peaks; 4 = FFmpeg lookup/preflight/extraction/no frame; 5 = output collision/I/O. All errors to stderr and no final output directory.

I ran in-memory synthetic checks: plateau merge, click-count tie sorted earlier first, one-sided endpoint, all-constant, and singleton; 5/5 passed. No workspace files modified.
<!-- END VERBATIM BASELINE OUTPUT -->

## Observable gaps against the approved design

- Boundary points: the approved design excludes boundary points, while the baseline treats start and end runs as eligible one-sided peaks.
- Sparse later peaks: the approved design emits all available frames with a warning when fewer than three later peaks exist, while the baseline requires exactly three later peaks and fails otherwise.
- Runtime and dependencies: the approved design targets Python 3.9+ with `imageio-ffmpeg`, while the baseline requires Python 3.10+ and a separately installed FFmpeg executable.
- Duplicate durations: the approved design deduplicates identical duplicate `(durationSec, metricValue)` observations and rejects only conflicting duplicates, while the baseline rejects every duplicate duration even when values match.
- FFmpeg lookup: the approved lookup order is `--ffmpeg-bin`, `IMAGEIO_FFMPEG_EXE`, `imageio_ffmpeg.get_ffmpeg_exe()`, then system `PATH`; the baseline uses `--ffmpeg`, then system `PATH`, followed by platform-specific paths, and omits the imageio lookup sources.
- Output contract: the approved design produces deterministic named JPEGs plus `summary.json` and `summary.md`; the baseline uses different JPEG names, emits `peaks.json`, and omits the Markdown summary.
- Execution boundary: the approved design makes local mode primary and reserves an explicit unconfigured online RPA adapter; the baseline does not describe that adapter boundary.
