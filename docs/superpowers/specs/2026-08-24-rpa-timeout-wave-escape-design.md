# RPA Single-Timeout Wave Escape Design

## Goal

Prevent one long-running material RPA request from holding an entire wave and
delaying subsequent candidate materials. A material that exceeds one configured
timeout becomes terminally failed, releases its local concurrency slot, and
allows the next wave to backfill the missing material.

## Context

The 2026-08-24 production run recorded a transport timeout for rank 4. The
request made two 600-second attempts and held WAVE 1 for about 20.5 minutes.
The current 480-second soft timeout is telemetry only; the wave barrier still
waits for every RPA and post-process future to reach a terminal state.

## Chosen Approach

Use one RPA execution timeout, with a default of 480 seconds. At that deadline,
the local isolated worker is terminated and the material becomes a terminal RPA
failure. It does not receive a second full-duration retry or a blocking
late-artifact grace wait.

The existing wave barrier remains in effect. It opens the next wave after the
other materials in the current wave have also reached terminal RPA and
post-process states. If the Top-N target still has a deficit, the next wave
starts up to `rpaConcurrency` later candidates together. With the normal
concurrency of three, a wave containing one timed-out material and two completed
materials starts three later candidates in the following wave.

## State And Contract

New manifests record `rpaTimeoutExceeded: true` and `rpaTimeoutSec: 480` for a
material that crossed the configured deadline. The failure is terminal with
`rpaResultStatus: final_failed` and a specific timeout failure reason. Existing
`rpaSoftTimeoutExceeded` and `rpaHardTimeoutExceeded` fields remain accepted
when reading historical manifests, but new executions do not write them.

The CLI retains `--rpa-preflight-timeout-sec` as the only RPA duration control
and changes its default from 600 to 480. The separate soft-timeout argument is
removed. The timeout is passed to the isolated worker as a single deadline.

## Error Handling

The timeout terminates only the local child worker. The upstream RPA service may
finish after that point because the connector has no reliable remote-cancellation
operation. A late CSV is not accepted into the already-advanced run, which keeps
Top-N selection deterministic. It remains available for later explicit
`--resume` or local repair/reconciliation under the existing receipt rules.

Fast failures that complete before the duration deadline retain the existing
bounded retry/backoff behavior. A duration timeout bypasses that retry path,
because retrying it would recreate the wave stall that this change removes.

## Test Acceptance

1. The parser exposes only one RPA execution timeout and defaults it to 480
   seconds.
2. An isolated worker still alive at that deadline is terminated and returns a
   terminal timeout result with the configured timeout value.
3. A timed-out wave attempt does not issue a second full-duration RPA request.
4. With `rpaConcurrency=3`, one timed-out material plus two completed materials
   causes the next wave to start the next three candidates, after the current
   wave reaches terminal state.
5. Historical manifests containing the former soft/hard timeout fields remain
   schema-valid; new timeout telemetry is schema-validated.

## Non-Goals

- Do not change the RPA service's remote cancellation behavior.
- Do not disable the wave barrier or introduce rolling scheduling.
- Do not accept late artifacts into an already-advanced wave.
