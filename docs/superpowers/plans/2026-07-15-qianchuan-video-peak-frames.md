# Qianchuan Video Peak Frames Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-local Codex skill that selects the first chronological CLICK_COUNT peak plus the later top three peaks from Qianchuan RPA JSON and extracts their JPEG frames from a local video on macOS and Windows.

**Architecture:** Keep peak selection and FFmpeg operations in a deterministic Python library, with a thin CLI for local/RPA input selection and a separate unconfigured RPA adapter. Resolve FFmpeg through an explicit path, environment, `imageio-ffmpeg`, or PATH, then reuse the same local processing pipeline when the online connector becomes available.

**Tech Stack:** Python 3.9+, standard library (`argparse`, `dataclasses`, `json`, `pathlib`, `subprocess`, `unittest`), `imageio-ffmpeg`, FFmpeg, Codex Skill metadata.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `SKILL.md` | Triggering and operational workflow for future Codex sessions |
| `agents/openai.yaml` | User-facing skill name, summary, and default prompt |
| `requirements.txt` | Cross-platform runtime dependency declaration |
| `scripts/peak_frame_lib.py` | Input parsing, peak selection, FFmpeg resolution/probing/extraction, and summaries |
| `scripts/extract_peak_frames.py` | CLI argument parsing and orchestration |
| `scripts/rpa_adapter.py` | Online connector interface and explicit unconfigured error |
| `references/rpa-output-contract.md` | Minimal RPA fields and date-range contract needed by the skill |
| `tests/test_peak_frames.py` | Unit and integration tests for library and CLI behavior |
| `tests/skill-baseline.md` | Skill RED-phase prompt, observed baseline behavior, and later comparison |

## Task 1: Capture the Skill RED Baseline

**Files:**
- Create: `tests/skill-baseline.md`

- [ ] **Step 1: Prepare a clean evaluation prompt**

Use a fresh subagent without access to a `qianchuan-video-peak-frames` skill. Pass only the source RPA document path and this user-style request:

```text
Analyze /Users/shuzida/Desktop/FDE客户/小仙炖/千川短视频/千川-素材分析-视频推直播单条诊断.md.
Design the exact local command and deterministic processing rules you would use to take the first chronological CLICK_COUNT peak and the later top three peaks by click count, then extract JPEG frames from a local MP4 on both macOS and Windows. Do not modify the workspace. State dependencies, tie/plateau behavior, FFmpeg lookup, output files, and failure behavior.
```

- [ ] **Step 2: Run the scenario without the skill**

Spawn one fresh subagent with the prompt above. Do not pass this design or implementation plan. Save its response verbatim under `## Baseline output` in `tests/skill-baseline.md`.

- [ ] **Step 3: Record observable gaps**

Under `## Baseline gaps`, record only differences observable against the approved design, such as an undefined plateau rule, global top-four selection, macOS-only tooling, shell-dependent commands, missing RPA/local boundary, or no verification of the extracted frame time. If no gap appears, create a second variation that adds a Windows path containing spaces and an equal-valued plateau, then record that result.

- [ ] **Step 4: Commit the RED evidence**

```bash
git add tests/skill-baseline.md
git commit -m "test: capture peak frame skill baseline"
```

Expected: commit contains no `SKILL.md` or production script.

## Task 2: Initialize the Skill and Python Environment

**Files:**
- Create: `SKILL.md`
- Create: `agents/openai.yaml`
- Create: `requirements.txt`
- Create directories: `scripts/`, `references/`

- [ ] **Step 1: Initialize the skill with the official scaffold**

Run from the parent directory because the target directory already exists:

```bash
python3 /Users/shuzida/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  qianchuan-video-peak-frames \
  --path /Users/shuzida/Desktop/FDE客户/小仙炖 \
  --resources scripts,references \
  --interface 'display_name=Qianchuan Video Peak Frames' \
  --interface 'short_description=Extract frames at Qianchuan click peaks' \
  --interface 'default_prompt=Use $qianchuan-video-peak-frames to extract the first click peak and the later top three peak frames from local Qianchuan RPA JSON and video.'
```

If the initializer refuses a non-empty target, initialize into a temporary sibling directory, then use `apply_patch` to add only the generated `SKILL.md` and `agents/openai.yaml` content to the existing repository. Do not overwrite `docs/` or `tests/`.

- [ ] **Step 2: Add the runtime dependency declaration**

Create `requirements.txt`:

```text
imageio-ffmpeg>=0.5.1,<1
```

- [ ] **Step 3: Create and populate an isolated virtual environment**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Expected: `python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"` prints an existing executable path.

- [ ] **Step 4: Ignore local runtime artifacts**

Create `.gitignore`:

```text
.venv/
__pycache__/
*.pyc
.test-output/
```

- [ ] **Step 5: Commit the scaffold**

```bash
git add SKILL.md agents/openai.yaml requirements.txt .gitignore
git commit -m "chore: scaffold qianchuan peak frame skill"
```

