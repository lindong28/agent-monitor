# Report orphan disposition using replay authority

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

The checker counted stored keys absent from current source but always called them preserved. Authoritative history replay can delete those same obsolete keys before rebuilding, so the dry-run description did not predict the writer.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Keep orphan_rows.count/items compatibility and add each item's action remove/preserve. Use the writer's effective authority floor (including minimum_date) and project eligibility. CLI reports preserved and rebuild-removed partitions; usage orphan reporting applies the same rule. A read-only check itself deletes nothing.

## Alternatives and consequences

Disabling authorized deletion would obstruct accounting corrections. Merely changing all orphans to “removed” would be false outside authority. The disjoint action partition retains the useful total while describing actual disposition; would_write remains logical changes, not SQL upsert count.

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
