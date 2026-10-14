# Attribute Codex usage to its actual history and turn

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

At baseline 22507ab, a copied child with inherited cumulative 100/150 and child 30 contributed 180 instead of 30. Thread-current model also reassigned both historical turns to the latest model. Official Codex rust-v0.162.1 persists copied event prefixes and per-turn model contexts.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Exclude proven inherited intervals using explicit ordinals/history boundaries or recoverable parent evidence; compute only child deltas and follow historical turn_context model. Emit source_error for an unrecoverable first segment rather than inventing consumption or a default known model. Proven prior erroneous archive keys require explicit correction/zero replacement and bounded authoritative replay. Only add TokenUsageRecord consumption when its model and deduplication boundaries can be closed.

## Alternatives and consequences

Skipping the first event loses real no-copy child usage and still counts longer copied prefixes. Dropping all forks loses child use. Timestamps or coincidental counter equality are not inherited-history authority. This extends the retained-observation approach in [20261013-42b6](20261013-42b6-usage-observation-correction.md), without making unavailable history reconstructable.

The first production replay exposed an overly conservative branch: paginated subagents of six source-verified producers (`0.147.0`, `0.152.0`, `0.153.4`, `0.154.0`, `0.158.0`, `0.162.1`) remove inherited TokenCount before seeding a fresh counter. The exact startup identity and boundary checkpoint described in the metric specification establish zero initial usage; dropping that first child amount is incorrect. Version `0.147.0` predates checkpoint `thread_id` and uses its explicit initial ordinal boundary; later verified versions require owner matching. Unverified versions, legacy histories and later resume checkpoints do not acquire this exception. The correction preserves the existing archive keys and requires another normal authoritative replay; it does not widen retention or recover ambiguous copied prefixes.

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
