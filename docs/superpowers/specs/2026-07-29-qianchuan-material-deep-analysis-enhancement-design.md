# Qianchuan Material Deep Analysis Enhancement Design

## Goal

Enhance `04｜单素材深度分析` so each selected material shows both deterministic RPA facts and the existing AI second-pass summary. The section should move closer to the Qianchuan official material-analysis layout by adding interaction timeline analysis and script-analysis facts, while keeping the final HTML report lightweight and responsive.

The report must not inline raw video files into HTML. Video playback is optional and only used when a usable external or local video URL is available.

## Current Context

The current final report keeps five sections:

1. `01｜TOP 素材组合结论`
2. `02｜人群洞察`
3. `03｜脚本洞察`
4. `04｜单素材深度分析`
5. `05｜下一轮策略`

`04｜单素材深度分析` currently renders:

- material rank, name, and extraction status;
- high-light frame images with second and click count;
- AI-produced `audienceConclusion`, `scriptConclusion`, `highlightAnalyses`, `integratedConclusion`, and `nextAction`.

The fullflow run already has enough raw facts for the enhancement:

- 18 materials have `script_analysis` records;
- 18 materials have `interaction_series` records;
- interaction metrics cover `CLICK_COUNT`, `LOSE_COUNT`, `LIKE_COUNT`, `COMMENT_COUNT`, `SHARE_COUNT`, and `NEW_FOLLOWER_COUNT`;
- the sampled fullflow contains 5406 interaction-series points, which is small enough for the existing JSON-in-HTML report model.

The Qianchuan official HTML snippet uses a remote `<video src="https://...mp4">`; it does not base64-inline the video. The current run does not retain local video files.

## Scope

In scope:

- Add material-level interaction timeline facts to the final report data.
- Add material-level script analysis facts to the final report data.
- Render a new interaction timeline block inside each material panel.
- Render a new script analysis block inside each material panel.
- Keep the current AI second-pass summary blocks visible.
- Add optional video metadata support without requiring video files.
- Keep high-light frames as the default visual evidence when video is unavailable.
- Add tests for data extraction, template contract, visible structure, and performance-sensitive rendering behavior.

Out of scope:

- Downloading, copying, transcoding, or embedding video files.
- Base64-inlining video into `final-report.html`.
- Calling Qianchuan, RPA, FFmpeg, or external APIs during final report rendering.
- Reintroducing the removed batch-level high-light frame insight section.
- Changing AI generation beyond clarifying that material-level AI summaries should complement, not duplicate, raw timeline and script facts.

## Recommended Approach

Use a lightweight data-first layout:

1. High-light frame strip remains the visual anchor.
2. Add `互动时序分析` as a compact chart area with metric tabs.
3. Add `脚本分析数据` as deterministic facts: content formula, labels, evidence sentences, and collapsible transcript.
4. Keep `AI 二次总结` as interpretation after the facts.
5. Add optional video preview only when a `videoSrc` exists; render it with `preload="metadata"` and never inline media bytes.

This is preferable to embedding videos because the report is intended to be portable and auditable. A typical mp4 would dominate report weight, and base64 encoding would further inflate it by about one third while increasing browser parse and memory pressure. Referenced media with metadata preload avoids that cost.

## Data Contract

Extend each material in `final-report-data.json` with optional fields:

```json
{
  "interactionTimeline": {
    "metrics": [
      {
        "metric": "CLICK_COUNT",
        "label": "整体点击次数",
        "points": [{"second": 0, "value": 16}],
        "peak": {"second": 7, "value": 50}
      }
    ]
  },
  "scriptAnalysis": {
    "materialUri": "v28033gi0000d9c98mfog65sha80p5v0",
    "formula": [
      {"level1": "商品卖点", "level2": "适用场景"}
    ],
    "labels": [
      {"level1": "商品卖点", "level2": "适用场景"}
    ],
    "evidence": [
      {"level2": "适用场景", "sentence": "早餐可以不碰碳水..."}
    ],
    "text": "完整脚本文案"
  },
  "video": {
    "src": "",
    "poster": "",
    "durationSec": null
  }
}
```

Rules:

- `interactionTimeline` is derived only from `interaction_series` records for the same `materialId`.
- Metric labels are localized in the renderer or data builder:
  - `CLICK_COUNT`: `整体点击次数`
  - `LOSE_COUNT`: `整体流失数`
  - `LIKE_COUNT`: `整体点赞次数`
  - `COMMENT_COUNT`: `整体评论次数`
  - `SHARE_COUNT`: `整体转发次数`
  - `NEW_FOLLOWER_COUNT`: `整体新增粉丝数`
