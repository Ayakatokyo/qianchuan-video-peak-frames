# RPA Output Contract

## Connector

| Field | Source |
| --- | --- |
| `connectorCode` / `function_code` | The only static identity: `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis` |
| `platform` | Runtime connector detail `platformCode`, forwarded unchanged |
| input contract | Runtime `inParamJsonSchemaText`; use `inParamList` only when schema text is `null` |
| `data_source_type` | `rpa` |

Standard `prepare` already checks authorization, reads live connector detail and validates parameters before submission; do not precede it with separate detail/schema/list-accounts/validate calls. For connector troubleshooting or an independent fetch, inspect `python scripts/rpa_dynamic_client.py detail` and `python scripts/rpa_dynamic_client.py schema`, and use `python scripts/rpa_dynamic_client.py validate --params-json '<JSON object>'` when checking parameters. Use `--params-file` for long or nested JSON and `list-accounts` when the RPA authorization ID is not yet confirmed. The adapter re-reads detail and validates immediately before task submission. Do not handwrite `platform`, rewrite `platformCode`, or treat detail `id` as connector code.

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

Online mode reads the `material_info` record field `videoUrl`, downloads it as the retained original MP4 under `video-assets/<materialId>.mp4`, and then reuses that local file for frame extraction. `videoDuration` may be present in the connector output, but FFmpeg remains the source of truth for frame timestamp bounds. The target is `zero_second` plus up to three local peaks. 少于 3 个局部峰值 is a curve characteristic, not a technical failure. Postprocessing succeeds when `zero_second` exists and, if peaks were expected, at least one peak frame exists (至少一张峰值帧). A failed expected frame is retained in `summary.json.missingFrames` and must be disclosed in that material's 单素材深度分析. Signal-terminated FFmpeg extraction receives one local 低资源重试 and never triggers an RPA refetch.

For performance, all selected timestamps for one material are extracted in one FFmpeg process using a split filter graph with output-side accurate `trim` branches. The input is bounded through the latest requested timestamp; input-side `-ss` and `-noaccurate_seek` are forbidden because frames must remain tied to the original `CLICK_COUNT` seconds. If the batch command or any staged JPEG fails validation, the material falls back to the existing per-frame accurate extractor and preserves the normal missing-frame and success rules.

Downloads are bounded before and during transfer. Diagnostic CSV files default to a 100 MiB
maximum, videos default to a 1 GiB maximum, and each download has a 600 second wall-clock
deadline. A declared or observed size over the limit, or a deadline overrun, is classified as
the current material's download failure; the staging file is removed and the HTTP response is
closed before the error is returned.

The isolated online-RPA preflight uses `spawn` on Windows and `fork` on Unix-like systems.

Downstream report preparation keeps `material_info` as structured facts: `diagnostic_analysis_lib.prepare_diagnostic_analysis()` injects `videoUrl`, `thumbnailUrl`, `videoDuration`, and `materialUri` into each selected material when present. For commercial delivery, `generate_final_report()` reuses each retained original MP4 and does not launch a second FFmpeg pass. It creates a hard link under `final-report/assets/videos/` when the filesystem permits and falls back to an atomic byte copy otherwise; HTML and report data use relative local paths, and `peak-frames/report-video-assets.json` records the source/delivery hash and strategy. Use `python scripts/run_standard_analysis.py finalize --output-dir <run-dir> --analysis-json <analysis-json>` for the default HTML-directory delivery. It copies retained JPEG highlight frames into `final-report/assets/highlight-frames/` and produces `final-report/final-report.html` without building XLSX or ZIP. Add `--include-workbook` only when XLSX is required and `--archive` only when a ZIP is explicitly required. Source deletion is permitted only after an archive was requested and validated. Report video references must not depend on transient platform URLs.

## Wave RPA Batch

