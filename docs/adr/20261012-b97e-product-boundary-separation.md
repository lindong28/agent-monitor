# Separate agent monitoring from Gateway audit history

Audience: Developer. Accepted 2026-10-12 after independent decision review (seven criteria; one retention-scope clarification).

## Context and decision

The user requested documenting the gaps then implementing independent product boundaries. Monitor owns agent sessions, usage, quotas and machine diagnostics. Gateway owns inference and call audit. Remove Monitor's Gateway UI, APIs, checkout dependency and call collection. Preserve agent usage with statistics extension v2 and legacy v1 usage reading. Gateway gains optional standalone source archives and a one-time legacy snapshot importer, preserving raw request/attempt records and machine namespaces; its default single-ledger behavior stays unchanged. No product-to-product navigation, proxy, shared database, Rust or PostgreSQL migration is included.

## Alternatives and consequences

Keeping a hidden Gateway reader prevents independent installation; a link to the single MacStudio ledger fails to preserve four-source historical access; duplicate full UIs retain the original ownership gap. A Gateway-owned archive preserves history without merging colliding source IDs into the inference ledger. Compatibility follows row schema, not source-code fingerprints (Gateway 20260916-380e).

Stable source identity, nonshrinking request/attempt identity sets, unchanged original observation timestamps on failure, and immutable published versions protect prior history. This change only supplies explicit single-run import/collect commands. Retaining old versions without GC is limited to this finite migration/verification. Before enabling continuous collection, implement bounded current+previous retention with active-reader protection as required by Gateway 20260915-9c21. No scheduler is enabled here.

## Scope and unverified boundary

Two session-owned development worktrees; local source/tests/docs only at decision time. Existing production files remain untouched. Historical import is a migration input, not an ongoing dependency on Monitor. Deployment must first establish final archive coverage, Gateway query/detail/export and independent collection continuity; only then retire Monitor's live entry. Upgrade the Monitor Hub reader before v2 source writers. After v2 publication, rolling back only to a v1-only Hub is unsafe. No performance benefit or live cutover is yet established. Gateway remote updates require authorized dual-remote push/pull.

This supersedes Monitor's Gateway calls UI/collection ownership in 20261006-d83e and its call-only worker purpose in 20261008-d102, preserving agent history/integrity and all unrelated behavior. Gateway 9c21 ownership and retention constraints remain effective.
