# Qianchuan Unreachable CLI Wrapper Removal Design

## Goal

Remove the two unused standalone CLI wrappers while preserving every standard
analysis and operational-recovery execution path.

## Scope

Delete only these scripts:

- `scripts/prepare_diagnostic_analysis.py`
- `scripts/render_final_report.py`

Update current operational documentation that presents either script as an
available command. Do not change product behavior, report data contracts, RPA
behavior, generated-run artifacts, or historical planning and handoff records.

## Execution Boundary

The supported user-facing entrypoint remains
`scripts/run_standard_analysis.py`:

- `prepare` calls `standard_workflow.prepare_standard_run()`.
- `status` calls `standard_workflow.standard_run_status()`.
- `finalize` calls `standard_workflow.finalize_standard_run()`.

`standard_workflow.py` calls `diagnostic_analysis_lib.prepare_diagnostic_analysis()`
and `final_report_lib.generate_final_report()` directly. The two deleted files
only adapt those same functions to obsolete direct CLI commands; no reachable
code imports or invokes them.

The recovery path remains intact. The runbook continues to permit direct use of
`scripts/extract_peak_frames.py` for retained-artifact investigation and
`scripts/repair_manifest.py` for local repair. Neither script is deleted or
altered.

## Documentation Changes

Update active documentation only:

- `docs/skill-flow.md` describes the standard workflow as directly calling the
  facts and report libraries, rather than listing obsolete CLI adapters.
- `references/rpa-output-contract.md` directs commercial report delivery
  through `run_standard_analysis.py finalize`, which already copies highlight
  assets and packages `final-report.zip`.

Historical material under `docs/superpowers/plans/` and
`docs/superpowers/handoffs/` is retained as historical evidence, even where it
records commands that existed at the time.

## Safety Checks

Before deletion, verify both candidate paths have no imports, subprocess calls,
or active-documentation commands outside historical records. After deletion:

1. Confirm the standard entrypoint still imports successfully and exposes
   `prepare`, `status`, and `finalize`.
2. Run the targeted standard-workflow, diagnostic-analysis, final-report, and
   skill-contract tests, then the full test suite.
3. Search active skill and operational documentation to confirm neither removed
   filename remains as an executable command.
4. Confirm the recovery runbook still names `extract_peak_frames.py` and
   `repair_manifest.py`.

## Non-Goals

- Do not delete shared implementation modules or any RPA, retry, validation,
  report-rendering, or recovery script.
- Do not replace `run_standard_analysis.py` with a new entrypoint.
- Do not rewrite historical design, plan, or handoff documents.
