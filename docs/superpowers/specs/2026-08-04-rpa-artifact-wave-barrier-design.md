# RPA Artifact Wave Barrier Design

## Goal

Start a new RPA wave only after every material in the previous wave has reached a terminal CSV-artifact state. Keep completed-material postprocessing concurrent with the next wave's RPA work.

## Terminal Artifact States

Each material releases its wave only when one of the following is true:

- Successful CSV-artifact state: the expected `diagnosis-csv/{materialId}.csv` exists, is non-empty, was created or modified no earlier than the material's current RPA attempt, parses successfully, and contains the expected `materialId`. `diagnosisCsvPath` records this verified artifact.
- Final failure state: `rpaResultStatus` is `final_failed` after all configured recovery attempts have completed.

`pending`, retryable failures, a gateway response without a verified CSV, a stale CSV from before the current attempt, and an invalid CSV do not release a wave.

## Scheduling

The RPA worker records an attempt boundary, fetches or recovers the RPA result, and verifies the per-material CSV before it resolves its future. The batch runner waits for every future in the active wave, then validates the terminal CSV-artifact state of every corresponding entry before submitting the next wave.

After CSV verification, the diagnosis-record JSON materialization and video/frame postprocessing may proceed concurrently with later-wave RPA work. Postprocess success remains the criterion for Top N report selection and backfill.

## Observability

The manifest records the wave's CSV-artifact-completion time separately from postprocess completion. The value is written only after all wave entries meet a terminal CSV-artifact state. Existing per-material RPA and postprocess timestamps remain unchanged.

## Error Handling

If a completed RPA future does not yield either terminal artifact state, the runner treats this as a scheduler contract failure, records a final failure diagnostic, and does not start the next wave until the entry is terminalized. This prevents a malformed worker result from silently bypassing the barrier.

## Tests

Add coverage that blocks creation of one material's CSV in wave 0 and asserts that no wave-1 RPA call starts until the CSV is fresh, parseable, and matches that material ID. Add coverage that a stale or mismatched CSV does not release the wave, while a retry-exhausted `final_failed` result does. Preserve coverage showing that postprocessing may overlap with subsequent-wave RPA.
