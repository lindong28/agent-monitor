# Separate network heuristics from missing evidence

Date: 2026-10-14. Status: accepted for implementation; deployment and historical replay verification are separate.

## Context

Public/DNS failures could render healthy or disabled statements; local IPv6 route selection was described as verified leakage; invalid StopForumSpam responses were treated as not-listed. Existing HIGH thresholds are product heuristics and missing risk was intentionally not itself HIGH.

The user requested specification-backed review and correction of every user-visible Monitor metric, with reusable documentation. Baseline was `22507ab5fbe1c794cdfb870c386d89f572f1d91e`.

## Decision

Keep the 30/70 score bins and existing positive HIGH signals. Add coverage.missing for public/dns/risk/timezone and coverage.limitations containing ipv6_leak_unverified. Missing only risk retains the heuristic verdict with partial coverage; missing public/DNS/timezone yields unknown unless a known HIGH signal exists. The deprecated ipv6_leaked field describes local-address presence only; it is not a remote leak proof. Require SFS success and explicit valid ip.appears before interpreting listed/unlisted. Derive unknown DNS count from the existing array without adding local fields.

## Alternatives and consequences

Making every missing probe HIGH produces false alarms. Keeping unqualified LOW/healthy or IPv6-disabled text overstates evidence. Adding new external leak probes expands traffic and scope unnecessarily. Web and both CLI renderers must consume the same coverage meaning; no provider-availability guarantee follows.

## Evidence and verification boundary

Definitions and versioned upstream sources are in the [metric specification](../references/metrics.md) and [operations appendix](../references/metrics-operations.md). The local audit and decision reviews are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`; they establish bounded source arguments and counterexamples, not production occurrence rates. Independent decision review accepted this direction. Real consumer checks, published revision, deployed process and any historical replay results belong to the release execution record; this ADR does not assert their completion.
