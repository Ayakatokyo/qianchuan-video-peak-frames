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

## Gateway Result Handling

The connector documentation says successful gateway responses may be `payload` or `files`.

- Direct list responses are treated as connector records.
- `result_type=payload` must contain a record list in `payload`.
- JSON-string payloads are decoded before processing.
- `result_type=files` is rejected because this frame workflow needs structured records and `material_info.videoUrl`.
