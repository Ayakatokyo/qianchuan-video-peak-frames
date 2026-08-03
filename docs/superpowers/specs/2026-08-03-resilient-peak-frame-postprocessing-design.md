# Resilient Peak-Frame Postprocessing Design

## Context

An RPA response can be valid and persist its normalized records, yet its local
video postprocessing can fail when FFmpeg is killed by the host. A historical
failure returned `-9` while extracting the 11-second frame for material
`7647777582327382057`. The same material succeeded in later batches, so this
is not evidence of a permanently corrupt video or an invalid timestamp.

The current extraction pipeline is already scripted, but it treats a failed
individual frame as a failed material and stops processing the remaining
frames. This makes a transient resource failure discard otherwise useful
analysis evidence.

## Goals

- Attempt the complete desired frame set: zero-second cover plus up to three
  local click peaks.
- Distinguish a curve with fewer than three local peaks from a technical frame
  extraction failure.
- Preserve successful frames when another frame fails.
- Allow a material to reach downstream analysis when its frame evidence is
  sufficient, while making any missing expected frame visible in that
  material's deep analysis.
- Retry resource-related FFmpeg failures locally without repeating RPA.

## Non-goals

- Inventing synthetic peaks for a monotonically changing click curve.
- Treating an RPA-selected material as an RPA failure because local processing
  failed.
- Hiding missing frames to make a report appear complete.

## Frame Contract

`select_peak_frames` remains the source of the expected frame set:

- `zero_second` is always expected.
- `peak_top_1` through `peak_top_3` are expected only when the input curve has
  that many local peaks. Fewer peaks are an observed curve characteristic, not
  a failed extraction.
- The normal target is four frames when three local peaks exist.

Each expected frame receives one of `succeeded` or `failed`, with a path on
success and a bounded error record on failure. Extraction continues after a
per-frame failure, so later peak frames are still attempted.

Material postprocessing succeeds when:

1. `zero_second` succeeded; and
2. either no local peak was expected, or at least one expected local peak
   succeeded.

A material that does not meet this rule remains a postprocessing failure and
uses the existing retained-record retry and rank-ordered backfill behavior.

## Resource-Safe Extraction

The postprocessing runner keeps its bounded channel but uses a conservative
default parallelism for FFmpeg-backed work. An FFmpeg process terminated by a
signal, including `SIGKILL`, is retried once in a low-resource invocation
before the frame is marked failed. The retry changes only local extraction
settings; it never re-fetches RPA records or re-downloads diagnosis data.

The manifest and per-material summary retain the attempt count, command mode,
return code or exception type, and bounded stderr excerpt. This allows a later
run to distinguish an environmental kill from input validation or decoding
failure.

## Data Flow

```text
records -> select expected frames -> extract each expected frame
                                      | success -> retained JPEG
                                      | failure -> per-frame error, continue
                                      v
                              frame availability summary
                                      |
                    material success rule / retained-record retry / backfill
                                      |
                    diagnostic fact package -> AI deep analysis -> report
                                      |
                      per-material missing-frame explanation when applicable
```

`summary.json` and the diagnostic fact package will include expected, produced,
and missing frame lists. The AI prompt will require a concise missing-frame
notice under the corresponding material's deep analysis. The HTML/data report
will render that notice at the same material, rather than as an unrelated batch
warning. A curve with fewer than three local peaks is explained as such; a
failed expected frame is explained as a technical extraction gap.

## Verification

Tests will cover:

- a monotonic curve producing only a successful cover frame without a missing
  frame warning;
- a curve with fewer than three peaks explaining the absent non-expected peaks;
- a failed expected peak that does not prevent later peak extraction;
- material success with cover plus one available peak and a corresponding
  deep-analysis disclosure;
- failure when the cover fails, or when all expected peaks fail;
- a signal-terminated FFmpeg attempt retrying in low-resource mode without an
  RPA call; and
- rendering the missing-frame disclosure beneath the affected material only.
