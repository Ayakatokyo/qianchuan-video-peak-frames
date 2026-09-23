# Qianchuan Skill Showcase Design

## Goal

Create a lightweight, static showcase for the `千川短视频素材分析` skill and include a desensitized example of the skill's final HTML report output.

## Approved Direction

Use an operator-console style presentation. The page should explain the skill in business delivery terms: what inputs it needs, how the end-to-end workflow runs, what artifacts are auditable, and what a final report looks like.

## Scope

Create:

- `showcase/index.html`: skill introduction page.
- `showcase/desensitized-final-report.html`: anonymized final report sample.

Do not modify the skill runtime, Python scripts, report renderer, templates, prompts, or tests.

## Content Requirements

The introduction page must cover:

- Skill name and one-sentence value proposition.
- Suitable use cases for Qianchuan short-video delivery review.
- Required inputs: analysis period, Top material count, advertiser id, RPA shop id and environment credentials when running online.
- Workflow: asset-table Top candidate export, streaming RPA diagnosis, click-peak frame extraction, batch verification, diagnostic fact pack, in-task visual AI analysis, final HTML report render.
- Key contracts: T+2 recent-date conversion, target Top N versus candidate pool size, `top_materials_rpa_ready.json` as final report input, no process narrative in business conclusions.
- Deliverables: retained JSON, CSV, JPEG frames, summaries, diagnostic fact pack, AI analysis JSON, final report data and HTML.
- A link to the desensitized final report.

The desensitized final report must include the six business sections used by the skill:

1. `01｜TOP 素材组合结论`
2. `02｜人群洞察`
3. `03｜脚本洞察`
4. `04｜高光帧洞察`
5. `05｜单素材深度分析`
6. `06｜下一轮策略`

## Desensitization Rules

The sample report must not contain real customer names, advertiser ids, shop ids, material ids, video URLs, local artifact paths, exact cost amounts, account names, or real frame images.

Use anonymous labels such as:

- customer: `某滋补食品品牌`
- advertiser id: `ADV-XXXXXX`
- shop id: `SHOP-XXXXXX`
- material ids: `MAT-A001`, `MAT-A002`, `MAT-A003`
- period: an illustrative date range
- amounts: ranges or indexed values, not real currency totals
- frames: CSS placeholder thumbnails with business observations, not actual extracted JPEGs

## Visual Design

Keep the design practical and report-like:

- Dense but readable dashboard layout.
- Calm neutral paper background with restrained accent colors.
- Compact metric strips, workflow steps, artifact tables, and material cards.
- No marketing landing page or decorative hero-only experience.
- Text must wrap safely on mobile and desktop.

## Verification

Before completion:

- Confirm both HTML files exist.
- Check that the two pages contain the required sections and links.
- Scan the generated files for obvious sensitive terms from the repository context, including the real customer folder name, known project-specific ids, local absolute paths, and URLs.
- Open the files or run a lightweight static check to confirm the HTML is readable.

## Self-Review

This spec is limited to static showcase artifacts and does not alter the production skill. The report sample is intentionally illustrative and anonymized, so it demonstrates the final report structure without exposing real customer or run data.