## Task 3: Implement RPA Parsing and Peak Selection with TDD

**Files:**
- Create: `scripts/peak_frame_lib.py`
- Create: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing parsing and selection tests**

Create `tests/test_peak_frames.py` with imports from `scripts/` and tests using this canonical series:

```python
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from peak_frame_lib import (
    InputDataError,
    SeriesPoint,
    find_local_peaks,
    parse_click_series,
    select_peak_frames,
)


def click_record(second, value, material_id="m-123"):
    return {
        "recordType": "interaction_series",
        "interactionMetric": "CLICK_COUNT",
        "durationSec": second,
        "metricValue": value,
        "materialId": material_id,
    }


class PeakSelectionTests(unittest.TestCase):
    def test_selects_first_chronological_peak_then_later_top_three(self):
        values = [1, 5, 1, 9, 1, 7, 1, 8, 1]
        points = [SeriesPoint(i, value) for i, value in enumerate(values)]
        selected = select_peak_frames(find_local_peaks(points))
        self.assertEqual(
            [(item.rank_type, item.duration_sec, item.metric_value) for item in selected],
            [
                ("first_peak", 1.0, 5.0),
                ("later_top_1", 3.0, 9.0),
                ("later_top_2", 7.0, 8.0),
                ("later_top_3", 5.0, 7.0),
            ],
        )

    def test_collapses_equal_plateau_to_earliest_second(self):
        points = [SeriesPoint(i, value) for i, value in enumerate([1, 4, 8, 8, 3, 1])]
        peaks = find_local_peaks(points)
        self.assertEqual([(peak.duration_sec, peak.metric_value) for peak in peaks], [(2.0, 8.0)])

    def test_excludes_first_and_last_points(self):
        points = [SeriesPoint(i, value) for i, value in enumerate([9, 1, 2, 1, 8])]
        peaks = find_local_peaks(points)
        self.assertEqual([(peak.duration_sec, peak.metric_value) for peak in peaks], [(2.0, 2.0)])

    def test_later_ties_are_ordered_by_second(self):
        values = [0, 2, 0, 6, 0, 6, 0, 6, 0]
        selected = select_peak_frames(find_local_peaks([SeriesPoint(i, v) for i, v in enumerate(values)]))
        self.assertEqual([item.duration_sec for item in selected], [1.0, 3.0, 5.0, 7.0])

    def test_parse_filters_click_records_and_collapses_identical_duplicates(self):
        records = [
            click_record(1, 5),
            click_record(1, 5),
            {**click_record(2, 99), "interactionMetric": "LIKE_COUNT"},
            click_record(3, 7),
        ]
        material_id, points = parse_click_series(records)
        self.assertEqual(material_id, "m-123")
        self.assertEqual(points, [SeriesPoint(1.0, 5.0), SeriesPoint(3.0, 7.0)])

    def test_parse_rejects_conflicting_values_for_same_second(self):
        with self.assertRaisesRegex(InputDataError, "conflicting.*1"):
            parse_click_series([click_record(1, 5), click_record(1, 6)])

    def test_parse_rejects_missing_click_series(self):
        with self.assertRaisesRegex(InputDataError, "CLICK_COUNT"):
            parse_click_series([])

    def test_select_rejects_no_peaks(self):
        with self.assertRaisesRegex(InputDataError, "No local peaks"):
            select_peak_frames([])

    def test_select_warns_when_fewer_than_three_later_peaks(self):
        values = [0, 5, 0, 4, 0]
        selected = select_peak_frames(find_local_peaks([SeriesPoint(i, v) for i, v in enumerate(values)]))
        self.assertEqual([item.rank_type for item in selected], ["first_peak", "later_top_1"])
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
```

Expected: import failure because `scripts/peak_frame_lib.py` does not exist.

- [ ] **Step 3: Implement minimal parsing and peak types**

Create `scripts/peak_frame_lib.py` with these public interfaces:

