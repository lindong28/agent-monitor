# Count only events not successfully emitted

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

The diagnostics writer cleared its queue, emitted events, then wrote current.json in one try block. Any failure counted the entire batch as dropped, including events already emitted. Independent two-event examples distinguished first-emit, second-emit and snapshot-write failure.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Count queue evictions and events not successfully emitted. Track pending events and decrement after each successful emit, so a later snapshot failure does not mark emitted events lost. Keep snapshot persistence_error separate. Successful emission is not a claim of fsync or crash durability.

## Alternatives and consequences

Changing only the label would retain a numerically incorrect loss count. Expanding to a durable logging protocol is not required by the observed defect. Existing process-local bounded observability and operation counters remain unchanged.

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