- Points are sorted by `second` ascending.
- Peak is the maximum-value point for that metric; ties use the earliest second.
- `scriptAnalysis` is derived from the existing parsed script facts in `scripts.materials`.
- `video.src` is empty unless a trusted source is available from retained local data. Empty video metadata must not create a broken player.

## HTML Layout

Inside each material panel, use this order:

1. Material header
   - rank, material name, Chinese extraction status.
2. Visual evidence
   - high-light frame grid;
   - optional video preview only when `video.src` is present.
3. `互动时序分析`
   - metric tabs for the six supported metrics;
   - one lightweight SVG or canvas chart for the selected metric;
   - a short peak note such as `整体点击次数峰值：7s，50 次`;
   - no AI conclusion text in this card.
4. `脚本分析数据`
   - content formula rendered as label chips;
   - top labels rendered as compact chips;
   - evidence sentences grouped by secondary label;
   - transcript shown in a collapsed or height-limited block to avoid making the panel too long.
5. Existing AI second-pass summary
   - `人群与脚本证据`;
   - `高光帧观察`;
   - `综合判断与下一步`.

The facts and AI blocks must use different visual hierarchy. Raw data cards should show numbers, labels, seconds, and evidence text. AI cards should read as interpretation and action guidance. This avoids repeating the same sentence in both places.

## Performance Rules

- Do not embed video bytes in `final-report.html` or `final-report-data.json`.
- If video is later supported, render `<video controls preload="metadata">`.
- Render only the active material panel's chart in full; inactive panels can keep data in JSON and draw on tab activation.
- Keep transcript blocks collapsed or height-limited.
- Continue using existing high-light frame images by reference rather than copying image bytes into HTML.
- The expected interaction data size is acceptable: 18 materials times 6 metrics times roughly 49 points is small relative to the existing report data.

## AI Boundary

The AI second-pass analysis should not be responsible for producing the raw interaction timeline or script facts. Those are deterministic facts from diagnosis records.

The AI output remains responsible for:

- material-level interpretation;
- how audience, script, and high-light evidence combine commercially;
- next action for each material;
- batch-level next-round strategy.

Prompt wording should make clear that the material AI summary must complement the raw cards. For example, it should explain why a peak or script pattern matters, not restate every visible metric and label.

## Failure Handling

- If a material lacks `interaction_series`, show `暂无互动时序数据` in the interaction block.
- If a material lacks `script_analysis`, show `暂无脚本分析数据` in the script block.
- If a metric has no points, hide that metric tab.
- If every metric is missing, show one empty-state message rather than rendering an empty chart.
- If `video.src` is missing, do not render a video element.
- Existing AI summaries should still render when raw timeline or script facts are absent.

## Implementation Units

The later implementation should keep responsibilities separated:

- `scripts/final_report_lib.py`
  - extract and normalize per-material interaction timelines;
  - attach per-material script analysis facts;
  - pass optional video metadata through only when trusted;
  - keep template data compact.
- `templates/final-report-template.html`
  - render interaction metric tabs and chart;
  - render script analysis facts;
  - keep existing AI summary cards;
  - lazy-render charts for active material tabs.
- `tests/test_final_report.py`
  - cover data extraction, rendering, empty states, no priority labels, and no raw video inlining.
- `prompts/diagnostic-analysis-prompt-v1.md`
  - clarify the AI boundary so generated material summaries complement visible raw facts.

## Verification

Add or update tests to verify:

- `interactionTimeline.metrics` is created from `interaction_series` records.
- Metric labels are Chinese and include click, loss, like, comment, share, and new follower metrics when available.
- Peaks are calculated from the maximum metric value with earliest-second tie breaking.
- `scriptAnalysis` includes labels, evidence, formula when available, and transcript text.
- The rendered HTML includes `互动时序分析` and `脚本分析数据` inside `04｜单素材深度分析`.
- Missing timeline or script facts render clean empty states.
- `final-report.html` does not contain base64 video payloads.
- Video elements are absent when no video source exists.
- Existing AI summary text still appears for each material.
- `python -m unittest tests.test_final_report -v` remains green.

## Self-Review

This design is scoped to one report section and does not require changing RPA extraction, peak-frame selection, or external AI execution. It resolves the video-size concern by treating video playback as optional referenced media, while using already retained interaction and script data for the new real-data display. The raw fact cards and AI summary cards have separate responsibilities, reducing duplication in the final report.