```python
from dataclasses import dataclass
import math


class PeakFrameError(Exception):
    """Base error for expected peak-frame failures."""


class InputDataError(PeakFrameError):
    """Raised when RPA or video input violates the contract."""


@dataclass(frozen=True)
class SeriesPoint:
    duration_sec: float
    metric_value: float


@dataclass(frozen=True)
class Peak:
    duration_sec: float
    metric_value: float


@dataclass(frozen=True)
class SelectedPeak:
    rank_type: str
    duration_sec: float
    metric_value: float


def _finite_number(value, field_name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InputDataError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise InputDataError(f"{field_name} must be a finite number")
    return number


def parse_click_series(records):
    """Return a material id and sorted, de-duplicated CLICK_COUNT points."""
    if not isinstance(records, list):
        raise InputDataError("RPA JSON must be a list of records")
    values_by_second = {}
    material_ids = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InputDataError(f"record {index} must be an object")
        if record.get("recordType") != "interaction_series":
            continue
        if record.get("interactionMetric") != "CLICK_COUNT":
            continue
        second = _finite_number(record.get("durationSec"), "durationSec")
        metric = _finite_number(record.get("metricValue"), "metricValue")
        if second < 0:
            raise InputDataError("durationSec cannot be negative")
        previous = values_by_second.get(second)
        if previous is not None and previous != metric:
            raise InputDataError(f"conflicting CLICK_COUNT values at second {second:g}")
        values_by_second[second] = metric
        material_id = str(record.get("materialId") or "").strip()
        if material_id:
            material_ids.add(material_id)
    if not values_by_second:
        raise InputDataError("No valid CLICK_COUNT interaction series found")
    if len(material_ids) > 1:
        raise InputDataError("CLICK_COUNT records contain multiple materialId values")
    material_id = next(iter(material_ids), "")
    points = [SeriesPoint(second, values_by_second[second]) for second in sorted(values_by_second)]
    return material_id, points


def find_local_peaks(points):
    """Collapse equal plateaus, exclude endpoints, and return chronological peaks."""
    peaks = []
    index = 1
    while index < len(points) - 1:
        plateau_start = index
        plateau_end = index
        value = points[index].metric_value
        while plateau_end + 1 < len(points) and points[plateau_end + 1].metric_value == value:
            plateau_end += 1
        if (
            plateau_start > 0
            and plateau_end < len(points) - 1
            and value > points[plateau_start - 1].metric_value
            and value > points[plateau_end + 1].metric_value
        ):
            peaks.append(Peak(points[plateau_start].duration_sec, value))
        index = plateau_end + 1
    return peaks


def select_peak_frames(peaks, later_limit=3):
    """Return first chronological peak plus later peaks sorted by value/time."""
    chronological = sorted(peaks, key=lambda peak: peak.duration_sec)
    if not chronological:
        raise InputDataError("No local peaks found in CLICK_COUNT series")
    first = chronological[0]
    later = [peak for peak in chronological[1:] if peak.duration_sec > first.duration_sec]
    later.sort(key=lambda peak: (-peak.metric_value, peak.duration_sec))
    selected = [SelectedPeak("first_peak", first.duration_sec, first.metric_value)]
    selected.extend(
        SelectedPeak(f"later_top_{rank}", peak.duration_sec, peak.metric_value)
        for rank, peak in enumerate(later[:later_limit], start=1)
    )
    return selected
```

Do not add smoothing, prominence, minimum distance, NumPy, or SciPy.

- [ ] **Step 4: Run parsing and selection tests and verify GREEN**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
```

Expected: all `PeakSelectionTests` pass.

- [ ] **Step 5: Commit the deterministic core**

```bash
git add scripts/peak_frame_lib.py tests/test_peak_frames.py
git commit -m "feat: select qianchuan click peaks"
```

## Task 4: Implement Cross-Platform FFmpeg Resolution and Probing with TDD

**Files:**
- Modify: `scripts/peak_frame_lib.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Add failing FFmpeg resolution tests**

Append tests that inject deterministic resolvers instead of changing the host environment:

```python
from tempfile import TemporaryDirectory
from unittest.mock import patch

from peak_frame_lib import FfmpegError, probe_video_duration, resolve_ffmpeg


class FfmpegResolutionTests(unittest.TestCase):
    def test_explicit_ffmpeg_wins(self):
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ffmpeg custom.exe"
            binary.touch()
            resolved = resolve_ffmpeg(
                explicit=str(binary),
                environ={"IMAGEIO_FFMPEG_EXE": "/env/ffmpeg"},
                imageio_resolver=lambda: "/imageio/ffmpeg",
                which=lambda _: "/path/ffmpeg",
            )
            self.assertEqual(resolved, str(binary))

    def test_environment_precedes_imageio_and_path(self):
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ffmpeg.exe"
            binary.touch()
            resolved = resolve_ffmpeg(
                environ={"IMAGEIO_FFMPEG_EXE": str(binary)},
                imageio_resolver=lambda: "/imageio/ffmpeg",
                which=lambda _: "/path/ffmpeg",
            )
            self.assertEqual(resolved, str(binary))

    def test_imageio_precedes_path(self):
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ffmpeg"
            binary.touch()
            resolved = resolve_ffmpeg(
                environ={},
                imageio_resolver=lambda: str(binary),
                which=lambda _: "/path/ffmpeg",
            )
            self.assertEqual(resolved, str(binary))

    def test_missing_ffmpeg_has_actionable_error(self):
        with self.assertRaisesRegex(FfmpegError, "imageio-ffmpeg"):
            resolve_ffmpeg(environ={}, imageio_resolver=lambda: None, which=lambda _: None)
```

- [ ] **Step 2: Run the new tests and verify RED**

```bash
.venv/bin/python -m unittest tests.test_peak_frames.FfmpegResolutionTests -v
```

Expected: import failure for `FfmpegError` or `resolve_ffmpeg`.

- [ ] **Step 3: Implement FFmpeg resolution and duration probing**

Add these public interfaces to `peak_frame_lib.py`:

