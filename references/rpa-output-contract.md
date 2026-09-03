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
- `POST /adg/v1/agent/fetch/tasks` to submit diagnosis records, followed by `POST /adg/v1/agent/fetch/tasks/status` using the returned `task_group_id`.

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

## Diagnosis Record Shape

`recordType` is the discriminator for every normalized diagnosis record. It is not a nested object name. The connector emits `interaction_series`, `crowd_analysis`, `delivery_material_detail`, `material_info`, and `script_analysis` records in one flat JSON list; each record keeps its fields as top-level fields.

For online frame extraction, locate the record with `recordType == "material_info"` and the target `materialId`, then read its top-level fields `videoUrl`, `thumbnailUrl`, `videoDuration`, and `materialUri`. Do not probe a nested `.material_info` field or conclude that a video URL is absent before enumerating `recordType` values and checking the matching flat record.

## Online Video Fields

Online mode reads the `material_info` record field `videoUrl`, downloads it to a temporary local video file, and then reuses the same local processing pipeline. `videoDuration` may be present in the connector output, but FFmpeg remains the source of truth for frame timestamp bounds. The target is `zero_second` plus up to three local peaks. 少于 3 个局部峰值 is a curve characteristic, not a technical failure. Postprocessing succeeds when `zero_second` exists and, if peaks were expected, at least one peak frame exists (至少一张峰值帧). A failed expected frame is retained in `summary.json.missingFrames` and must be disclosed in that material's 单素材深度分析. Signal-terminated FFmpeg extraction receives one local 低资源重试 and never triggers an RPA refetch.

For performance, all selected timestamps for one material are extracted in one FFmpeg process using a split filter graph with output-side accurate `trim` branches. The input is bounded through the latest requested timestamp; input-side `-ss` and `-noaccurate_seek` are forbidden because frames must remain tied to the original `CLICK_COUNT` seconds. If the batch command or any staged JPEG fails validation, the material falls back to the existing per-frame accurate extractor and preserves the normal missing-frame and success rules.

Downloads are bounded before and during transfer. Diagnostic CSV files default to a 100 MiB
maximum, videos default to a 1 GiB maximum, and each download has a 600 second wall-clock
deadline. A declared or observed size over the limit, or a deadline overrun, is classified as
the current material's download failure; the staging file is removed and the HTTP response is
closed before the error is returned.

The isolated online-RPA preflight uses `spawn` on Windows and `fork` on Unix-like systems.

Downstream report preparation keeps `material_info` as structured facts: `diagnostic_analysis_lib.prepare_diagnostic_analysis()` injects `videoUrl`, `thumbnailUrl`, `videoDuration`, and `materialUri` into each selected material when present. Final HTML rendering may expose `videoUrl` through an external `<video>` preview with `preload="none"`, because 巨量千川 URLs are transient signed addresses and should not auto-download during report open. Use `python scripts/run_standard_analysis.py finalize --output-dir <run-dir> --analysis-json <analysis-json>` for commercial delivery: standard finalize copies retained JPEG high-light frames into `final-report/assets/highlight-frames/`, references them by relative paths, and packages `final-report.zip`. Recipients extract it and open `final-report/final-report.html`. Video bytes must never be downloaded, embedded, or added to the ZIP; video and thumbnail URLs remain platform URLs.

## Wave RPA Batch

Top-N batch mode treats report-derived `top_materials.json` as a rank-ordered candidate pool, not a complete RPA request list. The default candidate limit is three times targetTopN. `rpaConcurrency` is the maximum request count in one RPA wave. The default `waveBarrier=true` scheduler submits candidates from the head as one wave and waits until the 当前波次的 RPA 与后处理全部进入终态 before submitting the next wave; a candidate becomes selected only when:

- the connector call returns parseable diagnosis records;
- the record list contains the target material's `material_info`;
- `material_info.videoUrl` is a non-empty string.

RPA success writes normalized records before postprocessing starts. Video download, FFmpeg extraction, and summary generation immediately follow selection on a separate bounded channel. The coordinator checkpoints completed postprocess futures while the current wave RPA waits; a wave completes only after every submitted request reaches `available` or `final_failed` and every selected material's postprocess reaches a terminal result. A transport failure first enters result-pending recovery and is not a deletion verdict.

Failure reasons:

- `rpa_request_failed`: gateway, HTTP, network, or configured RPA request failure. Its initial state is `rpaResultStatus: pending` and `retryable: true`.
- `rpa_request_timeout`: the single local RPA execution deadline expired. It is immediately `final_failed` and not retryable.

The connector uses asynchronous `task_group_id` submission and status polling. Remote polling is bounded at 3600 seconds, diagnostic CSV download has a 600-second total deadline, and the isolated RPA preflight default is 4260 seconds to cover both budgets plus a 60-second margin. An explicit shorter CLI timeout remains supported for tests or operations and may expire before the remote task completes. On expiry, it terminates the local worker and records `failureReason: rpa_request_timeout`, `rpaTimeoutExceeded: true`, the configured `rpaTimeoutSec` (4260 by default), and `rpaResultStatus: final_failed`. A duration timeout is terminal and never retries or consumes a local-artifact grace budget; it releases its local RPA concurrency slot. Existing manifests may retain legacy `rpaSoftTimeoutExceeded` and `rpaHardTimeoutExceeded` fields for read compatibility, but new runs do not write them.

