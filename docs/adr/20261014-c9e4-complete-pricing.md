# Require applicable rates before declaring cost known

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

The adapter accepted arbitrary substring model aliases and returned partial arithmetic as fully priced when a used cache category lacked a rate. Bundled Sonnet 5 rates differed from the checked first-party standard price. Existing recorded costs take precedence over calculated estimates.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Reject arbitrary substring aliases; require all rates actually used by nonzero categories, including dedicated 1h writes, before returning a known estimate. Keep token_cost.py shared arithmetic unchanged. Correct standard bundled Sonnet 5 input/output/read/5m-write/1h-write to 2/10/0.2/2.5/4 USD per million tokens, checked 2026-10-14 against official pricing. Describe amounts as recorded values or estimates at applicable configured rates; do not promise all history is revalued at current rates.

## Alternatives and consequences

Zero fallback conflates unknown pricing with known free usage. Expanding the snapshot into a partial-price provenance schema is unnecessary for this bounded correction. New rates alone do not authorize replacing historical stored amounts or claiming invoice accuracy. Official sources and historical provenance limits are in [metrics](../references/metrics.md#sources-versions-and-limits).

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
