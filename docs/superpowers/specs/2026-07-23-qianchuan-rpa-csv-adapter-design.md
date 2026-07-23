# Qianchuan RPA CSV Adapter Design

## Goal

Accept the CSV diagnostic files emitted by the Qianchuan single-material RPA and normalize them into the existing record-list contract used by peak-frame extraction and commercial reporting.

## Scope

- Add a local CSV input path to `extract_peak_frames.py`.
- Download `material_info.videoUrl` automatically when CSV input omits `--video`.
- Parse the real export format: UTF-8 text, `\x01` column separators, and an optional `\x02` record prefix.
- Preserve record fields while normalizing nested `Dimensions`, `Metrics`, and `Fields` values into objects.
- Populate `crowd_analysis.metricValue` from a single nested numeric `Metrics.*.Value` when the CSV column is empty.
- Reuse the existing peak selection and video-processing pipeline after conversion.

## Non-goals

- Change the RPA connector request parameters.
- Change peak selection rules or video download logic.
- Guess the gateway `result_type=files` envelope. The supplied CSV files establish the file-content contract but not the file URL field names. That integration requires one captured gateway response.

## Design

`rpa_csv_parser.py` owns parsing and validation. Its public function accepts a CSV path and returns `list[dict]`, the same contract already accepted by `process_records`. It rejects an unsupported delimiter, a missing header, an unterminated CSV field, malformed nested structured fields, and ambiguous `Metrics` values with a `RpaCsvError` containing the source and row number.

The CLI gains mutually exclusive `--input-json` and `--input-csv` options for local mode. The CSV path is converted in memory and handed to `process_records`; no intermediate JSON file is required. JSON input still requires `--video`; CSV input downloads `material_info.videoUrl` when `--video` is omitted. `--diagnosis-records-dir` is the optional persistent normalized-JSON artifact for either RPA record source.

## Validation

Tests use synthetic, sanitized rows mirroring the supplied exports. They verify control-character parsing, nested field normalization, peak selection from CSV, commercial audience aggregation, and actionable malformed-input errors. The full existing Python test suite remains the regression gate. Both supplied CSV files are run through the parser and peak selection after automated tests.

## Verification

- The 2026-06-01 to 2026-06-05 export parsed into 1,636 records, including 56 `CLICK_COUNT` points and five audience dimensions.
- The recent-seven-days export parsed into 1,742 records, including 82 `CLICK_COUNT` points and five audience dimensions.
- The first export and its RPA-provided video URL produced four JPEG peak frames and a normalized diagnosis JSON artifact.
- Gateway `result_type=files` envelope integration remains deferred until a raw response fixture supplies the file URL field shape.
