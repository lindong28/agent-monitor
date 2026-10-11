# Prepared generation views for aggregate queries

Accepted on 2026-10-11 after one independent decision review (all seven criteria satisfied; no unresolved findings).

Keep existing SQLite daily aggregates and prepare validated, leased generation views outside HTTP requests. Initialize before listening; refresh through one bounded background preparation lane and before statistics completion is announced. Requests reuse an immutable admitted view, check configuration and file signatures, and release their reference after querying. Retired/configured-away or modified sources must not remain usable. Admission exclusions retain existing coverage semantics; preparation exceptions produce explicit unavailability rather than an indefinitely stale success.

Prepare and validate snapshot copies outside the machine publication lock, with a preparation lease protecting live staging from cleanup. Retention checks use the prepared copy; pointer and retirement checks are repeated at commit. Preserve publication journal recovery and generation leases. Reuse validation only for the actual validated file signature after rename, never for an ID alone.

Explore loads independent data concurrently and ignores quota-only changes for statistics recomputation. Use correctly framed persistent HTTP responses, conditional asset caching and Server-Timing. Keep snapshot/accounting schemas, source identity, sync-process isolation, fresh full-query workers and their two-slot bound. No new TSDB or persistent summary database.

The user explicitly requested recording and implementing the performance recommendations. Cold admission measured 1.0706 seconds against 3.05 GB of snapshots, versus 2–3 ms for aggregate SQL; these are diagnostic samples, not a latency guarantee. Full incremental transfer remains conditional on later collection/transfer evidence. See [implementation record](../explore-performance.md) for measurements, alternatives, failure behavior and checks. Browser improvements during real generation changes remain unverified at decision time. Code rollback requires no schema/history migration.

Implementation review correction: the first sync-status read is a required generation barrier, so it remains ahead of the concurrent filters/pivot pair. Parallelizing that status too allowed an old pivot to be paired with two already-new statuses and skip reconciliation. The existing post-query comparison remains. This narrows concurrency while preserving the original consistency boundary; it adds no new data protocol.
