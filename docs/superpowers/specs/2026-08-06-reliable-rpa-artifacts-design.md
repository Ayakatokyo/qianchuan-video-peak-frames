# RPA Durable Artifact Receipts Design

## Baseline And Scope

This design is to be implemented only on `codex/remove-legacy-rpa-paths`, or on
a branch that contains it. It relies on the single Top-N wave scheduler and does
not restore the removed preflight, legacy batch, streaming, or single-material
RPA paths.

## Goal

Preserve and reconcile completed local RPA artifacts when a worker has written
CSV or normalized records before the wave scheduler updates `batch-manifest.json`.
Reconciliation must perform zero RPA calls.

## Durable Receipt

For each wave material, atomically publish
`rpa-receipts/<materialId>.json`. The receipt records schema version, material
ID, client attempt ID, lifecycle state, timestamps, CSV path, records path, CSV
size, and SHA-256 digest. Allowed states are `submitted`,
`artifact_downloaded`, and `records_persisted`.

The worker publishes `artifact_downloaded` after the CSV is atomically visible,
and `records_persisted` only after parsed records are atomically visible. The
parent then consumes the receipt to derive the wave manifest state. The receipt
and retained artifacts are the recovery source of truth; the manifest remains
the scheduler view.

## Reconciliation

Before a resumed wave submits any pending request, reconciliation validates all
receipts. A valid `records_persisted` receipt restores the matching material as
selected, marks records persisted, and queues only missing post-processing.
Mismatched material IDs, unreadable records, missing files, or digest failures
are explicit local-artifact errors and never count as RPA success. Reconciliation
does not submit, retry, or otherwise invoke RPA.

## Platform Boundary

The receipt closes the local crash window after a CSV URL has been received.
It cannot recover a platform-completed task if the client died before obtaining
that URL. The observed platform callback already has request, scheduler,
executor, and OSS identifiers; later platform work must expose a query by client
request ID before this skill can offer true remote result retrieval.

## Verification

Implementation starts test-first with coverage for receipt publication order,
valid receipt reconciliation, malformed receipt rejection, manifest repair, and
the zero-RPA-call invariant. The existing three-RPA-per-wave scheduling contract
remains unchanged.