```python
import os
from pathlib import Path
import re
import shutil
import subprocess


class FfmpegError(PeakFrameError):
    """Raised when FFmpeg is unavailable or fails."""


def resolve_ffmpeg(explicit=None, environ=None, imageio_resolver=None, which=None):
    """Resolve an existing FFmpeg in approved precedence order."""
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_file():
            raise FfmpegError(f"Explicit FFmpeg does not exist: {candidate}")
        return str(candidate)
    env_candidate = environ.get("IMAGEIO_FFMPEG_EXE")
    if env_candidate:
        candidate = Path(env_candidate).expanduser()
        if not candidate.is_file():
            raise FfmpegError(f"IMAGEIO_FFMPEG_EXE does not exist: {candidate}")
        return str(candidate)
    if imageio_resolver is None:
        try:
            import imageio_ffmpeg
        except ImportError:
            imageio_resolver = lambda: None
        else:
            imageio_resolver = imageio_ffmpeg.get_ffmpeg_exe
    imageio_candidate = imageio_resolver()
    if imageio_candidate and Path(imageio_candidate).is_file():
        return str(Path(imageio_candidate))
    path_candidate = which("ffmpeg")
    if path_candidate:
        return str(path_candidate)
    raise FfmpegError(
        "FFmpeg is unavailable; install imageio-ffmpeg or pass --ffmpeg-bin"
    )


def probe_video_duration(ffmpeg_bin, video_path):
    """Run FFmpeg input probing and parse Duration into float seconds."""
    video = Path(video_path)
    if not video.is_file():
        raise InputDataError(f"video does not exist: {video}")
    completed = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-i", str(video)],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", completed.stderr)
    if not match:
        raise FfmpegError(f"Unable to decode video duration: {video}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
```

Keep all process calls shell-free. Raise `FfmpegError` when the path cannot be decoded or duration is unavailable.

- [ ] **Step 4: Add and run a real probe test**

Append this integration test, which resolves the installed `imageio-ffmpeg` executable and generates a two-second temporary MP4:

```python
import subprocess

    def test_probe_reads_real_video_duration(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            video = Path(tmp) / "two seconds.mp4"
            completed = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25:d=2",
                    "-c:v", "mpeg4", "-y", str(video),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(1.9 <= probe_video_duration(ffmpeg, video) <= 2.1)
```

Run:

```bash
.venv/bin/python -m unittest tests.test_peak_frames.FfmpegResolutionTests -v
```

Expected: all resolution and real-probe tests pass.

- [ ] **Step 5: Commit FFmpeg discovery**

```bash
git add scripts/peak_frame_lib.py tests/test_peak_frames.py
git commit -m "feat: resolve and probe cross-platform ffmpeg"
```

## Task 5: Implement Exact Frame Extraction with an Integration Test

**Files:**
- Modify: `scripts/peak_frame_lib.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Write the failing extraction integration test**

Add this test. It generates a nine-second changing test video, extracts four frames through production code, and compares each to an independently invoked FFmpeg reference:

```python
from peak_frame_lib import extract_frame


class FrameExtractionTests(unittest.TestCase):
    def test_extracts_jpegs_at_requested_timestamps(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "changing video.mp4"
            generated = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=64x64:r=25:d=9",
                    "-c:v", "mpeg4", "-y", str(video),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)

            production_bytes = []
            for second in (1.0, 3.0, 5.0, 7.0):
                actual = root / f"actual-{int(second)}.jpg"
                expected = root / f"expected-{int(second)}.jpg"
                extract_frame(ffmpeg, video, second, actual)
                reference = subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-loglevel", "error",
                        "-i", str(video), "-ss", f"{second:.6f}",
                        "-frames:v", "1", "-q:v", "2", "-y", str(expected),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(reference.returncode, 0, reference.stderr)
                self.assertGreater(actual.stat().st_size, 0)
                self.assertEqual(actual.read_bytes(), expected.read_bytes())
                production_bytes.append(actual.read_bytes())

            self.assertEqual(len(set(production_bytes)), 4)
