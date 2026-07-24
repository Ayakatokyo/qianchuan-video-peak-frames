# Qianchuan Diagnostic Analysis Data Design

## Goal

Transform retained TOP-material RPA diagnostic records into a deterministic, auditable fact package and an editable AI analysis prompt. The processing step must not call RPA, download video, invoke a model, or render the existing commercial report.

## Scope

The new data-processing command consumes a run-local `top_materials.json` and `diagnosis-records/<materialId>.json` files. It produces one analysis data JSON and one prompt Markdown file. A later AI conversation reads those two artifacts and writes a separate analysis result JSON. The existing report generator remains unchanged during this work.

## Inputs

- `top_materials.json`: selected material IDs, ranks, names, types, period, and selection metadata.
- `diagnosis-records/<materialId>.json`: normalized complete RPA records retained by the extraction workflow.
- Optional prompt template path: a user-maintained Markdown file that replaces the packaged default template.

Every selected material is represented in the output even when its diagnostic record is missing or malformed. The output records a warning rather than silently dropping it.

## Processing

### Audience facts

Read only `crowd_analysis` records for the selected material. Group independently by the three source metrics:

- `SHOW_COUNT` (overall impressions)
- `CLICK_COUNT` (overall clicks)
- `TRANSACTION_AMOUNT` (overall transaction amount)

Within each metric, group by `dimensionKey` and audience bucket. Use `metricValue` as the additive value. Recalculate bucket percentage from the summed bucket values within the aggregated dimension; never sum source-row `metricPercentage` values. Preserve the source material IDs for every aggregated bucket and warn about materials that have no usable data for a metric.

### Script facts

Read one `script_analysis` record per material. Parse the string-valued `scriptInfo` with a safe structured-data parser, accepting the observed fields:

- `text`: complete script or spoken copy
- `data`: primary and secondary labels
- `detail`: secondary-label evidence sentences
- `formula`: source label references

Store the complete per-material script fact, its labels, and evidence. Aggregate labels across the TOP set by occurrence count, covered material IDs, and deduplicated evidence sentences. Script facts are tied to `materialId` and `materialUri`, not the queried date range; their reuse is valid only when those identifiers match.

### Delivery facts

Read `delivery_material_detail` records as per-day, per-material context. Preserve source values and normalize numbers only when an unambiguous finite value can be parsed. Do not aggregate ratios such as ROI or click rate by arithmetic mean; retain them as material/day facts for the AI to interpret with their numerator metrics.

## Output Contract

The processor writes atomically to the selected output directory:

- `diagnostic-analysis-data.json`
- `diagnostic-analysis-prompt.md`

The data JSON has a versioned top-level contract:

```json
{
  "schemaVersion": 1,
  "period": {"actualStartDate": "YYYY-MM-DD", "actualEndDate": "YYYY-MM-DD"},
  "materials": [],
  "audience": {
    "metrics": {
      "SHOW_COUNT": {"dimensions": {}, "missingMaterialIds": []},
      "CLICK_COUNT": {"dimensions": {}, "missingMaterialIds": []},
      "TRANSACTION_AMOUNT": {"dimensions": {}, "missingMaterialIds": []}
    }
  },
  "scripts": {"materials": [], "labelSummary": []},
  "delivery": {"materials": []},
  "warnings": []
}
```

The prompt directs the AI to use only the fact package, distinguish correlation from causation, separate the three audience metrics, and cite material IDs and source values for conclusions. It instructs the AI to emit a separate `diagnostic-ai-analysis.json`; that output is outside the processor's ownership.

## Prompt Template Policy

The packaged default prompt is a versioned Markdown template. The CLI accepts `--prompt-template <path>` to copy a replacement template into the run output. The data JSON schema is independent of prompt wording. A template change therefore needs no processor code change or data migration.

## Failure and Privacy

- Invalid or missing record files create material-scoped warnings; the processor still emits a complete data package.
- An invalid `scriptInfo` creates a script warning and does not discard usable audience or delivery facts.
- The processor never persists credentials, signed diagnostic CSV URLs, or video URLs in its new artifacts.
- It does not mutate raw diagnosis records, peak-frame artifacts, manifests, or report outputs.

## Verification

Tests will cover all three audience metrics, recomputed percentages, missing-material warnings, script text/labels/evidence parsing, safe rejection of malformed script data, delivery-value normalization, prompt-template override, and atomic output publication.
