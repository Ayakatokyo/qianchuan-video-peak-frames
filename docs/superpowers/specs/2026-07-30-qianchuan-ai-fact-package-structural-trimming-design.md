# Qianchuan AI Fact Package Structural Trimming Design

## Goal

Reduce AI second-pass input size without reducing visible report richness. The optimization targets the deterministic `diagnostic-analysis-data.json` fact package, especially the largest `audience` and `delivery` sections.

## Current Evidence

The 2026-07-28 top18 fullflow fact package is about 1.87 MB in pretty JSON form. Compact serialization lowers this to about 1.05 MB, but the remaining dominant sections are still:

- `audience.metrics`: about 619 KB compact, split almost evenly across `SHOW_COUNT`, `CLICK_COUNT`, and `TRANSACTION_AMOUNT`.
- `delivery.materials`: about 340 KB compact for 18 materials.
- `scripts` and `highlights` are much smaller and should not be the first trimming target.

## Scope

In scope:

- Add a new schema version for trimmed AI input, likely `schemaVersion: 2`.
- Keep the final report renderer and `diagnostic-ai-analysis.json` output contract unchanged.
- Preserve all evidence refs required by the existing AI prompt and final report validation.
- Keep raw high-frame JPEG review requirements unchanged.

Out of scope:

- Removing material-level AI summaries.
- Caching visual observations.
- Changing the HTML report section structure.
- Calling any external model or API from the deterministic renderer.

## Proposed Data Shape

### Audience

For each metric and dimension, keep:

- `topBuckets`: top 8 by metric value by default.
- `tailSummary`: bucket count, total metric value, total percentage, and representative excluded bucket names.
- `missingMaterialIds`.
- Existing dimension labels and metric names.

This preserves the strongest signal while preventing long bucket lists from dominating the prompt. City and province can use a larger cap, such as province top 12 and city top 20, because regional long tail can be commercially meaningful.

### Delivery

Replace free-form `values` dumps with a curated whitelist:

- spend/cost fields.
- click, show, conversion, transaction and GMV fields.
- ROI and rate fields.
- material status fields only if they are business-facing, not process metadata.

For each material, keep the latest or aggregate rows needed for analysis. If multiple `statDate` rows exist, emit both:

- `latest`: the latest date row.
- `periodSummary`: numeric sums for additive metrics and weighted or direct values for rates only when a safe denominator exists. If a rate cannot be safely aggregated, keep `latest` only and mark `aggregationStatus: "latest_only"`.

### Scripts

Keep current script text and label summary, but cap `evidence` sentences per label to 3 deterministic examples. The final report still renders raw script facts from report data; the AI input only needs enough evidence to reason.

## Evidence Ref Compatibility

The AI output currently cites `audience:*`, `script:*`, and `highlight:*` evidence refs. Trimming must not remove the ability to cite:

- top audience buckets that remain in `topBuckets`.
- script labels that remain in `labelSummary`.
- every high-frame `evidenceRef`.

If an excluded tail bucket matters, it cannot be cited directly; the AI should cite the corresponding dimension-level top bucket or state that the long tail is summarized. This is acceptable because the final report should focus on major commercial signals.

## Implementation Plan

1. Add tests for trimmed audience dimensions:
   - more than 8 age or user-group buckets are reduced to `topBuckets` plus `tailSummary`.
   - city and province use their larger caps.
   - percentages still sum to a defensible total when combining top and tail.
2. Add tests for delivery whitelist:
   - URL, raw nested fields, and process fields stay excluded.
   - known business fields are retained.
   - latest row and additive period summary are deterministic.
3. Implement helper functions in `scripts/diagnostic_analysis_lib.py`:
   - `_trim_audience_metric(metric_payload, dimension_caps)`.
   - `_curate_delivery_facts(material_facts)`.
   - `_cap_script_evidence(scripts_payload, limit=3)`.
4. Bump `diagnostic-analysis-data.json` `schemaVersion` only if the public structure changes. Keep tests that render final reports unchanged because final report data is built from original retained records and AI JSON.
5. Update `prompts/diagnostic-analysis-prompt-v1.md` to explain `topBuckets` and `tailSummary`.
6. Re-run real top18 fullflow preparation and compare byte size against the compact baseline.

## Risks

- Over-trimming regional or audience tail data can hide niche conversion pockets.
- Delivery metric names may vary across RPA exports; an overly narrow whitelist may drop useful fields.
- A schema bump requires prompt and tests to stay aligned.

## Acceptance Criteria

- Compact + trimmed `diagnostic-analysis-data.json` is at least 30% smaller than the compact-only baseline on the top18 fullflow.
- Existing AI output schema remains `diagnostic-ai-analysis.json` schema v2.
- Final HTML report still renders all sections and all AI secondary insights.
- Unit tests prove deterministic trimming and no URL/process metadata leakage.
