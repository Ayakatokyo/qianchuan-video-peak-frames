# Qianchuan Diagnostic Analysis Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform retained Qianchuan diagnostic records into a stable AI fact package and editable prompt, without RPA, video, model, or report calls.

**Architecture:** `scripts/diagnostic_analysis_lib.py` owns parsing, aggregation, sanitization, and atomic publication. `scripts/prepare_diagnostic_analysis.py` is its CLI. The report library remains unchanged.

**Tech Stack:** Python 3 standard library: `argparse`, `ast`, `json`, `math`, `os`, `pathlib`, `uuid`, and `unittest`.

---

## File Structure

- Create `scripts/diagnostic_analysis_lib.py`: retained-record transformation and paired-output API.
- Create `scripts/prepare_diagnostic_analysis.py`: offline CLI adapter.
- Create `prompts/diagnostic-analysis-prompt-v1.md`: versioned default prompt source.
- Create `tests/test_diagnostic_analysis.py`: library and subprocess CLI tests.
- Modify `references/rpa-output-contract.md`: document currently supported `files` responses.

### Task 1: Write the three-metric audience aggregation

**Files:** Create `tests/test_diagnostic_analysis.py`; create `scripts/diagnostic_analysis_lib.py`.

- [ ] **Step 1: Write the failing audience tests**

```python
analysis = build_diagnostic_analysis(top_payload, records_by_material)
metrics = analysis["audience"]["metrics"]
assert set(metrics) == {"SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT"}
assert metrics["SHOW_COUNT"]["dimensions"]["age"]["buckets"] == [
    {"bucket": "24-30", "metricValue": 300.0, "metricPercentage": 60.0,
     "sourceMaterialIds": ["m-1"]},
    {"bucket": "18-23", "metricValue": 200.0, "metricPercentage": 40.0,
     "sourceMaterialIds": ["m-1", "m-2"]},
]
assert metrics["CLICK_COUNT"]["missingMaterialIds"] == ["m-2"]
assert metrics["TRANSACTION_AMOUNT"]["dimensions"]["gender"]["totalMetricValue"] == 60.0
```

Fixtures include intentionally wrong source percentages, nested `Dimensions`, mismatched `materialId`, negative values, and an unknown metric.

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisAudienceTests -v`

Expected: `ModuleNotFoundError: No module named 'diagnostic_analysis_lib'`.

- [ ] **Step 3: Implement the contract and aggregation**

```python
SCHEMA_VERSION = 1
AUDIENCE_METRICS = ("SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT")

def build_diagnostic_analysis(top_payload, records_by_material):
    materials, warnings = _selected_materials(top_payload)
    selected_ids = [item["materialId"] for item in materials]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "period": _period(top_payload), "materials": materials,
        "audience": {"metrics": {
            metric: _aggregate_audience_metric(records_by_material, selected_ids, metric)
            for metric in AUDIENCE_METRICS
        }},
        "scripts": _build_scripts(records_by_material, selected_ids, warnings),
        "delivery": {"materials": _build_delivery(records_by_material, selected_ids)},
        "warnings": warnings,
    }
```

Accept a row only when it is `crowd_analysis`, has an exact target `displayMetric`, a matching-or-empty `materialId`, nonempty dimension/bucket, and finite nonnegative `metricValue`. Sum by metric, dimension, and bucket; recompute bucket percent from the dimension total; record sorted source IDs on dimensions and buckets. Sort buckets by descending value then bucket text, and populate `missingMaterialIds` independently for every metric.

- [ ] **Step 4: Verify the test passes**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisAudienceTests -v`

Expected: PASS.

### Task 2: Write script and delivery fact preservation

**Files:** Modify `tests/test_diagnostic_analysis.py`; modify `scripts/diagnostic_analysis_lib.py`.

- [ ] **Step 1: Write failing script and delivery tests**

