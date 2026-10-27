# Extract responsibilities behind stable entrypoints

Status: accepted, 2026-10-27.

The root CLI and service scripts remain public entrypoints. New internal responsibilities live under `agent_monitor/`; this is an incremental extraction, not an installed-package conversion. A root path bootstrap supplies stable checkout/state/resource locations. One source inventory exposes purpose-specific views for compilation, runtime drift, service signatures and clean export admission.

Server account memory, collection coordination and quota views receive explicit ownership. Shared rollup storage owns the existing lock registry and read lifecycle. Quota acquisition is a public service consumed by Hub and exporter. Browser shared/page modules retain the static no-build delivery and propagate asset versions through their dependency graph.

This resolves demonstrated dependency and ownership coupling. Moving every root module first would retain that coupling while changing more paths; documentation alone cannot remove it. New frameworks, storage engines and data migration are unnecessary for this change.

Preserve the existing CLI/API and snapshot schemas, accounting, identities, state symlinks and locks, provider requests and collection cadence. Account memory must retain the sequence admission, epoch registration, live candidate derivation. Storage extraction must retain one lock owner, fork handlers and private read copies. Asset caching retains the dashboard delivery ADR guarantees.

Independent decision review approved these boundaries. Implementation interfaces, affected tests, real browser navigation, CI and deployment readback still require acceptance; this decision is not evidence that those checks passed. The architecture map is the authority for the resulting module and consumer boundaries.
