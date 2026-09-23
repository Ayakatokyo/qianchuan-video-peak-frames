# Qianchuan RPA Files Envelope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process nested RPA `result_type=files` responses into retained diagnostic CSV artifacts and normalized records for online peak-frame extraction.

**Architecture:** `rpa_adapter.py` will unpack record and files responses while preserving the list-returning `fetch_material_diagnosis()` API. Files are validated, atomically retained, then parsed through `rpa_csv_parser.py`; the CLI passes a deterministic artifact directory without changing local processing modes.

**Tech Stack:** Python 3.9, `unittest`, `requests`, existing CSV parser and FFmpeg fixtures.

---

### Task 1: Define The Files-Envelope Adapter Contract

**Files:**
- Modify: `scripts/rpa_adapter.py:1-189`
- Modify: `tests/test_peak_frames.py:97-238`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write the failing nested-files adapter test**

Add `RpaAdapterTests.test_fetch_material_diagnosis_downloads_nested_files_envelope`. Use this sanitized POST payload and GET body:

```python
envelope = {
    "success": True,
    "code": "200",
    "data": {
        "result_type": "files",
        "result_content": {
            "status": "COMPLETED",
            "failedInstances": 0,
            "files": [{"fileUrl": "https://files.example/diagnosis.csv"}],
        },
    },
}
csv_bytes = (
    b"recordType\x01materialId\x01interactionMetric\x01durationSec\x01metricValue\n"
    b"\x02interaction_series\x01m-files\x01CLICK_COUNT\x011\x012\n"
)
```

Use a temporary `diagnosis-csv` directory. Assert that `fetch_material_diagnosis(..., diagnosis_csv_dir=csv_dir)` returns one normalized row, creates `csv_dir / "m-files.csv"`, and calls `session.get()` with `stream=True` and `timeout=300`.

- [ ] **Step 2: Support separate POST and GET fake responses**

Replace `RecordingSession` with:

```python
class RecordingSession:
    def __init__(self, post_response, get_response=None):
        self.post_response = post_response
        self.get_response = post_response if get_response is None else get_response
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.post_response

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self.get_response
```

- [ ] **Step 3: Run the focused test and confirm red state**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.RpaAdapterTests.test_fetch_material_diagnosis_downloads_nested_files_envelope -v`

Expected: FAIL because `fetch_material_diagnosis()` has no `diagnosis_csv_dir` parameter.

- [ ] **Step 4: Add response unpacking and atomic CSV download helpers**

In `scripts/rpa_adapter.py`, import `dataclass` and `parse_diagnosis_csv`/`RpaCsvError`. Add these public/internal boundaries:

```python
@dataclass(frozen=True)
class GatewayFile:
    file_url: str


def unpack_gateway_result(result):
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and data.get("result_type") == "files":
            return GatewayFile(_files_response_url(result))
    return normalize_gateway_records(result)
```

Implement `_files_response_url(result)` to require `success is True`, `code in (200, "200")`, `status == "COMPLETED"`, `failedInstances == 0`, a nonempty `files` list, and a nonempty `files[0].fileUrl`. Its errors must not interpolate the URL.

Implement `download_diagnosis_csv(file_url, target_path, session=None, timeout=300)` by mirroring `download_video()`: create the parent, write response chunks to a UUID staging CSV, reject empty data, publish with `os.replace()`, and remove only staging data on failure. Its errors must say `RPA diagnostic CSV` but never include `file_url`.

- [ ] **Step 5: Parse retained files without changing callers' return type**

Change `fetch_material_diagnosis()` to accept `diagnosis_csv_dir=None`. After the POST succeeds, execute:

```python
gateway_result = unpack_gateway_result(response.json())
if isinstance(gateway_result, list):
    return gateway_result
if diagnosis_csv_dir is None:
    raise RpaAdapterError("diagnosis_csv_dir is required for RPA files responses")
target = Path(diagnosis_csv_dir) / f"{business_params['material_id']}.csv"
download_diagnosis_csv(gateway_result.file_url, target, session=session)
try:
    return parse_diagnosis_csv(target)
except RpaCsvError as exc:
    raise RpaAdapterError(str(exc)) from exc
```

Delete `test_normalize_gateway_records_rejects_file_results`; direct record-list and payload tests remain unchanged.

- [ ] **Step 6: Run focused tests and commit**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.RpaAdapterTests tests.test_rpa_csv_parser -v`

Expected: `OK`.

```bash
git add scripts/rpa_adapter.py tests/test_peak_frames.py
git commit -m "feat: parse RPA diagnostic files responses"
```

### Task 2: Retain CSV Artifacts Through The Online CLI

**Files:**
- Modify: `scripts/extract_peak_frames.py:109-131`
- Modify: `tests/test_peak_frames.py:1928-2101`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write a failing TOP-material CLI integration test**