```python
script = analysis["scripts"]["materials"][0]
assert script == {
    "materialId": "m-1", "materialUri": "uri-1", "text": "完整口播",
    "labels": [{"level1": "价格优惠", "level2": "降价/折扣"}],
    "evidence": [{"level2": "降价/折扣", "sentence": "今天四九折"}],
}
assert analysis["scripts"]["labelSummary"] == [{
    "level1": "价格优惠", "level2": "降价/折扣", "occurrenceCount": 2,
    "materialIds": ["m-1", "m-2"], "evidence": ["今天四九折"],
}]
assert analysis["delivery"]["materials"][0]["facts"][0]["values"] == {
    "clickCount": 1490.0, "clickRate": "0.89%", "payRoi": 2.3,
}
assert any("invalid scriptInfo" in item for item in analysis["warnings"])
```

Cover Python-literal and JSON script structures, duplicate evidence, malformed script data, comma-formatted numbers, percentage strings, and nonfinite delivery values.

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisScriptAndDeliveryTests -v`

Expected: FAIL because script and delivery facts are absent.

- [ ] **Step 3: Implement safe extraction and no-ratio-aggregation delivery facts**

```python
def _parse_script_info(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("scriptInfo must be a nonempty structured string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(value)
    if not isinstance(parsed, dict):
        raise ValueError("scriptInfo must decode to an object")
    return parsed
```

Extract labels from `data[].level_1_label` and `level_2_labels`; attach evidence only from `detail[level2.value].list`, preferring the detail label. Deduplicate per-material labels/evidence and roll up `(level1, level2)` with occurrence count, material IDs, and unique evidence. Preserve each `delivery_material_detail` as `{statDate, values}` under the selected material. Exclude identity/transport fields; normalize unambiguous finite numbers (including commas), preserve percentage text, and never calculate ROI, CTR, or rate averages.

- [ ] **Step 4: Verify the test passes**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisScriptAndDeliveryTests -v`

Expected: PASS.

### Task 3: Write retained-input loading and paired atomic publication

**Files:** Modify `tests/test_diagnostic_analysis.py`; modify `scripts/diagnostic_analysis_lib.py`; create `prompts/diagnostic-analysis-prompt-v1.md`.

- [ ] **Step 1: Write failing publication tests**

```python
data_path, prompt_path, analysis = prepare_diagnostic_analysis(
    top_materials_json, diagnosis_records_dir, output_dir
)
assert data_path.name == "diagnostic-analysis-data.json"
assert prompt_path.name == "diagnostic-analysis-prompt.md"
assert json.loads(data_path.read_text(encoding="utf-8")) == analysis
assert prompt_path.read_text(encoding="utf-8") == DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
assert "https://signed.example" not in data_path.read_text(encoding="utf-8")
assert "missing diagnosis records for material m-2" in analysis["warnings"]
```

Add exact custom-template copy and failed-publication tests: a failure before either staged write completes leaves existing files unchanged, and every staging file is removed after an error.

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisPublicationTests -v`

Expected: FAIL because the API and template do not exist.

- [ ] **Step 3: Implement loading, redaction, and output publication**

```python
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "diagnostic-analysis-prompt-v1.md"

def prepare_diagnostic_analysis(top_materials_json, diagnosis_records_dir, output_dir, prompt_template=None):
    top_payload = _read_json(top_materials_json, "top materials JSON")
    records_by_material, read_warnings = _load_selected_records(top_payload, diagnosis_records_dir)
    analysis = build_diagnostic_analysis(top_payload, records_by_material)
    analysis["warnings"] = read_warnings + analysis["warnings"]
    prompt = _read_text(prompt_template or DEFAULT_PROMPT_PATH, "diagnostic prompt template")
    return _publish_output_pair(output_dir, analysis, prompt)
```

Require a top-level JSON object with nonempty materials. Retain selected material ID, rank, name, types, and finite cost; missing/malformed/non-list record files create warnings without dropping materials. Do not modify inputs. Remove signed/video URLs from derived data. Stage both output files in the chosen output directory before replacing either final target, then use `os.replace` for each final artifact so each publication is atomic; remove staging files after errors.

The default prompt identifies itself as `diagnostic-analysis-prompt-v1`, treats `diagnostic-analysis-data.json` as the sole fact source, asks for a separate `diagnostic-ai-analysis.json`, prohibits invented facts, distinguishes correlation from causation, demands citations to material ID / metric / dimension / label, and keeps the three audience metrics separate.

- [ ] **Step 4: Verify the test passes**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisPublicationTests -v`

Expected: PASS.

### Task 4: Write the offline CLI and correct connector documentation

**Files:** Modify `tests/test_diagnostic_analysis.py`; create `scripts/prepare_diagnostic_analysis.py`; modify `references/rpa-output-contract.md`.

- [ ] **Step 1: Write a failing subprocess CLI test**

```python
completed = subprocess.run([
    sys.executable, str(ROOT / "scripts" / "prepare_diagnostic_analysis.py"),
    "--top-materials-json", str(top_materials),
    "--diagnosis-records-dir", str(records_dir), "--output-dir", str(output_dir),
], capture_output=True, text=True)
assert completed.returncode == 0, completed.stderr
assert "diagnostic_analysis_data=" in completed.stdout
assert "diagnostic_analysis_prompt=" in completed.stdout
```

Also assert invalid input exits `2` and the CLI only imports the new processor library.

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisCliTests -v`

Expected: FAIL because `scripts/prepare_diagnostic_analysis.py` is missing.

- [ ] **Step 3: Implement the narrow CLI**

```python
def build_parser():
    parser = argparse.ArgumentParser(description="Prepare Qianchuan diagnostic AI-analysis facts.")
    parser.add_argument("--top-materials-json", required=True)
    parser.add_argument("--diagnosis-records-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prompt-template")
    return parser

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data_path, prompt_path, _analysis = prepare_diagnostic_analysis(**vars(args))
    except DiagnosticAnalysisError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"diagnostic_analysis_data={data_path}")
    print(f"diagnostic_analysis_prompt={prompt_path}")
    return 0
```

Replace the reference claim that `result_type=files` is rejected. Document validation of a completed files envelope, download of its first CSV to the diagnostic-CSV directory, parsing through `parse_diagnosis_csv`, and transient treatment of signed URLs.

- [ ] **Step 4: Verify the test passes**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisCliTests -v`

Expected: PASS.

### Task 5: Run isolation and full regressions

**Files:** Modify `tests/test_diagnostic_analysis.py`.

- [ ] **Step 1: Add an end-to-end isolation test**

```python
analysis = prepare_diagnostic_analysis(top_materials, records_dir, output_dir)[2]
assert analysis["schemaVersion"] == 1
assert original_records_text == records_path.read_text(encoding="utf-8")
assert not (output_dir / "report.html").exists()
assert analysis["audience"]["metrics"]["SHOW_COUNT"] != analysis["audience"]["metrics"]["CLICK_COUNT"]
```

The fixture covers two selected materials, all metrics, labels/evidence, delivery facts, one missing record, and signed video URLs absent from outputs.

- [ ] **Step 2: Run the processor suite**

Run: `python3 -m unittest tests.test_diagnostic_analysis -v`

Expected: PASS.

- [ ] **Step 3: Run legacy and full regressions**

Run: `python3 -m unittest tests.test_diagnostic_analysis tests.test_commercial_report tests.test_rpa_csv_parser tests.test_peak_frames -v`

Expected: PASS, with only the existing environment `urllib3`/LibreSSL warning allowed.

- [ ] **Step 4: Inspect and commit the isolated feature**

Run: `git diff --check && git diff -- scripts/diagnostic_analysis_lib.py scripts/prepare_diagnostic_analysis.py prompts/diagnostic-analysis-prompt-v1.md tests/test_diagnostic_analysis.py references/rpa-output-contract.md`

Expected: no whitespace errors and no report-library modifications.

```bash
git add scripts/diagnostic_analysis_lib.py scripts/prepare_diagnostic_analysis.py prompts/diagnostic-analysis-prompt-v1.md tests/test_diagnostic_analysis.py references/rpa-output-contract.md docs/superpowers/plans/2026-07-24-qianchuan-diagnostic-analysis-data.md
git commit -m "feat: prepare qianchuan diagnostic analysis data"
```
