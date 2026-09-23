# Qianchuan RPA CSV Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process the Qianchuan RPA diagnostic CSV export directly with the existing peak-frame pipeline.

**Architecture:** A focused CSV parser converts the RPA export to the existing `list[dict]` records contract. The local CLI selects JSON or CSV input and calls the same `process_records` function, so peak selection, video probing, frame extraction, and summaries retain their established behavior.

**Tech Stack:** Python 3.9 standard library (`csv`, `ast`, `json`), `unittest`, existing `imageio-ffmpeg` runtime.

---

### Task 1: Define CSV parsing behavior with failing tests

**Files:**
- Create: `tests/test_rpa_csv_parser.py`
- Create: `scripts/rpa_csv_parser.py`

- [x] **Step 1: Write the failing control-character parser test**

```python
def test_parses_rpa_control_delimited_rows_into_records(self):
    path = self._write_csv(
        "recordType\\x01materialId\\x01durationSec\\x01metricValue\\n"
        "\\x02interaction_series\\x01m-1\\x011.0\\x012.0\\n"
    )
    records = parse_diagnosis_csv(path)
    self.assertEqual(records[0]["materialId"], "m-1")
    self.assertEqual(records[0]["durationSec"], 1.0)
```

- [ ] **Step 2: Run the test and verify it fails because the parser is unavailable**

Run: `.venv/bin/python -m unittest tests.test_rpa_csv_parser -v`

Expected: import failure for `rpa_csv_parser` or `parse_diagnosis_csv`.

- [ ] **Step 3: Implement the smallest parser API in `scripts/rpa_csv_parser.py`**

```python
class RpaCsvError(ValueError):
    pass

def parse_diagnosis_csv(path):
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(
        io.StringIO(_remove_record_prefixes(text)), delimiter="\x01"
    )
    return [_normalize_record(dict(row), source, row_number)
            for row_number, row in enumerate(reader, start=2)]
```

- [ ] **Step 4: Run the focused parser test and verify it passes**

Run: `.venv/bin/python -m unittest tests.test_rpa_csv_parser -v`

Expected: `OK`.

### Task 2: Normalize nested RPA values and audience metrics

**Files:**
- Modify: `tests/test_rpa_csv_parser.py`
- Modify: `scripts/rpa_csv_parser.py`

- [ ] **Step 1: Write failing normalization tests**

```python
def test_normalizes_dimensions_and_uses_single_metrics_value(self):
    records = parse_diagnosis_csv(self._write_crowd_csv())
    record = records[0]
    self.assertEqual(record["Dimensions"]["age"]["ValueStr"], "24-30")
    self.assertEqual(record["metricValue"], 42.5)

def test_rejects_ambiguous_crowd_metrics(self):
    with self.assertRaisesRegex(RpaCsvError, "multiple numeric Metrics"):
        parse_diagnosis_csv(self._write_ambiguous_crowd_csv())
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `.venv/bin/python -m unittest tests.test_rpa_csv_parser -v`

Expected: the first test observes unparsed text or an empty `metricValue`; the second test does not raise.

- [ ] **Step 3: Implement strict nested-value normalization**

```python
def _parse_structured_cell(value, field_name, source, row_number):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return ast.literal_eval(value)

def _normalize_crowd_metric(record, source, row_number):
    values = [metric["Value"] for metric in record["Metrics"].values()]
    if len(values) != 1:
        raise RpaCsvError("multiple numeric Metrics.Value entries")
    record["metricValue"] = values[0]
```

- [ ] **Step 4: Run parser tests and commercial-report tests**

Run: `.venv/bin/python -m unittest tests.test_rpa_csv_parser tests.test_commercial_report -v`

Expected: `OK`.

### Task 3: Add local CSV support to the peak-frame CLI

**Files:**
- Modify: `tests/test_peak_frames.py`
- Modify: `scripts/extract_peak_frames.py`

- [ ] **Step 1: Write a failing CLI test for `--input-csv`**

```python
def test_local_csv_cli_reuses_peak_frame_pipeline(self):
    result = extract_peak_frames.main([
        "--input-csv", str(csv_path), "--video", str(video),
        "--output-dir", str(output_dir),
    ])
    self.assertEqual(result, 0)
    self.assertTrue((output_dir / "m-1" / "summary.json").is_file())
```

- [ ] **Step 2: Run the selected test and verify argparse rejects `--input-csv`**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_local_csv_cli_reuses_peak_frame_pipeline -v`

Expected: FAIL because `--input-csv` is not recognized.

- [ ] **Step 3: Add mutually exclusive JSON/CSV local inputs**

```python
input_group = parser.add_mutually_exclusive_group()
input_group.add_argument("--input-json")
input_group.add_argument("--input-csv")

if args.input_csv:
    records = parse_diagnosis_csv(args.input_csv)
    result_dir, json_path, markdown_path = process_records(
        records, args.input_csv, args.video, args.output_dir,
        ffmpeg_bin=args.ffmpeg_bin,
    )
```

- [ ] **Step 4: Run the CLI test and the complete peak-frame test module**

Run: `.venv/bin/python -m unittest tests.test_peak_frames -v`

Expected: `OK`.

### Task 4: Validate actual exports and the full branch

**Files:**
- Modify: `docs/superpowers/specs/2026-07-23-qianchuan-rpa-csv-adapter-design.md`

- [ ] **Step 1: Run each supplied CSV through the parser and peak-selection functions**

```bash
PYTHONPATH=scripts .venv/bin/python -c "from rpa_csv_parser import parse_diagnosis_csv; from peak_frame_lib import parse_click_series, find_local_peaks, select_peak_frames; import sys; records=parse_diagnosis_csv(sys.argv[1]); print(select_peak_frames(find_local_peaks(parse_click_series(records)[1])))" '<csv-path>'
```

- [ ] **Step 2: Record verified actual-file outcomes in the design document**

```markdown
## Verification

- Both supplied CSV exports parse successfully and produce a click series.
- Gateway `result_type=files` envelope integration remains deferred until a raw response fixture is supplied.
```

- [ ] **Step 3: Run the complete test suite**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: `OK` with no failures or errors.
