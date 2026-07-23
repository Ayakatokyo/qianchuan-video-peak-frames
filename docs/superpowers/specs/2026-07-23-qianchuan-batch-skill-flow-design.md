# Qianchuan Batch Skill Flow Design

## Goal

Make the batch workflow in `SKILL.md` select ranked materials from the asset table before invoking the Qianchuan RPA diagnosis connector. The workflow must preserve the selection artifact and prevent a batch run from using a different ranking and diagnosis period.

## Scope

This change governs skill guidance and the existing command sequence only. It does not add an orchestration script or change the single-material RPA implementation.

The existing online `result_type=files` CSV-envelope integration remains separate work. The batch instructions must not report a successful RPA run unless that adapter can process the returned CSV.

## Batch Contract

The default online batch flow accepts explicit `start-date` and `end-date` values. They are the single source of truth for both operations:

1. `export_top_materials.py` queries the asset table for the inclusive date range and writes `<output-dir>/top_materials.json`.
2. The skill validates that the JSON has a nonempty `materials` list, that every entry has `materialId`, and that `actualStartDate` and `actualEndDate` exactly match the requested range.
3. `extract_peak_frames.py --source rpa --top-materials-json` receives that exact file and uses `CUSTOM` with the same dates.
4. Normalized records are retained in `<output-dir>/diagnosis-records/`; peak-frame results are retained in `<output-dir>/peak-frames/`.

The skill must stop before RPA when selection fails, produces no eligible materials, has invalid material IDs, or reports a different date range. It must not substitute a prior `top_materials.json` from another run.

## User-Facing Command Sequence

`SKILL.md` will show the following ordered batch sequence, with a single run directory chosen before either command:

```bash
python scripts/export_top_materials.py \
  --start-date 2026-06-01 \
  --end-date 2026-06-07 \
  --top-n 50 \
  --output "/path/to/run/top_materials.json"

python scripts/extract_peak_frames.py \
  --source rpa \
  --shop-id "<rpa-authorized-shop-id>" \
  --top-materials-json "/path/to/run/top_materials.json" \
  --date-range-type CUSTOM \
  --custom-start-date 2026-06-01 \
  --custom-end-date 2026-06-07 \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/peak-frames"
```

The skill will state that `--material-id` is a single-material debugging or replay option. It is not an alternative selection method for a TOP-material batch run.

## Error Handling And Completion

The skill must require review of `top_materials.json`, every generated summary, corresponding JPEG files, and warnings before success is reported. For the current implementation, a `result_type=files` error is a blocking RPA adapter failure, not a partial success.

## Verification

Documentation verification will assert that `SKILL.md`:

- places asset-table selection before every batch RPA command;
- uses the same explicit dates in both commands;
- stores and reuses the run-local `top_materials.json`;
- marks `--material-id` as single-material only; and
- names the current CSV-envelope limitation accurately.

## Self-Review

This design adds no new CLI or runtime dependency. The scope is restricted to batch skill guidance and keeps the outstanding RPA CSV-envelope implementation explicitly out of scope.
