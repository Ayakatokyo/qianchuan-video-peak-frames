# RPA/Postprocess Scheduler Benchmark Design

## Goal

Measure the real-RPA performance difference between the current wave barrier
scheduler and an experiment-only scheduler that permits the next RPA wave to
start once the current wave's RPA work has reached terminal state, without
waiting for that wave's postprocessing work to finish.

The experiment uses the same rank-ordered candidate-pool snapshot for both
runs. It stops after RPA and all started postprocessing work reach terminal
state. It does not generate a fact package, AI analysis, report data, or HTML.

## Fixed Experiment Inputs

- Analysis interval: 2026-07-30 through 2026-08-04.
- Target: Top 9 successful materials.
- Candidate pool: one Top 27 export, snapshotted before either RPA run.
- RPA and postprocess concurrency: 3 for both variants.
- RPA credentials: taken only from the invoking process environment; never
  persisted in source control, manifests, benchmark summaries, or logs.

## Variants

### Barrier baseline

Use the existing `rpa_wave_barrier=True` behavior. A new RPA wave starts only
after every current-wave RPA future and every current-wave postprocess future
is terminal. This is the production scheduler and remains the default.

### Overlap comparison

Use a benchmark-only `rpa_only` wave-barrier mode. This mode retains the RPA
wave boundary: the next wave starts only after every RPA future in the current
wave is terminal. It removes only the postprocess part of the barrier, so
prior selected materials may still be downloading and extracting frames when
the next RPA wave starts. It is intentionally distinct from the runner's
legacy continuous-fill branch, which starts a replacement request whenever
any RPA slot is free and would not isolate the requested comparison.

The scheduler retains all existing request recovery, receipt verification,
postprocess retry, serial compensation, terminal backfill, and manifest
validation behavior. Only the wave-opening condition differs.

## Architecture

Add an experiment-only command that:

1. Exports the candidate pool once and writes its SHA-256 hash into a benchmark
   request record.
2. Creates separate `barrier/` and `overlap/` output roots below a new,
   user-selected benchmark root. Existing roots must be absent so a failed or
   resumed run cannot contaminate the comparison.
3. Invokes the existing RPA batch entry twice, once per mode, with the identical
   candidate JSON and fixed concurrency.
4. Adds an append-only scheduler event log and a final benchmark summary for
   each variant. The main batch manifest remains the authoritative operational
   artifact.
5. Writes a combined comparison report only after both variants are terminal.

The normal `run_standard_analysis.py prepare` command is not changed and
continues to enforce the production barrier before downstream facts.

## Metrics

All timestamps are monotonic seconds relative to each variant's batch start.

- `rpaSubmittedAtSec`, `recordsPersistedAtSec`, and `postprocessSubmittedAtSec`
  for each accepted material.
- RPA terminal time for each submitted material and each wave.
- Postprocess terminal time for each selected material and each wave.
- `allRpaTerminalAtSec`, `allRecordsPersistedAtSec`, and
  `allPostprocessTerminalAtSec` for each variant.
- Overlap duration: time in which an RPA future and an earlier-wave
  postprocess future are both active.
- RPA throughput, full batch duration, selection count, successful
  postprocess count, retry count, and failure classifications.

The comparison report must label runs as non-comparable when their candidate
hash, requested parameters, concurrency settings, or terminal outcome counts
differ. It reports the raw results in that case but does not claim a speedup.

## Failure and Safety Rules

- Every real RPA run has an isolated output root and lock path.
- A gateway or local failure follows the existing recovery policy. A terminal
  postprocess failure can trigger existing backfill, and both variants receive
  the same candidate limit.
- Both runs execute sequentially to avoid cross-run RPA, network, disk, and
  FFmpeg contention. Concurrency exists only within each run.
- An interrupted experiment leaves its run directories and event logs intact;
  it does not silently resume or resubmit RPA.
- Benchmark execution never calls the diagnostic-analysis or final-report
  modules.

## Validation

Automated tests cover the event order for both variants, isolation of output
paths, parameter/hash comparability checks, metric aggregation, and that the
production CLI still rejects an externally selectable no-barrier mode. Before
real RPA, a deterministic fake-adapter test proves the overlap run submits the
second RPA wave while first-wave postprocessing remains blocked; the baseline
test proves it does not.

For the real run, preserve both manifests, event logs, the candidate snapshot,
and the comparison report. Report only evidence contained in those artifacts.
