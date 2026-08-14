# Skill Contract and Frame Batching Design

## Scope

Apply two documentation-level refinements to the Qianchuan short-video analysis skill. Runtime workflow, RPA retries, candidate backfill, and report-generation behavior remain unchanged.

## Changes

1. Make schema version 5 the required standard AI-analysis output in `SKILL.md`. State that version 4 remains accepted only for historical compatibility. Align the skill contract test with that requirement.
2. Require JPEG review in batches of at most six frames. When a batch is rejected by a policy or guardrail, split the rejected batch further and continue until all fact-package frames have been reviewed. No frame may be skipped.

## Error Handling

The frame-batching rule applies only to the model's image-review interaction. It does not retry RPA, regenerate facts, or alter the postprocessing pipeline.

## Verification

The skill contract test must assert the v5 standard and the batch/retry wording. The existing standard-workflow and final-report tests must continue to pass.