Top-N batch mode treats report-derived `top_materials.json` as a rank-ordered candidate pool, not a complete RPA request list. The default candidate limit is three times targetTopN. `rpaConcurrency` is the maximum request count in one RPA wave. The default `waveBarrier=true` scheduler submits candidates from the head as one wave and waits only for the current wave's RPA requests and durable CSV/records artifacts to reach terminal state before submitting the next RPA wave; video download and FFmpeg never run during this collection phase. A candidate becomes selected only when:

- the connector call returns parseable diagnosis records;
- the record list contains the target material's `material_info`;
- `material_info.videoUrl` is a non-empty string.

RPA success writes normalized records before postprocessing starts. The coordinator first collects and checkpoints `targetTopN` accepted records (or exhausts the candidate pool). Only then does the local postprocess phase begin. The default limits are `downloadConcurrency=2` for retained MP4 downloads and `postprocessConcurrency=2` for FFmpeg/frame extraction. Download and FFmpeg may form a local pipeline, but neither overlaps the primary RPA collection phase. A transport failure is recorded as a final failure after the current request and local late-artifact check complete.

Failure reasons:

- `rpa_request_failed`: gateway, HTTP, network, or configured RPA request failure. It becomes `rpaResultStatus: final_failed` after the current request's late-artifact window closes; the coordinator does not submit a second RPA request.
- `rpa_request_timeout`: the single local RPA execution deadline expired. It is immediately `final_failed` and is not resubmitted.

The connector uses asynchronous `task_group_id` submission and status polling. Remote polling is bounded at 3600 seconds, diagnostic CSV download has a 600-second total deadline, and the isolated RPA preflight default is 4260 seconds to cover both budgets plus a 60-second margin. An explicit shorter CLI timeout remains supported for tests or operations and may expire before the remote task completes. On expiry, it terminates the local worker and records `failureReason: rpa_request_timeout`, `rpaTimeoutExceeded: true`, the configured `rpaTimeoutSec` (4260 by default), and `rpaResultStatus: final_failed`. A duration timeout is terminal and never retries or consumes a local-artifact grace budget; it releases its local RPA concurrency slot. Existing manifests may retain legacy `rpaSoftTimeoutExceeded` and `rpaHardTimeoutExceeded` fields for read compatibility, but new runs do not write them.

For `completed` and `partial_success` tasks, the first usable `fileUrl` is downloaded atomically and parsed before the material is accepted. A `partial_success` response with files is persisted together with its safe errors; a payload response still has no CSV and therefore cannot satisfy the verified-CSV contract. If a worker is interrupted after submission, `--resume` uses the saved `task_group_id` to query the same task and 不得重复提交; a receipt without that ID is marked failed and is not resubmitted.

The coordinator submits each material to RPA at most once. It may wait briefly for the CSV from that same request and may poll a saved `task_group_id` during `--resume`, but HTTP 4xx/429/5xx, transport failures, missing CSV, and local timeouts never trigger a new submission. The next full RPA wave starts after every material in the current wave reaches `available` or `final_failed`; it does not wait for video download or FFmpeg. Safe failure diagnostics are retained when available: HTTP status, gateway request ID, a redacted response summary, worker stage, child exit code/signal, result-queue state, and an exception traceback summary.
- `rpa_payload_invalid`: gateway payload could not be normalized into usable records; it is immediately `final_failed` and not retried.
- `missing_material_info`: no matching `material_info` record was returned.
- `missing_video_url`: matching `material_info` exists but has no usable `videoUrl`.
- `unknown_preflight_failure`: unexpected failure outside the known classes.
- `target_satisfied`: a candidate was never submitted because `postprocessSucceededCount >= targetTopN`.

`submitted` alone proves only that the local client started an invocation. It is not evidence of platform failure or success. The local receipt can reconcile a downloaded CSV or persisted records, but cannot retrieve a platform-completed task if the invocation failed before the client received a CSV URL. That requires a platform query API keyed by a client or platform request identifier.

With `waveBarrier=true`, every current-wave RPA request must reach `available` or `final_failed` before the next RPA wave opens. Once the primary collection target is durable, the scheduler switches to local postprocess: downloads are bounded independently from FFmpeg, and a verified retained MP4 is reused when frame extraction retries. A terminal postprocess failure opens a separate rank-ordered backfill cycle: the replacement candidate's RPA must finish and persist before that candidate enters local postprocess. The batch is not ready for downstream facts or reporting until the required postprocess futures have completed successfully and verification passes.