Add `CliTests.test_rpa_top_materials_cli_processes_retained_files_response`. Build a one-material `top_materials.json`, a decodable source video, the nested envelope from Task 1, and control-delimited CSV bytes containing nine `CLICK_COUNT` rows plus a `material_info` row for `m-files`. Patch `rpa_adapter._default_session` to return the session, patch `extract_peak_frames.download_video` to copy the source video, and patch the environment with the RPA credentials fixture.

Call `extract_peak_frames.main()` with `--source rpa`, `--top-materials-json`, CUSTOM dates `2026-06-01` to `2026-06-02`, `--diagnosis-records-dir <root>/diagnosis-records`, and `--output-dir <root>/peak-frames`. Assert return code `0`, four JPEGs, normalized JSON at `diagnosis-records/m-files.json`, and raw CSV at `diagnosis-csv/m-files.csv`.

- [ ] **Step 2: Run the integration test and confirm red state**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_top_materials_cli_processes_retained_files_response -v`

Expected: FAIL because `_process_rpa_material()` does not pass a CSV artifact directory.

- [ ] **Step 3: Wire deterministic CSV paths into RPA processing**

Add this helper before `_process_rpa_material()`:

```python
def _diagnosis_csv_dir(args):
    if args.diagnosis_records_dir:
        return Path(args.diagnosis_records_dir).parent / "diagnosis-csv"
    return Path(args.output_dir) / "diagnosis-csv"
```

Pass `diagnosis_csv_dir=_diagnosis_csv_dir(args)` to `fetch_material_diagnosis()`. Do not change local JSON/CSV branches.

- [ ] **Step 4: Run the new and existing online CLI tests**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_top_materials_cli_processes_retained_files_response tests.test_peak_frames.CliTests.test_rpa_cli_fetches_downloads_and_reuses_local_pipeline tests.test_peak_frames.CliTests.test_rpa_cli_processes_top_materials_json -v`

Expected: `OK`.

- [ ] **Step 5: Commit the CLI artifact wiring**

```bash
git add scripts/extract_peak_frames.py tests/test_peak_frames.py
git commit -m "feat: retain RPA diagnostic CSV artifacts"
```

### Task 3: Cover Validation, Redaction, And Parsing Failures

**Files:**
- Modify: `tests/test_peak_frames.py:130-238`
- Modify: `scripts/rpa_adapter.py:126-189`
- Test: `tests/test_peak_frames.py`

- [ ] **Step 1: Write failing failure-path tests**

Add tests for nested file envelopes with `success=False`, `code="500"`, `status="RUNNING"`, `failedInstances=1`, empty files, and missing `fileUrl`. Add HTTP download failure and malformed CSV tests using a URL ending in `?secret=redacted`. Assert each raises `RpaAdapterError`, does not contain `secret=redacted`, and malformed CSV leaves its final `.csv` artifact available.

- [ ] **Step 2: Run adapter tests and confirm the intended failure coverage is red**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.RpaAdapterTests -v`

Expected: at least one test fails before the required validation or cleanup branch is implemented.

- [ ] **Step 3: Implement only missing guards and cleanup**

Use the Task 1 helper errors, preserve published CSV before parsing, and delete only staging files on download/write failure. Do not log or interpolate signed URLs.

- [ ] **Step 4: Run all adapter, CLI, and parser tests**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.RpaAdapterTests tests.test_peak_frames.CliTests tests.test_rpa_csv_parser -v`

Expected: `OK`.

- [ ] **Step 5: Commit failure coverage**

```bash
git add scripts/rpa_adapter.py tests/test_peak_frames.py
git commit -m "test: cover RPA files response failures"
```

### Task 4: Run Full Regression And Update The Skill Boundary

**Files:**
- Modify: `SKILL.md:58`
- Verify: `tests/test_peak_frames.py`
- Verify: `tests/test_rpa_csv_parser.py`
- Verify: `tests/test_top_materials.py`
- Verify: `tests/test_commercial_report.py`

- [ ] **Step 1: Replace the obsolete files-response limitation in `SKILL.md`**

Replace the statement that `result_type=files` is unsupported with text directing users to retain and inspect `diagnosis-csv/<materialId>.csv`; state that CSV download or parsing failures remain blocking.

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: `OK` with no failures or errors.

- [ ] **Step 3: Commit documentation adjustment**

```bash
git add SKILL.md
git commit -m "docs: document retained RPA diagnostic CSVs"
```

Do not create an empty commit.

## Plan Self-Review

- Spec coverage: Task 1 supplies isolated response unpacking, validation, atomic download, parsing, and a stable list-returning contract. Task 2 retains CSV in deterministic online artifacts. Task 3 tests failures and signed-URL redaction. Task 4 validates all modules and removes the obsolete skill limitation.
- Placeholder scan: every test type, helper signature, artifact path, and verification command is specified.
- Consistency: `diagnosis_csv_dir`, `GatewayFile`, `unpack_gateway_result`, and `download_diagnosis_csv` retain the same names across all tasks.
