# RPA Artifact Wave Barrier Design

## Goal

Start a new RPA wave only after every material in the previous wave has reached a terminal artifact state. Keep completed-material postprocessing concurrent with the next wave's RPA work.

## Terminal Artifact States

Each material releases its wave only when one of the following is true:

- Successful artifact state: `rpaResultStatus` is `available`, `recordsPersisted` is `true`, and `diagnosisRecordsPath` identifies the atomically written diagnosis-record JSON.
- Final failure state: `rpaResultStatus` is `final_failed` after all configured recovery attempts have completed.

`pending`, retryable failures, a gateway response without persisted records, and an in-progress record write do not release a wave.

## Scheduling

The RPA worker remains responsible for fetching, recovering, parsing, and atomically persisting the diagnosis records before it resolves its future. The batch runner waits for every future in the active wave, then validates the terminal artifact state of every corresponding entry before submitting the next wave.

Postprocessing is submitted immediately for successful entries and is deliberately excluded from the wave barrier. It can therefore overlap with later-wave RPA work. Postprocess success is still the criterion for Top N report selection and backfill.

## Observability

The manifest records the wave's artifact-completion time separately from postprocess completion. The value is written only after all wave entries meet a terminal artifact state. Existing per-material RPA and postprocess timestamps remain unchanged.

## Error Handling

If a completed RPA future does not yield either terminal artifact state, the runner treats this as a scheduler contract failure, records a final failure diagnostic, and does not start the next wave until the entry is terminalized. This prevents a malformed worker result from silently bypassing the barrier.

## Tests

Add coverage that blocks diagnosis-record persistence for one material in wave 0 and asserts that no wave-1 RPA call starts until persistence completes. Add coverage that a retry-exhausted `final_failed` result releases the next wave. Preserve coverage showing that postprocessing may overlap with subsequent-wave RPA.