For `completed` and `partial_success` tasks, the first usable `fileUrl` is downloaded atomically and parsed before the material is accepted. A `partial_success` response with files is persisted together with its safe errors; a payload response still has no CSV and therefore cannot satisfy the verified-CSV contract. If a worker is interrupted after submission, `--resume` uses the saved `task_group_id` to query the same task and 不得重复提交; a receipt without that ID follows the existing resubmission path.

For a retryable `rpa_request_failed` that returns in less than 60 seconds, the next request waits 45 seconds before the first retry and doubles that wait for each later retry. The wait remains inside the current wave, so `rpaConcurrency=3` still caps parallel RPA calls and no later wave opens early. HTTP 429/5xx and transport failures are retryable; explicit gateway 4xx responses are final and not retried. A duration timeout is distinct: it does not retry, and the next full wave starts only after every material in the current wave reaches `available` or `final_failed` and each selected material's postprocess reaches a terminal result. Late CSV or records from a timed-out worker are not accepted into the advanced run; reconcile them only through explicit recovery. Every retry recovery attempt retains safe diagnostics when available: HTTP status, gateway request ID, a redacted response summary, worker stage, child exit code/signal, result-queue state, and an exception traceback summary.
- `rpa_payload_invalid`: gateway payload could not be normalized into usable records; it is immediately `final_failed` and not retried.
- `missing_material_info`: no matching `material_info` record was returned.
- `missing_video_url`: matching `material_info` exists but has no usable `videoUrl`.
- `unknown_preflight_failure`: unexpected failure outside the known classes.
- `target_satisfied`: a candidate was never submitted because `postprocessSucceededCount >= targetTopN`.

`submitted` alone proves only that the local client started an invocation. It is not evidence of platform failure or success. The local receipt can reconcile a downloaded CSV or persisted records, but cannot retrieve a platform-completed task if the invocation failed before the client received a CSV URL. That requires a platform query API keyed by a client or platform request identifier.

With `waveBarrier=true`, the scheduler requires both RPA and postprocess terminality before it opens the next RPA wave. Every current-wave RPA request must reach `available` or `final_failed`; every selected current-wave material must then finish download, frame extraction, and summary generation, either successfully or after its local retries are exhausted. A terminal postprocess failure releases the next wave so that a ranked candidate can be submitted as a backfill. The batch is not ready for downstream facts or reporting until the required postprocess futures have completed successfully and verification passes.

The wave scheduler writes:

- `batch-manifest.json`: schemaVersion 2 record with `targetTopN`, `candidateCount`, `candidateLimit`, `rpaConcurrency`, `postprocessConcurrency`, `waveBarrier`, `submittedMaterials`, `selectedMaterials`, `rejectedMaterials`, and `cancelledMaterials`. Every submitted candidate records rank, material metadata, RPA state, `rpaResultStatus`, `rpaRecoveryAttempts`, `errorType`/`errorMessage`, postprocess state, failure reason, paths, timings, `waveIndex`, `waveSize`, `waveStartedAtSec`, and `waveFinishedAtSec`.
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

Local repair is available with `python scripts/repair_manifest.py --output-dir <peak-frames-dir>`. It first validates `rpa-receipts/<materialId>.json` in `records_persisted` state, then performs `CSV -> records`, `records -> 后处理`, and valid summary/JPEG -> manifest repairs without calling RPA. A valid receipt overrides a stale manifest checkpoint and restores the material as `rpaStatus: selected` and `rpaResultStatus: available` before local frame extraction. If the manifest is missing, pass `--top-materials-json`; artifact-only recovery uses `rpaStatus: unknown` until records prove RPA selection. The resulting `repair-manifest.json` records `rpaCalls: 0`, actions, errors, and stage counts.

Counting terms are intentionally separate. `assetSelectedMaterialCount` is the legacy field name for the number of rank-ordered material IDs loaded from the report candidate pool for this run, equivalent to `candidateCount` and capped by `candidateLimit`. `rpaSubmittedMaterialCount` is the number of material IDs actually sent to the RPA connector, equivalent to the length of `submittedMaterials`. Batch completion requires `postprocessSucceededCount >= targetTopN`; the scheduler never submits the entire candidate pool up front, and submits from the candidate head by waves until that final-success condition is met or the candidate pool is exhausted. Wave timing measures RPA boundaries only and must not be used as an end-to-end or postprocess timing.

Selected and ready-list ordering always follows source candidate rank, even when lower-ranked RPA jobs finish first. Once the final-success condition is met the scheduler stops submitting new RPA work and unsubmitted candidates are cancelled. 后处理失败 remains selected with `postprocessStatus: failed`, does not become an RPA rejection, receives one local retry using retained records, and then triggers rank-ordered 补位 after its second failure. A batch is nonzero only when the candidate pool is exhausted before `postprocessSucceededCount >= targetTopN`.

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