```

- [ ] **Step 2: Run the integration test and verify RED**

```bash
.venv/bin/python -m unittest tests.test_peak_frames.FrameExtractionTests -v
```

Expected: import failure because `extract_frame` is missing.

- [ ] **Step 3: Implement frame extraction and timestamp validation**

Add:

```python
def extract_frame(ffmpeg_bin, video_path, timestamp_sec, output_path):
    """Decode accurately to timestamp and write one JPEG."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(Path(video_path)),
            "-ss", f"{float(timestamp_sec):.6f}",
            "-frames:v", "1",
            "-q:v", "2",
            "-y", str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        detail = completed.stderr.strip() or "FFmpeg did not produce a frame"
        raise FfmpegError(f"Frame extraction failed at {timestamp_sec:g}s: {detail}")
    return output


def validate_timestamps(selected_peaks, video_duration):
    """Reject negative timestamps or timestamps greater than/equal to duration."""
    for peak in selected_peaks:
        if peak.duration_sec < 0 or peak.duration_sec >= video_duration:
            raise InputDataError(
                f"Peak timestamp {peak.duration_sec:g}s is outside video duration "
                f"{video_duration:g}s"
            )
```

Treat a non-zero FFmpeg exit code, a missing output, or a zero-byte JPEG as `FfmpegError`. If Python 3.9 compatibility requires it, replace `missing_ok=True` with an existence check before unlinking.

- [ ] **Step 4: Run all tests and verify GREEN**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
```

Expected: peak, resolver, probe, and extraction tests all pass.

- [ ] **Step 5: Commit exact extraction**

```bash
git add scripts/peak_frame_lib.py tests/test_peak_frames.py
git commit -m "feat: extract exact peak frames with ffmpeg"
```

## Task 6: Implement Output Naming and Atomic Summaries with TDD

**Files:**
- Modify: `scripts/peak_frame_lib.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing output-contract tests**

Add tests for:

```python
from peak_frame_lib import (
    SelectedPeak,
    build_summary,
    format_frame_filename,
    write_summaries,
)


class SummaryTests(unittest.TestCase):
    def test_formats_integer_and_fractional_seconds_safely(self):
        self.assertEqual(format_frame_filename("first_peak", 7.0, 18.0), "first_peak_0007s_18.jpg")
        self.assertEqual(format_frame_filename("later_top_1", 7.5, 12.0), "later_top_1_0007p5s_12.jpg")

    def test_summary_warns_when_later_peaks_are_missing(self):
        selected = [
            SelectedPeak("first_peak", 1.0, 5.0),
            SelectedPeak("later_top_1", 3.0, 4.0),
        ]
        summary = build_summary("m-123", "input.json", "video.mp4", selected, ["a.jpg", "b.jpg"])
        self.assertEqual(len(summary["frames"]), 2)
        self.assertIn("2 later peak frame(s) unavailable", summary["warnings"])
```

Add a filesystem test proving `write_summaries` creates valid `summary.json` and `summary.md`, and does not leave `.tmp` files.

- [ ] **Step 2: Run summary tests and verify RED**

```bash
.venv/bin/python -m unittest tests.test_peak_frames.SummaryTests -v
```

Expected: import failure for summary functions.

- [ ] **Step 3: Implement naming and summaries**

Add:

```python
import json


def format_seconds_token(duration_sec):
    """Format 7 as 0007s and 7.5 as 0007p5s."""
    value = f"{float(duration_sec):.6f}".rstrip("0").rstrip(".")
    whole, dot, fraction = value.partition(".")
    token = f"{int(whole):04d}"
    if dot:
        token += f"p{fraction}"
    return f"{token}s"


def format_frame_filename(rank_type, duration_sec, metric_value):
    """Return a filesystem-safe deterministic JPEG name."""
    metric_token = f"{float(metric_value):.6f}".rstrip("0").rstrip(".").replace(".", "p")
    return f"{rank_type}_{format_seconds_token(duration_sec)}_{metric_token}.jpg"


def build_summary(material_id, input_json_path, video_path, selected_peaks, frame_paths):
    """Build the approved JSON-compatible output contract."""
    if len(selected_peaks) != len(frame_paths):
        raise ValueError("selected_peaks and frame_paths must have equal length")
    missing = max(0, 4 - len(selected_peaks))
    warnings = []
    if missing:
        warnings.append(f"{missing} later peak frame(s) unavailable")
    return {
        "materialId": material_id,
        "inputJsonPath": str(Path(input_json_path)),
        "videoPath": str(Path(video_path)),
        "selectionRule": {
            "first": "earliest_local_peak",
            "later": "top_3_by_metric_desc_then_time_asc_after_first",
            "plateau": "collapse_to_earliest_second",
            "boundariesExcluded": True,
        },
        "frames": [
            {
                "rankType": peak.rank_type,
                "durationSec": peak.duration_sec,
                "metricValue": peak.metric_value,
                "requestedTimestampSec": peak.duration_sec,
                "framePath": str(Path(frame_path)),
            }
            for peak, frame_path in zip(selected_peaks, frame_paths)
        ],
        "warnings": warnings,
    }


def _render_summary_markdown(summary):
    lines = [
        f"# 千川点击峰值画面：{summary['materialId']}",
        "",
        "| 类型 | 秒数 | 点击次数 | 画面 |",
        "| --- | ---: | ---: | --- |",
    ]
    for frame in summary["frames"]:
        lines.append(
            f"| {frame['rankType']} | {frame['durationSec']:g} | "
            f"{frame['metricValue']:g} | {frame['framePath']} |"
        )
    if summary["warnings"]:
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    return "\n".join(lines) + "\n"


def write_summaries(summary, result_dir):
    """Atomically write summary.json and summary.md after frames exist."""
    result = Path(result_dir)
    result.mkdir(parents=True, exist_ok=True)
    json_path = result / "summary.json"
    md_path = result / "summary.md"
    json_tmp = result / "summary.json.tmp"
    md_tmp = result / "summary.md.tmp"
    json_tmp.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_tmp.write_text(_render_summary_markdown(summary), encoding="utf-8")
    json_tmp.replace(json_path)
    md_tmp.replace(md_path)
    return json_path, md_path
```

Markdown must preserve `first_peak`, `later_top_1`, `later_top_2`, `later_top_3` order from the selected list.

- [ ] **Step 4: Run all tests and verify GREEN**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
```

Expected: all tests pass with no `.tmp` artifacts.

- [ ] **Step 5: Commit output generation**

```bash
git add scripts/peak_frame_lib.py tests/test_peak_frames.py
git commit -m "feat: write peak frame summaries"
```

## Task 7: Implement Local CLI and Online Adapter Boundary with TDD

**Files:**
- Create: `scripts/rpa_adapter.py`
- Create: `scripts/extract_peak_frames.py`
- Modify: `scripts/peak_frame_lib.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing CLI tests**

Add subprocess-level tests that use temporary JSON/video/output paths:

```python
import json


class CliTests(unittest.TestCase):
    def _make_video(self, ffmpeg, video, duration=9):
        completed = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc2=s=64x64:r=25:d={duration}",
                "-c:v", "mpeg4", "-y", str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_local_cli_outputs_four_frames_and_two_summaries(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "material with spaces.mp4"
            input_json = root / "rpa result.json"
            output = root / "output with spaces"
            self._make_video(ffmpeg, video)
            values = [0, 2, 0, 9, 0, 7, 0, 8, 0]
            input_json.write_text(
                json.dumps([click_record(i, value) for i, value in enumerate(values)]),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "extract_peak_frames.py"),
                    "--input-json", str(input_json),
                    "--video", str(video),
                    "--output-dir", str(output),
                    "--ffmpeg-bin", ffmpeg,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_dir = output / "m-123"
            self.assertEqual(len(list(result_dir.glob("*.jpg"))), 4)
            self.assertTrue((result_dir / "summary.json").is_file())
            self.assertTrue((result_dir / "summary.md").is_file())

    def test_rpa_source_is_explicitly_unconfigured(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "extract_peak_frames.py"),
                "--source", "rpa",
                "--material-id", "m-123",
                "--date-range-type", "LAST_7_DAYS",
                "--output-dir", ".test-output",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 4)
        self.assertIn("RPA entry is not configured", completed.stderr)
```

Add these explicit failure tests to the same class:

```python
    def _run_local(self, input_json, video, output, ffmpeg):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "extract_peak_frames.py"),
                "--input-json", str(input_json),
                "--video", str(video),
                "--output-dir", str(output),
                "--ffmpeg-bin", ffmpeg,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_local_cli_rejects_malformed_json(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            self._make_video(ffmpeg, video)
            input_json = root / "bad.json"
            input_json.write_text("{not-json", encoding="utf-8")
            completed = self._run_local(input_json, video, root / "out", ffmpeg)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("valid JSON", completed.stderr)

    def test_local_cli_rejects_missing_video(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_json = root / "input.json"
            input_json.write_text(
                json.dumps([click_record(i, value) for i, value in enumerate([0, 5, 0])]),
                encoding="utf-8",
            )
            completed = self._run_local(
                input_json, root / "missing.mp4", root / "out", ffmpeg
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("video does not exist", completed.stderr)

    def test_local_cli_rejects_no_peaks(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            self._make_video(ffmpeg, video)
            input_json = root / "input.json"
            input_json.write_text(
                json.dumps([click_record(i, value) for i, value in enumerate([1, 2, 3])]),
                encoding="utf-8",
            )
            completed = self._run_local(input_json, video, root / "out", ffmpeg)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("No local peaks", completed.stderr)

    def test_local_cli_rejects_peak_at_video_duration(self):
        ffmpeg = resolve_ffmpeg()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            self._make_video(ffmpeg, video, duration=2)
            input_json = root / "input.json"
            records = [click_record(0, 0), click_record(2, 5), click_record(3, 0)]
            input_json.write_text(json.dumps(records), encoding="utf-8")
            completed = self._run_local(input_json, video, root / "out", ffmpeg)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("outside video duration", completed.stderr)
```

- [ ] **Step 2: Run CLI tests and verify RED**

```bash
.venv/bin/python -m unittest tests.test_peak_frames.CliTests -v
```

Expected: failure because the CLI and adapter do not exist.

- [ ] **Step 3: Implement the unconfigured adapter**

Create `scripts/rpa_adapter.py`:

```python
class RpaNotConfiguredError(RuntimeError):
    pass


def fetch_material_diagnosis(**params):
    raise RpaNotConfiguredError(
        "RPA entry is not configured: "
        "rpa.conn.juliang.qc.material.analysis.video.live.diagnosis"
    )
```

- [ ] **Step 4: Implement orchestration and the CLI**

Add a library function:

```python
def _safe_component(value):
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned.strip("_") or "unknown-material"


def process_local(input_json_path, video_path, output_dir, ffmpeg_bin=None):
    """Run the complete local JSON/video pipeline and return summary paths."""
    input_path = Path(input_json_path)
    video = Path(video_path)
    if not input_path.is_file():
        raise InputDataError(f"input JSON does not exist: {input_path}")
    if not video.is_file():
        raise InputDataError(f"video does not exist: {video}")
    try:
        records = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputDataError(f"input must contain valid JSON: {input_path}") from exc
    material_id, points = parse_click_series(records)
    selected = select_peak_frames(find_local_peaks(points))
    ffmpeg = resolve_ffmpeg(explicit=ffmpeg_bin)
    duration = probe_video_duration(ffmpeg, video)
    validate_timestamps(selected, duration)
    result_dir = Path(output_dir) / _safe_component(material_id or video.stem)
    result_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(result_dir.glob("*.jpg")) + [
        result_dir / "summary.json",
        result_dir / "summary.md",
        result_dir / "summary.json.tmp",
        result_dir / "summary.md.tmp",
    ]:
        if stale.exists():
            stale.unlink()
    frame_paths = []
    for peak in selected:
        frame_path = result_dir / format_frame_filename(
            peak.rank_type, peak.duration_sec, peak.metric_value
        )
        extract_frame(ffmpeg, video, peak.duration_sec, frame_path)
        frame_paths.append(frame_path)
    summary = build_summary(
        material_id or video.stem,
        input_path,
        video,
        selected,
        frame_paths,
    )
    json_path, md_path = write_summaries(summary, result_dir)
    return result_dir, json_path, md_path
```

Create `extract_peak_frames.py` with this orchestration. Do not download `videoUrl` or synthesize RPA data:

```python
#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

from peak_frame_lib import FfmpegError, InputDataError, process_local
from rpa_adapter import RpaNotConfiguredError, fetch_material_diagnosis


DATE_RANGE_TYPES = (
    "YESTERDAY", "LAST_7_DAYS", "LAST_15_DAYS", "LAST_WEEK",
    "THIS_MONTH", "LAST_MONTH", "CUSTOM",
)


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("local", "rpa"), default="local")
    parser.add_argument("--input-json")
    parser.add_argument("--video")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg-bin")
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
            if not args.material_id or not args.date_range_type:
                parser.error("--material-id and --date-range-type are required for --source rpa")
            if args.date_range_type == "CUSTOM" and not (
                args.custom_start_date and args.custom_end_date
            ):
                parser.error("CUSTOM requires --custom-start-date and --custom-end-date")
            fetch_material_diagnosis(
                material_id=args.material_id,
                date_range_type=args.date_range_type,
                custom_start_date=args.custom_start_date,
                custom_end_date=args.custom_end_date,
            )
            return 0
        if not args.input_json or not args.video:
            parser.error("--input-json and --video are required for local source")
        result_dir, json_path, md_path = process_local(
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
    except RpaNotConfiguredError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    print(f"result_dir={result_dir}")
    print(f"summary_json={json_path}")
    print(f"summary_md={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run CLI tests and the complete suite**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
```

Expected: all tests pass, including a real four-frame local run.

- [ ] **Step 6: Commit CLI behavior**

```bash
git add scripts/extract_peak_frames.py scripts/rpa_adapter.py scripts/peak_frame_lib.py tests/test_peak_frames.py
git commit -m "feat: add local peak frame CLI"
```

## Task 8: Write the Skill and Minimal RPA Reference

**Files:**
- Modify: `SKILL.md`
- Regenerate: `agents/openai.yaml`
- Create: `references/rpa-output-contract.md`

- [ ] **Step 1: Write the minimal RPA reference**

Document only:

- Connector code `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis`.
- Local record fields used by parsing.
- `material_info.videoUrl` and `videoDuration` reserved for future online use.
- Supported date-range values and custom-date requirement.
- A short JSON example with enough CLICK_COUNT points to produce four peaks.

- [ ] **Step 2: Replace the scaffold SKILL.md**

Use this frontmatter and concise workflow:

```markdown
---
name: qianchuan-video-peak-frames
description: Use when extracting or reviewing video frames at CLICK_COUNT peaks from 巨量千川素材分析、视频推直播单条诊断、Qianchuan RPA JSON, including local JSON/video runs and future connector-backed runs.
---

# Qianchuan Video Peak Frames

## Overview

Extract the first chronological click peak and the later top three click peaks from Qianchuan material-diagnosis data, then save their corresponding JPEG frames and summaries.

## Workflow

1. Read `references/rpa-output-contract.md` when validating RPA fields or preparing connector parameters.
2. Prefer local mode while the online RPA adapter is unconfigured.
3. Verify Python 3.9+ and install `requirements.txt` into an isolated environment.
4. Run `scripts/extract_peak_frames.py` with local JSON, video, and output paths.
5. Review `summary.json`, `summary.md`, and every listed JPEG before reporting success.

## Local Run

```bash
python scripts/extract_peak_frames.py \
  --input-json /path/to/rpa-result.json \
  --video /path/to/material.mp4 \
  --output-dir /path/to/output
```

Use `--ffmpeg-bin` when production manages FFmpeg separately. Paths containing spaces are supported; pass arguments as separate shell arguments.

## Selection Contract

- Exclude the first and last time-series points.
- Collapse an equal-valued plateau into one peak at its earliest second.
- Select the earliest peak first.
- From only the later peaks, choose three by click count descending and second ascending.
- If fewer later peaks exist, return available frames with a warning; never fabricate peaks.

## Online Boundary

The `--source rpa` entry is reserved but intentionally unconfigured. Do not simulate connector results. When the connector becomes available, update only `scripts/rpa_adapter.py` and reuse the local processing pipeline.

## Verification

Run:

```bash
python -m unittest tests/test_peak_frames.py -v
```

Confirm each summary frame path exists and that warnings are surfaced to the user.
```

- [ ] **Step 3: Regenerate agents metadata from the finished skill**

```bash
python3 /Users/shuzida/.codex/skills/.system/skill-creator/scripts/generate_openai_yaml.py \
  /Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames \
  --interface 'display_name=Qianchuan Video Peak Frames' \
  --interface 'short_description=Extract frames at Qianchuan click peaks' \
  --interface 'default_prompt=Use $qianchuan-video-peak-frames to extract the first click peak and the later top three peak frames from local Qianchuan RPA JSON and video.'
```

- [ ] **Step 4: Run structural validation**

```bash
python3 /Users/shuzida/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames
```

Expected: skill is valid.

- [ ] **Step 5: Commit skill instructions and reference**

```bash
git add SKILL.md agents/openai.yaml references/rpa-output-contract.md
git commit -m "docs: add qianchuan peak frame skill workflow"
```

## Task 9: Run Skill GREEN/REFACTOR Validation

**Files:**
- Modify: `tests/skill-baseline.md`
- Modify: `SKILL.md` only when the GREEN scenario exposes a recorded contract gap

- [ ] **Step 1: Re-run the original scenario with the skill**

Spawn a fresh subagent with this user-style prompt:

```text
Use $qianchuan-video-peak-frames at /Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames to solve this task:
Analyze /Users/shuzida/Desktop/FDE客户/小仙炖/千川短视频/千川-素材分析-视频推直播单条诊断.md and explain the exact local invocation, peak rules, cross-platform FFmpeg behavior, outputs, and verification for extracting the first chronological CLICK_COUNT peak plus the later top three peak frames. Do not modify production or external systems.
```

Pass the skill path and request, but not the approved answer, baseline gaps, design, or plan.

- [ ] **Step 2: Record GREEN evidence**

Append the response verbatim under `## Skill-enabled output` in `tests/skill-baseline.md`. Under `## Comparison`, record whether it selected local mode, preserved platform peaks, described the correct two-stage selection, handled Windows paths, kept RPA unconfigured, and required result verification.

- [ ] **Step 3: Close only observed loopholes**

If the skill-enabled response violates the contract, add the minimal explicit instruction to `SKILL.md` and re-run the same scenario. Record each observed rationalization and the counter added. Do not add hypothetical workflow sections.

- [ ] **Step 4: Re-run automated tests and Skill validation**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
python3 /Users/shuzida/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

Expected: all tests pass and skill is valid.

- [ ] **Step 5: Commit validation evidence and any minimal refinement**

```bash
git add tests/skill-baseline.md SKILL.md agents/openai.yaml
git commit -m "test: validate qianchuan peak frame skill"
```

## Task 10: Final Verification and Handoff

**Files:**
- Verify all tracked files

- [ ] **Step 1: Run the complete verification suite from a clean command**

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
python3 /Users/shuzida/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames
git diff --check
```

Expected: all unit/integration tests pass, Skill validation succeeds, and `git diff --check` is silent.

- [ ] **Step 2: Run one retained local smoke test**

Generate a temporary nine-second video and RPA JSON, run the CLI, then inspect:

```text
<output>/<material-id>/
  first_peak_*.jpg
  later_top_1_*.jpg
  later_top_2_*.jpg
  later_top_3_*.jpg
  summary.json
  summary.md
```

Expected: four non-empty JPEGs and summaries with matching seconds, click counts, and paths.

- [ ] **Step 3: Review repository state and scope**

```bash
git status --short
git log --oneline --decorate -10
```

Expected: no uncommitted source changes. `.venv/` and `.test-output/` remain ignored.

- [ ] **Step 4: Report the Windows verification boundary**

State explicitly that subprocess/path behavior is cross-platform tested and FFmpeg is supplied by `imageio-ffmpeg`, while an actual Windows end-to-end run still requires a Windows machine or CI runner.

- [ ] **Step 5: Commit any verification-only fixes**

If verification required source changes, repeat the affected RED/GREEN cycle and commit them. Otherwise do not create an empty commit.
