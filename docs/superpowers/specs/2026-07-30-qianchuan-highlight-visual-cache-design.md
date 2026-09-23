# Qianchuan Highlight Visual Cache Design

## Goal

Avoid repeated AI vision work for unchanged high-frame JPEGs. The cache stores deterministic, reviewable visual observations keyed by image content and evidence identity, then lets later AI second-pass runs reuse prior observations while still producing fresh strategic conclusions.

## Current Evidence

The top18 fullflow contains 68 high-frame JPEGs. The prompt requires the model to actually inspect every JPEG and produce one `highlightAnalyses` item per frame. When rerunning because of prompt wording, HTML template changes, or non-visual data changes, the same 68 images are inspected again even though their pixels did not change.

## Scope

In scope:

- Cache per-frame visual observations and optional commercial interpretation drafts.
- Key cache entries by image SHA256 plus `evidenceRef`.
- Include cache metadata in the diagnostic analysis workspace.
- Preserve the rule that uncached images must be visually inspected.

Out of scope:

- Replacing final AI strategic synthesis with cached text.
- Trusting cached observations when image bytes change.
- Storing signed video URLs, tokens, or external credentials.
- Calling a separate vision model from deterministic Python code.

## Cache Artifact

Store a JSON file at:

`diagnostic-analysis/highlight-visual-cache.json`

Suggested shape:

```json
{
  "schemaVersion": 1,
  "entries": [
    {
      "cacheKey": "sha256:<image_hash>|highlight:<material_id>:<rank_type>",
      "imageSha256": "<image_hash>",
      "evidenceRef": "highlight:<material_id>:first_peak",
      "framePath": "/absolute/path/to/frame.jpg",
      "materialId": "<material_id>",
      "rankType": "first_peak",
      "durationSec": 2,
      "metricValue": 41,
      "visualObservation": "画面中可见产品/人物/字幕等客观元素。",
      "commercialInterpretationDraft": "该画面可能降低理解成本或强化信任感。",
      "createdAt": "2026-07-30T00:00:00Z",
      "sourcePromptHash": "<prompt_hash>"
    }
  ]
}
```

The cache is a local artifact. It should not be embedded in the final HTML unless a future report explicitly needs audit traces.

## Workflow

1. `prepare_diagnostic_analysis.py` computes SHA256 for each high-frame JPEG and emits `imageSha256` in `highlights.materials[*].frames[*]`.
2. Before AI analysis, the operator or model checks `highlight-visual-cache.json`.
3. For a cache hit, the model may reuse `visualObservation` as a factual observation and update only the strategy-level interpretation if needed.
4. For a cache miss, the model must inspect the JPEG and write a new cache entry after producing `diagnostic-ai-analysis.json`.
5. Final report rendering continues to consume `diagnostic-ai-analysis.json`; it does not need to understand the cache.

## Invalidation Rules

Invalidate a frame cache entry when any of the following changes:

- image SHA256 changes.
- `evidenceRef` changes.
- `rankType` changes.
- the previous entry lacks `visualObservation`.

Do not invalidate solely because:

- prompt wording changes.
- HTML template changes.
- final-report renderer changes.
- non-visual audience or delivery facts change.

Prompt changes may affect `commercialInterpretationDraft`, but the objective `visualObservation` remains reusable if the image hash is stable.

## Safety Rules

- Cache only visual observations that can be verified from the image.
- Do not cache signed URLs, cookies, tokens, or raw RPA response payloads.
- Do not use cache entries for different images even if filenames match.
- If cache and current JPEG conflict, current JPEG wins and the entry is rewritten.

## Implementation Plan

1. Add `imageSha256` to high-frame facts in `scripts/diagnostic_analysis_lib.py`.
2. Add tests proving the hash changes when image bytes change and stays stable when only path changes.
3. Add a helper script, for example `scripts/update_highlight_visual_cache.py`, that:
   - reads `diagnostic-analysis-data.json`.
   - reads an existing cache file if present.
   - reports hits and misses.
   - optionally merges AI-produced observations into cache after validation.
4. Update `prompts/diagnostic-analysis-prompt-v1.md`:
   - allow reuse of cached `visualObservation` for exact hash hits.
   - require direct image inspection for misses.
   - require one `highlightAnalyses` item per current frame regardless of cache status.
5. Add tests for cache merge validation:
   - rejects unknown `evidenceRef`.
   - rejects hash mismatch.
   - rejects empty observation.
   - preserves existing valid entries.

## Risks

- A stale or low-quality observation can propagate if cache review is weak.
- Cache introduces a second artifact that operators must understand.
- Prompt wording must remain strict so the model does not treat cache hits as permission to skip all image reasoning.

## Acceptance Criteria

- On a rerun with unchanged 68 JPEGs, all 68 frames report cache hits.
- AI analysis still outputs complete `highlightAnalyses` coverage.
- A changed JPEG byte stream forces a cache miss.
- No final HTML or AI JSON field contains local cache metadata unless explicitly designed later.
