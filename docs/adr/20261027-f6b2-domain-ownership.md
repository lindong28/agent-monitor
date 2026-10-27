# Complete the query, ingestion and page boundaries

Status: accepted, 2026-10-27. Continues [the initial extraction](20261027-structure-boundaries.md).

HTTP entrypoints still contain overview/session calculations and network cache ownership; aggregators also contains project identity persistence and the loading pipeline. Shared usage models live in the parser package, and snapshot admission imports a constant from the rollup implementation that consumes admitted generations. Overview and collection page implementations remain in the browser shell.

Extract instance-owned query services under `agent_monitor/views/`, project identity and usage loading under `agent_monitor/ingestion/`, and dependency-light existing models and constants under `agent_monitor/contracts/`. Keep aggregation over supplied entries separate from discovery and persistence. Extract overview and collection factories using the existing browser module and page-lifetime interfaces. The architecture map defines placement for subsequent internal modules; this does not convert the project into an installed distribution.

Explicit constructors and factories preserve the current runtime without a new dependency-injection container, Web framework or build step. Moving all root files first would change paths while retaining the demonstrated coupling. Documentation alone would leave the state ownership and dependency edges intact. Root command entrypoints remain stable; compatibility exports must reference the same implementation and model objects, and remain only for actual consumers.

Preserve CLI/API behavior, snapshot schemas, accounting, identities, state locations/symlinks, locks, provider policy and collection cadence. The ingestion lock order remains identity/rollup lock before the loading lock. One network report instance shares its cache and bounded failed probe across requests. Parser-cache fingerprints must follow the moved authoritative code, independently of deployment source signatures. Frontend dependency versions and page cleanup follow the existing [asset delivery decision](20261026-8d31-dashboard-delivery.md).

One independent decision review approved this scope. Final interfaces, consumer migrations, isolated-instance behavior, installation, regression tests, browser navigation, CI and deployed behavior require implementation acceptance; this decision does not claim they have passed or establish a performance gain.
