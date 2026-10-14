# Known-cost subtotals with pricing coverage

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

Overview treated all-unpriced observations as zero and hid the excluded cohort in mixed totals. Explore exposed known subtotals without sufficient per-group pricing coverage. Period comparison already distinguished known cost, unknown prices and empty data; Sessions already required every cost to be known.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Retain known-cost subtotals for Overview/Explore and carry known/unknown observation counts through each displayed grouping. With no priced observations, amount is null; a known zero remains zero. Keep Sessions' strict total=null when any selected entry is unknown. Legacy daily fields must not coerce null to zero. Pricing coverage is not source completeness.

## Alternatives and consequences

Making every mixed aggregate null would hide useful known amounts and diverge from period comparison. Renaming the amount without counts would leave empty/unpriced/zero ambiguous. No new schema, raw-history rescan or accounting platform is needed because rollups already retain entry_count and cost_known_count.

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
