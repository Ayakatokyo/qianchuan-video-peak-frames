# Qianchuan Final HTML Report Design

## Goal

Complete the business loop after TOP-material RPA diagnosis and peak-frame extraction by producing a polished, auditable HTML report. The report combines selected TOP materials, retained high-light frames, audience facts, script facts, delivery facts, and externally supplied business conclusions.

The report generator must not call RPA, download videos, invoke external APIs, or create business conclusions by itself. It only reads local run artifacts and injects the supplied structured conclusion data into fixed report sections.

## Scope

In scope:

- Add a final report rendering layer that produces `final-report-data.json` and `final-report.html`.
- Reuse existing retained artifacts:
  - `top_materials.json`
  - `diagnosis-records/<materialId>.json`
  - `peak-frames/batch-manifest.json`
  - `peak-frames/batch-verification.json`
  - `peak-frames/<materialId>/summary.json`
  - `peak-frames/<materialId>/*.jpg`
- Reuse the existing diagnostic fact processing for audience, script, and delivery facts.
- Accept `diagnostic-ai-analysis.json` as the only source of injected business conclusions.
- Generate a draft report when conclusion data is absent.

Out of scope:

- Calling any model or external API.
- Changing RPA request parameters, peak selection rules, video download, or FFmpeg extraction.
- Inferring missing business conclusions inside the HTML renderer.
- Removing the existing commercial report generator during this change.

## Run Directory Contract

The standard run directory is:

```text
/run/
  top_materials.json
  diagnosis-records/<materialId>.json
  diagnosis-csv/<materialId>.csv
  peak-frames/
    batch-manifest.json
    batch-verification.json
    <materialId>/summary.json
    <materialId>/*.jpg
  diagnostic-analysis/
    diagnostic-analysis-data.json
    diagnostic-analysis-prompt.md
    diagnostic-ai-analysis.json
  final-report/
    final-report-data.json
    final-report.html
```

The final report renderer reads local files only. It must tolerate partial material-level gaps and preserve warnings in the final data package.

## External Conclusion Contract

`diagnostic-ai-analysis.json` is optional for draft rendering. When present, it must be a JSON object with `schemaVersion: 1`.

Minimum supported shape:

```json
{
  "schemaVersion": 1,
  "overallConclusion": "整体结论",
  "keyInsights": [
    {"title": "洞察标题", "body": "洞察说明", "materialIds": ["764..."]}
  ],
  "materialRecommendations": [
    {
      "materialId": "764...",
      "decision": "scale|remix|hold|stop|unknown",
      "reason": "判断理由",
      "nextAction": "下一步动作"
    }
  ],
  "nextActions": [
    {"title": "动作", "body": "说明", "priority": "high|medium|low"}
  ],
  "warnings": []
}
```

The renderer may display conclusion entries without `materialIds` as portfolio-level conclusions. It must not synthesize missing conclusions, decisions, reasons, or next actions.

## Output Contract

The renderer writes:

- `final-report-data.json`: a merged, auditable report data package.
- `final-report.html`: a standalone HTML report.

`final-report-data.json` contains:

- report status: `complete`, `partial`, or `draft`;
- selected materials and ranking metadata;
- high-light frame metadata and local frame paths;
- audience metrics for `SHOW_COUNT`, `CLICK_COUNT`, and `TRANSACTION_AMOUNT`;
- script text, labels, evidence, and label summary;
- delivery material detail facts;
- injected conclusion data;
- batch verification errors and warnings;
- renderer warnings.

## HTML Structure

The HTML visual direction follows the provided simulation report: warm dark hero, cream report panels, metric cards, material cards, high-light frame grids, and conclusion cards. The implementation should extract a lightweight template rather than depending on the large simulation HTML file.

Sections:

1. Hero overview
   - title, period, selected material count, successful frame material count, high-light frame count, total cost, warning count;
   - one representative high-light frame as cover when available;
   - clear status label for `complete`, `partial`, or `draft`.
2. Portfolio conclusion
   - fixed display area for `overallConclusion`;
   - draft placeholder when missing.
3. Key insights
   - cards from `keyInsights`;
   - related material IDs shown as evidence links or labels.
4. Material evidence cards
   - one card per selected material, including failed materials;
   - rank, name, cost, extraction status, high-light frame grid, audience highlights, script labels, delivery facts, and material recommendation.
5. Next-round strategy
   - `nextActions`, sorted by `high`, `medium`, then `low`.
6. Data quality and methodology
   - batch verification errors and warnings;
   - conclusion warnings;
   - missing data warnings;
   - statement that audience and script data are material-level correlations, not frame-level causality.

## Partial Delivery Rules

The renderer should still generate HTML when:

- some materials lack high-light frames;
- some materials lack diagnosis records;
- some materials lack conclusion recommendations;
- audience, script, or delivery modules are empty for some materials;
- batch verification has warnings or known material-level failures.

The renderer should block with an actionable error when:

- `top_materials.json` is missing, unreadable, or contains no usable materials;
- output cannot be written;
- a supplied `diagnostic-ai-analysis.json` is invalid JSON, has an unsupported schema, or violates the required field types.

Report status:

- `complete`: all selected materials have valid high-light summaries and batch verification has no errors.
- `partial`: at least one selected material is missing required evidence or batch verification has errors, while at least one material has usable evidence.
- `draft`: conclusion data is absent; evidence can still be shown.

Failed materials are never silently removed. Their cards must show rank, cost, material name, failure reason when available, and recommended manual follow-up.

## CLI Shape

Add:

```bash
python scripts/render_final_report.py \
  --top-materials-json "/path/to/run/top_materials.json" \
  --peak-output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --analysis-json "/path/to/run/diagnostic-analysis/diagnostic-ai-analysis.json" \
  --output-dir "/path/to/run/final-report"
```

`--analysis-json` is optional. If omitted, the renderer creates a `draft` report. If supplied and invalid, rendering fails.

## Skill Workflow Update

`SKILL.md` should document the complete post-RPA business loop:

1. export TOP materials;
2. run RPA peak-frame extraction with retained diagnosis records;
3. verify retained extraction artifacts;
4. prepare diagnostic analysis facts and prompt;
5. obtain or place `diagnostic-ai-analysis.json` from external analysis;
6. render the final HTML report.

The skill must state that final report rendering does not call external APIs and only injects local structured conclusions.

## Testing

Add focused tests for:

- conclusion JSON schema acceptance and rejection;
- draft report generation without conclusion JSON;
- partial report generation when one material lacks high-light frames;
- failed materials remaining visible in final report data and HTML;
- final HTML containing hero overview, portfolio conclusion, key insights, material cards, next actions, and data quality sections;
- `SKILL.md` documenting the final report loop and no-external-API boundary.

Existing peak extraction, RPA CSV, TOP-material selection, diagnostic analysis, and commercial report tests remain regression coverage.

## Self-Review

This design keeps business conclusion generation outside the renderer, so the skill can close the reporting loop without introducing external API dependencies. It creates a new final report layer rather than stretching the older shallow commercial report generator, while preserving the older CLI for compatibility.
