# RPA Output Contract

## Connector

| Field | Value |
| --- | --- |
| `function_code` | `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis` |
| `platform` | `RPA_JULIANG_QC_ACCOUNT_PASSWORD` |
| `data_source_type` | `rpa` |

The adapter calls the Yuce data gateway with fixed connector identity. Do not ask the user or an agent to handwrite `function_code` or `platform`.

## Runtime Environment

Online mode requires:

- `ENV_BACKEND_HOST`
- `YUCE_AUTHORIZATION`
- `YUCE_SESSION_ID`
- CLI `--shop-id`, from the platform RPA authorization account list

The adapter calls:

- `POST /adg/v1/agent/rpa/authorizations/query` to list authorized 巨量千川 accounts.
- `POST /adg/v1/agent/invoke` with a 3600 second timeout to fetch diagnosis records.

## Required Local Records

The JSON root is a list. Peak selection reads only records with:

| Field | Required value or type |
| --- | --- |
| `recordType` | `interaction_series` |
| `interactionMetric` | `CLICK_COUNT` |
| `durationSec` | Finite non-negative number |
| `metricValue` | Finite number |
| `materialId` | Consistent string when present |

Identical duplicate time/value rows are collapsed. Conflicting values at the same second are invalid.

```json
[
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":0,"metricValue":0,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":1,"metricValue":2,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":2,"metricValue":0,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":3,"metricValue":9,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":4,"metricValue":0,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":5,"metricValue":7,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":6,"metricValue":0,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":7,"metricValue":8,"materialId":"m-123"},
  {"recordType":"interaction_series","interactionMetric":"CLICK_COUNT","durationSec":8,"metricValue":0,"materialId":"m-123"}
]
```

This example selects second 0 as the fixed opening frame, then seconds 3, 7, and 5 by global local-peak click rank.

## Online Video Fields

Online mode reads the `material_info` record field `videoUrl`, downloads it to a temporary local video file, and then reuses the same local processing pipeline. `videoDuration` may be present in the connector output, but FFmpeg remains the source of truth for frame timestamp bounds. The target is `zero_second` plus up to three local peaks. 少于 3 个局部峰值 is a curve characteristic, not a technical failure. Postprocessing succeeds when `zero_second` exists and, if peaks were expected, at least one peak frame exists (至少一张峰值帧). A failed expected frame is retained in `summary.json.missingFrames` and must be disclosed in that material's 单素材深度分析. Signal-terminated FFmpeg extraction receives one local 低资源重试 and never triggers an RPA refetch.

Downloads are bounded before and during transfer. Diagnostic CSV files default to a 100 MiB
maximum, videos default to a 1 GiB maximum, and each download has a 600 second wall-clock
deadline. A declared or observed size over the limit, or a deadline overrun, is classified as
the current material's download failure; the staging file is removed and the HTTP response is
closed before the error is returned.

The isolated online-RPA preflight uses `spawn` on Windows and `fork` on Unix-like systems.

Downstream report preparation keeps `material_info` as structured facts: `prepare_diagnostic_analysis.py` injects `videoUrl`, `thumbnailUrl`, `videoDuration`, and `materialUri` into each selected material when present. Final HTML rendering may expose `videoUrl` through an external `<video>` preview with `preload="none"`, because 巨量千川 URLs are transient signed addresses and should not auto-download during report open. Use `render_final_report.py --copy-assets` for commercial delivery: retained JPEG high-light frames are copied into `final-report/assets/highlight-frames/` and referenced by relative paths, so the HTML avoids a large base64 data island. Use `--embed-assets` only for small one-file debug reports. video bytes must never be embedded into the HTML.

## Wave RPA Batch

Top-N batch mode treats asset-table `top_materials.json` as a rank-ordered candidate pool, not a complete RPA request list. The default candidate limit is three times targetTopN. `rpaConcurrency` is the maximum request count in one RPA wave. The default `waveBarrier=true` scheduler submits candidates from the head as one wave and waits until the 当前波次全部完成 before submitting the next wave; a candidate becomes selected only when:

- the connector call returns parseable diagnosis records;
- the record list contains the target material's `material_info`;
- `material_info.videoUrl` is a non-empty string.

RPA success writes normalized records before postprocessing starts. Video download, FFmpeg extraction, and summary generation immediately follow selection on a separate bounded channel. The coordinator collects and checkpoints completed postprocess futures while RPA waits, but postprocess completion never opens the next RPA wave. A wave completes only after every submitted request returns normally, reaches its hard timeout, raises an exception, or is successfully recovered from a matching late CSV/JSON artifact. RPA failure means "RPA currently unavailable for this material"; it is not a deletion verdict.

Failure reasons:

- `rpa_request_failed`: gateway, HTTP, network, or configured RPA request failure.

The batch runner records a slow request after 8 分钟 (480 seconds) and uses a 10 分钟 (600 seconds) hard request limit by default. Crossing the soft threshold only records `rpaSoftTimeoutExceeded=true`; the isolated request remains alive and may still write its records/CSV, so it is not rejected or backfilled. Only a request that remains unresolved through the hard limit is terminated, marked `rpaHardTimeoutExceeded=true`, and classified as `rpa_request_failed`. At the hard-timeout boundary the runner waits 30 seconds for the request or a newly written, parseable records JSON/diagnosis CSV with a matching `material_info.videoUrl`; recovered output is treated as RPA-selected and proceeds to postprocess.
- `rpa_payload_invalid`: gateway payload could not be normalized into usable records.
- `missing_material_info`: no matching `material_info` record was returned.
- `missing_video_url`: matching `material_info` exists but has no usable `videoUrl`.
- `unknown_preflight_failure`: unexpected failure outside the known classes.
- `target_satisfied`: a candidate was never submitted because `postprocessSucceededCount >= targetTopN`.

