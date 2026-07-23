# Qianchuan RPA Files Envelope Design

## Goal

Allow online Qianchuan RPA diagnosis runs to consume the connector's nested `data.result_type=files` response, retain the downloaded diagnostic CSV, normalize it into records, and reuse the existing video and peak-frame pipeline.

## Scope

- Recognize direct record lists, existing payload envelopes, and the observed nested files envelope.
- Download the first returned diagnostic CSV atomically and retain it as a run artifact.
- Parse retained CSV files with `parse_diagnosis_csv()` and keep `fetch_material_diagnosis()` returning a record list.
- Add adapter and batch CLI tests using a sanitized copy of the observed gateway response shape.

## Non-goals

- Change asset-table selection, ranking, date-range, peak selection, or video download rules.
- Add retries, concurrency, a new batch entrypoint, or SQLite aggregation.
- Print signed OSS URLs or their query parameters in errors or output.

## Gateway Contract

The files envelope has this relevant shape:

```json
{
  "success": true,
  "code": "200",
  "data": {
    "result_type": "files",
    "result_content": {
      "status": "COMPLETED",
      "failedInstances": 0,
      "files": [{"fileUrl": "https://files.example/diagnosis.csv"}]
    }
  }
}
```

The adapter validates `success`, `code`, `status`, `failedInstances`, a nonempty files list, and `files[0].fileUrl` before making a download request. It accepts `code` as either the string `"200"` or integer `200`, matching gateway variations. It reports validation and download failures without including the URL.

## Design

`rpa_adapter.py` gains a response-unpacking boundary that returns either a record list or a file URL descriptor. `normalize_gateway_records()` remains responsible only for list and payload normalization. The orchestration function `fetch_material_diagnosis()` handles a returned file descriptor by downloading its CSV, parsing it, and returning the parsed record list to all existing callers.

CSV download uses a staging filename and `os.replace()` to publish the final artifact. The file remains on disk after parsing, including when parsing fails, so a malformed real export is available for review. A failed HTTP download removes only its staging file.

The CLI resolves the raw CSV directory without a new command-line option:

- when `--diagnosis-records-dir` is present, use its parent plus `diagnosis-csv`;
- otherwise use `<output-dir>/diagnosis-csv`.

For a batch run with `/run/diagnosis-records`, the artifacts are therefore `/run/diagnosis-csv/<materialId>.csv` and `/run/diagnosis-records/<materialId>.json`.

## Validation

Tests use a sanitized files envelope and control-character CSV bytes. They verify:

- files-envelope validation and parsed record output;
- raw CSV retention and no signed URL leakage;
- missing URLs, incomplete or failed RPA task status, HTTP download failure, and malformed CSV handling;
- a TOP-material CLI run whose real adapter receives a files envelope and produces normalized records and peak-frame output.

The existing test that rejects files responses is removed because files are now a supported online RPA contract.

## Self-Review

The design preserves the existing list-returning adapter API and only changes the online files-response boundary. It assigns raw CSV ownership to a deterministic artifact directory and keeps sensitive signed URLs out of messages and output.