The wave scheduler writes:

- `batch-manifest.json`: schemaVersion 2 record with `targetTopN`, `candidateCount`, `candidateLimit`, `rpaConcurrency`, `downloadConcurrency`, `postprocessConcurrency` (FFmpeg/frame extraction), `waveBarrier`, `submittedMaterials`, `selectedMaterials`, `rejectedMaterials`, and `cancelledMaterials`. Every submitted candidate records rank, material metadata, RPA state, `rpaResultStatus`, `errorType`/`errorMessage`, postprocess state, failure reason, paths, timings, `waveIndex`, `waveSize`, `waveStartedAtSec`, and `waveFinishedAtSec`. Wave timestamps now measure RPA collection only.
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

Local repair is available with `python scripts/repair_manifest.py --output-dir <peak-frames-dir>`. It validates `rpa-receipts/<materialId>.json` in `records_persisted` state, then reconciles CSV -> records and valid summary/JPEG -> manifest without calling RPA. With `--records-only`, no download or FFmpeg work is allowed. Otherwise `records -> 后处理` requires usable records/video URLs for `targetTopN` distinct materials; an unknown target or incomplete collection blocks postprocess with exit code 5 and `postprocessBlockedReason=rpa_collection_incomplete`. Candidate exhaustion in the batch engine also stops before processing a partial initial collection.

Standard `run_standard_analysis.py resume` performs exactly one records-only reconciliation, then lets the shared batch engine resume saved remote IDs, collect any missing candidates, and process the completed collection. Previously submitted materials are never resubmitted; unused candidates may be submitted to fill the target. Standalone repair retains its bounded defaults (download 2, FFmpeg 2, at most 2 materials per invocation); only an unblocked invocation with actual progress and a positive `postprocessRemainingCount` warrants another local repair call. Download completion and each material completion checkpoint the manifest. Valid summary/JPEG artifacts are reused, including canonical paths recovered from older duplicated run prefixes. `postprocessAttemptCount` is the non-negative scheduling count and `postprocessAttempts` is always a list; a legacy integer is migrated into the count. A valid receipt overrides a stale manifest and restores `rpaStatus: selected` and `rpaResultStatus: available`. If the manifest is missing, pass `--top-materials-json`; artifact-only recovery uses `rpaStatus: unknown` until records prove selection. The repair receipt records `rpaCalls: 0`, `recordsOnly`, the blocked reason, actions, errors, timings, and stage counts.

Counting terms are intentionally separate. `assetSelectedMaterialCount` is the legacy field name for the number of rank-ordered material IDs loaded from the report candidate pool for this run, equivalent to `candidateCount` and capped by `candidateLimit`. `rpaSubmittedMaterialCount` is the number of material IDs actually sent to the RPA connector, equivalent to the length of `submittedMaterials`. Batch completion requires `postprocessSucceededCount >= targetTopN`; the scheduler never submits the entire candidate pool up front, and submits from the candidate head by waves until that final-success condition is met or the candidate pool is exhausted. Wave timing measures RPA boundaries only and must not be used as an end-to-end or postprocess timing.

Selected and ready-list ordering always follows source candidate rank, even when lower-ranked RPA jobs finish first. Once the final-success condition is met the scheduler stops submitting new RPA work and unsubmitted candidates are cancelled. 后处理失败 remains selected with `postprocessStatus: failed`, does not become an RPA rejection, receives one local retry using retained records, and then triggers rank-ordered 补位 after its second failure. A batch is nonzero only when the candidate pool is exhausted before `postprocessSucceededCount >= targetTopN`.

## Historical Business Semantics Reference

The following names describe the current business intent only. They are not an authoritative runtime schema; always use `rpa_dynamic_client.py schema` and validate the complete JSON returned for the current connector deployment.

Supported `date_type` values for new requests:

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