The wave scheduler writes:

- `batch-manifest.json`: schemaVersion 2 record with `targetTopN`, `candidateCount`, `candidateLimit`, `rpaConcurrency`, `postprocessConcurrency`, `waveBarrier`, `submittedMaterials`, `selectedMaterials`, `rejectedMaterials`, and `cancelledMaterials`. Every submitted candidate records rank, material metadata, RPA state, postprocess state, failure reason, paths, timings, `waveIndex`, `waveSize`, `waveStartedAtSec`, and `waveFinishedAtSec`.
- `top_materials_rpa_ready.json`: compatibility top-materials shape containing only the rank-ordered final postprocess-success materials.
- `diagnosis-records/<materialId>.json`: normalized records written before a candidate joins selected.
- `diagnosis-csv/<materialId>.csv`: retained source CSV for a files-envelope response.

The manifest also exposes `stageCounts` with four separate meanings:

- `rpaSelectedCount`: records, matching `material_info`, and non-empty `videoUrl` are available.
- `recordsPersistedCount`: normalized records JSON is present on disk, parses as a list, and contains the matching material ID. Legacy entries without `recordsPersisted` are counted only after this validation; unverifiable legacy entries are reported separately as `recordsPersistedUnknownCount`.
- `postprocessSucceededCount`: video download, frame extraction, and both summaries succeeded.
- `reportReadyCount`: batch-level 0/1 flag set only after the complete fact package and final report data/HTML validate.

Report readiness additionally requires final-report data schema version 1 with `status=complete`,
the rendered HTML to contain a parseable `script#report-data` without the template placeholder,
every referenced copied highlight asset to exist, and the report material IDs and `runId` to
match the batch manifest. The readiness marker records the same `runId`.

The wave output directory is protected by `.batch-run.lock` and a unique `runId`. A stale lock with `running` or `submitted` manifest entries requires explicit recovery; it must not be silently overwritten.

Local repair is available with `python scripts/repair_manifest.py --output-dir <peak-frames-dir>`. It performs `CSV -> records`, `records -> 后处理`, and valid summary/JPEG -> manifest repairs without calling RPA. If the manifest is missing, pass `--top-materials-json`; artifact-only recovery uses `rpaStatus: unknown` until records prove RPA selection. The resulting `repair-manifest.json` records `rpaCalls: 0`, actions, errors, and stage counts.

Counting terms are intentionally separate. `assetSelectedMaterialCount` is the number of rank-ordered material IDs loaded from the asset-table candidate pool for this run, equivalent to `candidateCount` and capped by `candidateLimit`. `rpaSubmittedMaterialCount` is the number of material IDs actually sent to the RPA connector, equivalent to the length of `submittedMaterials`. Batch completion requires `postprocessSucceededCount >= targetTopN`; the scheduler never submits the entire candidate pool up front, and submits from the candidate head by waves until that final-success condition is met or the candidate pool is exhausted. Wave timing measures RPA boundaries only and must not be used as an end-to-end or postprocess timing.

Selected and ready-list ordering always follows source candidate rank, even when lower-ranked RPA jobs finish first. Once the final-success condition is met the scheduler stops submitting new RPA work and unsubmitted candidates are cancelled. 后处理失败 remains selected with `postprocessStatus: failed`, does not become an RPA rejection, receives one local retry using retained records, and then triggers rank-ordered 补位 after its second failure. A batch is nonzero only when the candidate pool is exhausted before `postprocessSucceededCount >= targetTopN`.

`--preflight-only`, `--rpa-preflight-json`, and `--allow-rpa-refetch` remain compatibility and diagnostic tools for old runs. They are not part of the recommended wave Top-N command.

## Connector Inputs

Supported `date_range_type` values:

- `YESTERDAY`
- `LAST_7_DAYS`
- `LAST_15_DAYS`
- `LAST_WEEK`
- `THIS_MONTH`
- `LAST_MONTH`
- `CUSTOM`

`CUSTOM` requires both `custom_start_date` and `custom_end_date` in `YYYYMMDD` or `YYYY-MM-DD` form.

Skill-driven natural-language date ranges should not be passed through as relative connector enums. 自然语言近七天、近三十天必须换算为 CUSTOM closed intervals using the skill's T+2 data-availability rule before calling the connector.

## Gateway Result Handling

The connector documentation says successful gateway responses may be `payload` or `files`.

- Direct list responses are treated as connector records.
- `result_type=payload` must contain a record list in `payload`.
- JSON-string payloads are decoded before processing.
- `result_type=files` must report a successful completed task with at least one diagnostic CSV. The workflow downloads the first CSV to the local diagnostic-CSV directory, parses it with `parse_diagnosis_csv`, and uses the resulting normalized records exactly as it would a direct payload.
- Signed diagnostic CSV URLs are transient download inputs. They are not retained in diagnosis-record JSON, peak-frame summaries, or downstream diagnostic-analysis artifacts.
