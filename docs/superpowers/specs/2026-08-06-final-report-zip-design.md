# Final Report ZIP Delivery Design

## Goal

Make the completed standard workflow deliver a portable `final-report.zip` alongside
the existing `final-report/` directory. The archive allows a recipient to extract it
and open the report with local high-light images, without relying on the report host
or third-party image URLs.

## Scope

The workflow continues to render and validate `final-report/` exactly as it does
today. Once that directory is ready, it creates a sibling archive at
`<run-output>/final-report.zip`.

The archive has one top-level `final-report/` directory containing:

- `final-report.html`
- `final-report-data.json`
- `assets/highlight-frames/` and every image referenced by the HTML

The generated HTML continues to use relative paths for high-light frames, so the
same report works both from the directory and after extraction from the archive.

## Video Boundary

Video and thumbnail fields remain the URLs returned by the platform. The workflow
does not download, rewrite, embed, or archive video bytes. This preserves the current
preview behavior and avoids materially increasing archive size or generation time.

## Archive Creation

Archive creation occurs only after HTML rendering and copied-image validation have
succeeded. It writes to a temporary ZIP in the run directory and atomically replaces
`final-report.zip` only when packaging succeeds. The packager includes regular files
under `final-report/` and excludes unsafe paths and temporary files.

The workflow records the ZIP as the delivery artifact in `run-summary.json` and
prints its absolute path from `run_standard_analysis.py finalize`. The existing HTML
artifact remains recorded for diagnostics and readiness validation.

## Failures And Retry

If ZIP creation or archive-content validation fails, finalization fails at the report
stage and leaves the run in `analysis_pending`. A corrected environment can rerun
`finalize` with the existing AI analysis JSON; no selection, RPA, or AI analysis is
repeated.

## Verification

Tests will cover:

- ZIP creation with the expected top-level directory and report files.
- Inclusion of copied high-light frames and relative image references.
- Preservation of external video URLs without video payloads in the archive.
- A final workflow summary and CLI output that expose `final-report.zip`.
- Failure behavior when packaging cannot produce a valid archive.
