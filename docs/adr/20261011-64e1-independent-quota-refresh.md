# Refresh quota independently of statistics

Accepted on 2026-10-11 after an independent decision review and a focused correction. The user requested a root fix for Overview Refresh leaving lindong5@philoai.xyz stale. Implementation and live results are recorded separately.

## Decision

An explicit Hub refresh starts a supervised quota-only round immediately, including while a statistics round is running. Each machine returns its quota without exporting or publishing a SQLite statistics generation. A private lightweight cache retains the observations. The existing statistics worker keeps its validation and publication behavior but uses cached quota. The GUI collector handles quota requests concurrently with local rollup, under its existing finite process lifetime; no new LaunchAgent or periodic service is introduced. Automatic statistics and hourly quota schedules remain unchanged.

The Hub merges quota only for an admitted source identity, unchanged configured SSH target, and an identical provider/account identity (Codex workspace plus email). Statistics remain authoritative for account membership and plan provenance. A different account produces a visible waiting-for-statistics message, not reassignment of another account's reading. Account changes therefore still require statistics admission. Query completion time does not establish identity ordering: a slow query can finish after the machine switched accounts. For the same identity, select a whole observation by its original updated_at; failures retain the last value/time. The common merged view supplies display, account directory, deletion checks and parent-owned account memory with its existing deletion epochs.

Quota progress is exposed alongside statistics progress through the existing refresh/status/overview APIs and shown next to the quota table. A changed quota observation triggers progressive rendering without waiting for a generation change. A refresh request is not terminal until its quota work has finished. At most one quota round and one coalesced follow-up run; machines in a round query concurrently, bounded by the declared machine count and eight slots. Each machine has independent credentials/endpoints; no concurrent round targets the same machine. Worker deadlines and the existing parent-pipe/process-group supervisor bound cleanup.

This partially supersedes the statistics-before-quota ordering in [c4e2](20261008-c4e2-sync-process.md). It preserves process isolation, snapshot schemas, validation, retention, accounting and credential ownership. No inference messages or new login flows are initiated. The change is A1/V1: reversible internal tooling with persisted observations and concurrent state requiring one implementation reviewer. User-visible failures stay in the quota area; existing unattended-service alert coverage is unchanged.

## Evidence and alternatives

On 2026-10-11 the observed refresh started at 01:42:18.968501Z; macmini's quota query completed at 01:44:41.572559Z, about 143 seconds later. Before its statistics stage completed, no quota request file existed. The existing managed-account quota-only path took 5.2 seconds and returned 100% seven-day usage without sending a message. This contrast identifies avoidable dependencies; it is not a latency guarantee for the new machine path.

Changing only UI text leaves the wait. Refreshing only saved managed logins misses other machine accounts and Claude. Querying first but publishing through a full statistics generation still delays visibility. Removing statistics validation would weaken an unrelated contract. The lightweight observation cache is limited to eliminating this publication dependency.

Verification must distinguish a blocked statistics job from a completed quota query, preserve values on failure/restart/late snapshots, reject wrong source/account/target, and confirm live Overview Refresh updates the target row while statistics are still running. New-path latency and deployment were unverified when this decision was accepted. Rollback reverts code without changing saved statistics or account history; the extra cache can remain unused.

## Implementation checks

Independent V1 review found and then verified a correction separating signed-out query outcomes from fallback quota age. The affected 169-test suite passed, including 10 independent-quota cases, GUI requests during blocked rollup and Node progressive rendering with unchanged generations. A full 901-test run initially had 11 failures and one error: two obsolete serial-quota expectations were updated and passed; the other nine failures and one mock-signature error reproduced unchanged on an isolated a496c82 archive in the same environment. They concern Gateway schema/projection expectations and the pre-existing account-memory mock, and are outside this quota fix. Production timing remains a separate deployment check.
