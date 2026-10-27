# Complete internal domain placement and owner interfaces

Status: accepted, 2026-10-28. Continues [domain ownership](20261027-f6b2-domain-ownership.md).

The root inventory contains internal ingestion and view helpers alongside public commands. Shared session/project identity is also consumed by parsers and snapshots; those rules belong to dependency-light contracts, not to a higher-level ingestion composition. Move ingestion cache/archive into ingestion, session/conversation caches and prepared query views into views, identity rules into contracts, and pure aggregations/static resource delivery into the internal package. Migrate real consumers and source fixtures; keep actual command/service paths and independent subsystem owners at the root. This is a checkout-based application, not a new installed distribution.

Separate shared file synchronization from generation business logic, quota message persistence from Hub lifecycle, configuration/role reads from Hub orchestration, calendar rules from storage/rollup implementations, and HTTP rejection rules from ip-check presentation. Return quota recovery state ownership to its Manager while keeping HTTP validation and response delivery at the handler.

Documentation alone leaves the observed cross-owner writes and dependencies. Moving every root file changes unrelated paths without resolving those boundaries; duplicating helpers creates competing implementations. These extractions use existing domains and narrowly shared functions instead of a general utilities layer or new framework.

Preserve CLI/API results, accounting and date boundaries, source identity, snapshot schemas, configuration selection timing, state symlinks, locks, provider requests and no-retry policy. In recovery, keep occupancy through HTTP response delivery and preserve records assignment, persistence, revision increment and failure ordering. Do not conflate the differing account-memory and quota JSON durability sequences. Keep token_cost.py byte-identical.

An independent decision review approved this scope. Package import cycles, moved-source fingerprints, test fixtures, installation, normal browser consumers, CI and deployed identity still require implementation validation; this decision does not claim those checks passed or establish performance gains.
