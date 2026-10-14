# Preserve quota observations and display reported windows

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

Passive readers reset expired observed percentages to zero even though passing reset time supplies no observation of a new period. Batch reset-known ignored valid before readings. UX B2's old “Codex does not report 5h” premise conflicts with the versioned 300-minute window interface and the existing reader fields.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Preserve historical used/reset/observed-at values for both providers. Batch reset-known uses the latest applicable whole reading, including valid before, and counts only future resets. Display reported Codex 5h and 7d values; unreported windows remain unknown. On 2026-10-14 the user explicitly selected “更新契约并修复 (Recommended)” through MCP question codex_5h_contract; this authorizes superseding B2's no-5h premise. The existing [whole-reading decision](20261007-a62e-codex-quota-display.md) and [recovery decision](20261011-7c3a-account-round-recovery.md) remain applicable.

## Alternatives and consequences

Elapsed-reset zero invents a new provider fact. Counting only after while labeling the result “known reset” disagrees with valid retained before observations. Continuing to hide reported 5h would preserve an unsupported factual assumption. No account percentages are added, no send is inferred from quota, and no new provider query is introduced.

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
