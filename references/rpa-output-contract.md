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

This example selects second 1 first, then seconds 3, 7, and 5 by later click rank.

## Online Video Fields

Online mode reads the `material_info` record field `videoUrl`, downloads it to a temporary local video file, and then reuses the same local processing pipeline. `videoDuration` may be present in the connector output, but FFmpeg remains the source of truth for frame timestamp bounds.

Downstream report preparation keeps `material_info` as structured facts: `prepare_diagnostic_analysis.py` injects `videoUrl`, `thumbnailUrl`, `videoDuration`, and `materialUri` into each selected material when present. Final HTML rendering may expose `videoUrl` through an external `<video>` preview with `preload="none"`, because 巨量千川 URLs are transient signed addresses and should not auto-download during report open. Use `render_final_report.py --copy-assets` for commercial delivery: retained JPEG high-light frames are copied into `final-report/assets/highlight-frames/` and referenced by relative paths, so the HTML avoids a large base64 data island. Use `--embed-assets` only for small one-file debug reports. video bytes must never be embedded into the HTML.

## Streaming RPA Batch

Top-N batch mode treats asset-table `top_materials.json` as a rank-ordered candidate pool, not a complete RPA request list. The default candidate limit is three times targetTopN. The scheduler submits candidates from the head with at most three RPA jobs in flight; a candidate becomes selected only when:

- the connector call returns parseable diagnosis records;
- the record list contains the target material's `material_info`;
- `material_info.videoUrl` is a non-empty string.

RPA success writes normalized records before postprocessing starts. Video download, FFmpeg extraction, and summary generation immediately follow selection on a separate bounded channel. RPA failure means "RPA currently unavailable for this material"; it is not a deletion verdict.

Failure reasons:

- `rpa_request_failed`: gateway, HTTP, network, or configured RPA request failure.
- `rpa_payload_invalid`: gateway payload could not be normalized into usable records.
- `missing_material_info`: no matching `material_info` record was returned.
- `missing_video_url`: matching `material_info` exists but has no usable `videoUrl`.
- `unknown_preflight_failure`: unexpected failure outside the known classes.
- `top_n_already_selected`: an already-submitted RPA success completed after targetTopN was selected; it is not postprocessed.
- `target_satisfied`: a candidate was never submitted because targetTopN was already selected.

The stream writes:

- `batch-manifest.json`: schemaVersion 2 record with `targetTopN`, `candidateCount`, `candidateLimit`, `rpaConcurrency`, `postprocessConcurrency`, `submittedMaterials`, `selectedMaterials`, `rejectedMaterials`, and `cancelledMaterials`. Every candidate records rank, material metadata, RPA state, postprocess state, failure reason, paths, and timings.
- `top_materials_rpa_ready.json`: compatibility top-materials shape containing rank-ordered selected materials only.
- `diagnosis-records/<materialId>.json`: normalized records written before a candidate joins selected.
- `diagnosis-csv/<materialId>.csv`: retained source CSV for a files-envelope response.

Counting terms are intentionally separate. `assetSelectedMaterialCount` is the number of rank-ordered material IDs loaded from the asset-table candidate pool for this run, equivalent to `candidateCount` and capped by `candidateLimit`. `rpaSubmittedMaterialCount` is the number of material IDs actually sent to the RPA connector, equivalent to the length of `submittedMaterials`. The scheduler never submits the entire candidate pool up front; it submits from the candidate head until `targetTopN` selected materials exist or the candidate pool is exhausted.

Selected and ready-list ordering always follows source candidate rank, even when lower-ranked RPA jobs finish first. Once Top N is selected the scheduler stops submitting new RPA work; unsubmitted candidates are cancelled and successful already-submitted extras are rejected. A postprocess failure remains selected with `postprocessStatus: failed`, makes the batch nonzero, and does not become an RPA rejection or trigger replacement.

`--preflight-only`, `--rpa-preflight-json`, and `--allow-rpa-refetch` remain compatibility and diagnostic tools for old runs. They are not part of the recommended streaming Top-N command.

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
