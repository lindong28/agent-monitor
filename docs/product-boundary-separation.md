# Product boundary separation

Audience: Developer. Execution record started 2026-10-12, before implementation.

## Authorized outcome

The user requested recording the gaps and proposed improvements, then implementing the agreed separation. agent-monitor owns Claude Code/Codex sessions, usage, account quotas, machine collection and network diagnostics. llm-gateway owns inference, the request/attempt ledger, call analysis, routing diagnostics and exports. Neither product should require the other to run. No Gateway navigation link, context-carrying redirect, shared database or new runtime dependency is wanted. Keep Python and SQLite; no language/database migration is in scope.

## Confirmed gaps

| Gap | Source evidence | Intended correction |
| --- | --- | --- |
| Duplicate Gateway calls UI and APIs | web/llm-calls.*, server.py; Gateway has its own calls UI | Retire monitor's Gateway-only calls page, navigation and API implementation after preserving access to existing records |
| Monitor cannot start without a Gateway checkout | gateway_dependency.py, llm_attempts.py, server.py imports | Remove the mandatory runtime checkout dependency, including installation assumptions |
| Gateway coupling extends beyond the UI | statistics_snapshot.py imports the Gateway audit reader for export/validation; full-history query workers serve calls | Separate agent usage snapshots from Gateway call history and remove newly orphaned query machinery |
| Multi-machine history may be visible only through monitor | Monitor admits per-machine snapshots; Gateway's documented personal console reads one ledger | Inventory actual source histories before cutover; implement independent Gateway-owned historical access if required |

## Constraints and open questions

- Preserve existing state symlinks, source/account identities, quotas, accounting arithmetic and retained histories. No deletion of production data is authorized or needed.
- Inspect machine-level Gateway record coverage and the receiving Gateway's historical access before retiring the deployed monitor entry.
- Any snapshot format transition must preserve a readable path for older retained generations and avoid silently dropping agent history.
- No git push authorization exists. Gateway remote updates require dual-remote push/pull under its repository policy; finish local code, review and commits before requesting any necessary publication permission.
- Existing unrelated WIP: monitor main has untracked state; Gateway main has untracked gemini-gateway-src.tar.gz and gemini-gateway.md. Do not change them.

## Execution and acceptance

1. Record gaps and scope (this document), inspect live source coverage with read-only queries, settle the narrow migration design and pass the applicable decision review.
2. Implement independent historical access in Gateway only if source coverage requires it; document its standalone ownership and entry points.
3. Remove monitor's Gateway page/API/dependency and call-history collection while preserving agent statistics and existing stored files.
4. Verify monitor import/start and agent usage/snapshot paths with isolated HOME/state and no accessible Gateway package; verify removed routes/navigation and affected Python/Node tests. Verify Gateway access for each retained source needed by cutover, including request detail and export.
5. Sync current documentation, run applicable change review, create local commits and integrate into local main. Deploy only within applicable authorization and source-transfer policy; report local and live evidence separately.

## State

### Inventory and migration design (decision review passed)

Read-only SSH inventory on MacStudio at 2026-10-12 13:25–13:29 UTC followed each machine's actual `current` pointer. Requests/attempts: macbook 28/27 (schema 7), macmini 120792/92755 (schema 6), macstudio 534013/534395 (schema 10), tencent-webserver-china 7599/7211 (schema 6). These are per-source stored counts, not deduplicated global counts or current traffic rates. A prior exploratory listing sampled a generation by filename; only the pointer-based inventory above supports cutover decisions.

Decision proposal: Gateway gains an optional independent multi-source audit store and Web source configuration, using its existing audit projections and retaining namespaced request identities. A one-time importer accepts the old immutable statistics snapshots, validates the Gateway rows and saves Gateway-owned archives; ongoing collection reads source Gateway ledgers without monitor. Existing single-ledger serving stays the default. Monitor writes a new agent-only statistics extension, reads old agent usage while ignoring retired Gateway payloads, and no longer validates/collects Gateway rows. New publication must still reject shrinking agent usage. Existing source snapshots/ledgers remain untouched; live removal waits for archive coverage and Gateway access verification.

Alternatives: keeping a hidden Gateway reader in monitor violates independent installation; merely linking to MacStudio loses other sources; keeping both full UIs preserves the duplication the user asked to remove. New centralized PostgreSQL or Rust rewrite adds unrelated scope. No runtime proxy or reciprocal service dependency is introduced. Historical import is a migration input, not a recurring dependency on monitor's state layout.

Decision scope: two owned development worktrees, local source/test/docs changes; no provider requests or credential changes; no production data deletion. Existing snapshot schema v1 history remains readable for agent usage; new extension version explicitly removes Gateway fields. Existing dates/IDs/arithmetic and outer generation integrity remain unchanged. Local implementation is reversible by commits; live cutover requires proven retained Gateway access and the applicable deployment/push authorization.

Related decision corpus: both repos' docs/adr and docs/issues searched for llm-call, audit, snapshot, statistics. Monitor 20261004-a902 extraction preserved Gateway ownership; 20261006-d83e calls explorer added multi-source UI and cites four-source 498194/469818 observations; 20261008-f43a request-first loading separated cheap lists from full-history work (32.284s instrumented source); 20261008-d102 query processes isolated rather than removed full-history costs; 20261011-a8d3 prepared view optimized aggregate paths. Gateway 20260915-9c21 described possible Gateway-owned aggregation; 20261006-42cf indexes single-ledger queries. This user's explicit separation request supersedes monitor's ownership of the calls UI/collection, not retained history or accounting correctness.

Unknowns before implementation: exact archive/import schema, Gateway fast-endpoint integration, continuous collection deployment configuration, and live cutover evidence. No performance improvement is claimed from this design alone. Required verification is affected behavior plus one independent decision reviewer and one applicable change reviewer; no expanded performance SLA, test matrix or independent review loop is proposed.

- Recorded: scope, gaps, constraints and acceptance before code changes.
- Implemented locally: product separation and Gateway-owned archive path.
- Pending: final local commit/integration, production history handover and deployment/cutover.
- Isolation: agent-monitor-boundary-20261012 at c16ce83; llm-gateway-boundary-20261012 at 34b45b6. Both worktrees are owned by this session.
- Execution routing: main thread for coupled design/implementation (current, cross-module, inherited model/effort); independent reviewer required for architecture/schema decision and applicable change gate.

Decision review passed after limiting no-GC retention to finite migration/verification. Implementation follows [20261012-b97e](./adr/20261012-b97e-product-boundary-separation.md). Continuous scheduling is not enabled by this change; bounded retention and independent collection continuity remain deployment prerequisites.

## Local implementation and validation

Monitor's Calls assets, APIs, query workers and Gateway checkout imports/configuration are removed. Extension v2 carries agent usage; v1 still supplies agent usage without decoding retired Gateway data. The outer digest and agent retention guard remain. No production snapshots or ledgers have been deleted.

Isolated affected suite: 94 tests passed. Full suite: 847 tests, two pre-existing failures reproduced in a clean HEAD export (account-memory mock omits quota_refresh; benchmark daily-rollup census omits its two synthetic sites); a third failure was the removed Gateway read site's census, now updated. Final affected run: 139 tests, 138 passed and the same baseline daily-rollup census failure; no other failures. Baseline logs are under ~/.local/state/agent-monitor-boundary-20261012/.

Headless Chromium against isolated HOME/state: guide→sessions navigation succeeded; navigation contains four data pages plus guide, empty session state is visible. Actual HTTP returned 200 for guide/sessions and 404 for retired page/list/calls/detail/export routes. This is local fixture verification, not a production cutover.

Gateway change review identified incomplete archive integration for overview section parameters and ID lookup machine selection. Both are fixed; its Python 3.14 environment passed 101 related tests. Actual isolated Chromium loaded the two-source overview, looked up a request on fixture-a and an attempt on fixture-b with colliding IDs, and required a machine when none was selected. Archive overview uses the complete historical projection; this is not a large-history latency improvement.

Read-only transfer of four full snapshots exceeded the 240-second bound without completing a copy; incomplete local transfer files were discarded. A targeted extraction of macbook Gateway rows completed: 28 requests/27 attempts, schema 7, source observation 2026-10-12T13:50:14.322801+00:00. Gateway import/read preserved the complete raw fields; JSON exports matched both counts and two request-detail samples resolved. This is one source, not four-source acceptance. Evidence is local under ~/.local/state/agent-monitor-boundary-20261012/history-verified.json; raw data stays out of Git.

Production handover remains pending for macmini, macstudio and tencent-webserver-china, followed by a final four-source checkpoint. Continuous independent collection is also pending: the new CLI is intentionally single-run; bounded current/previous retention and active-reader protection must precede scheduling. The implementation does not claim production replacement is complete. The old production Monitor entry and every source snapshot/ledger remain intact. Gateway remote delivery needs explicit dual-remote publication and deployment authorization. After that the executor must verify history/detail/export and collection continuity before retiring the Monitor entry; deploy the compatible Hub reader before v2 writers and do not roll back the Hub alone to a v1-only reader.

The two baseline test failures are outside this separation change and tracked as ISSUE-TEST-20261012-bd71 in docs/issues/general.md for the Monitor test maintainer: update the account-memory fixture to accept quota_refresh and classify the two synthetic benchmark rollup sites, then rerun those tests. They do not justify claiming the full suite passed.

Final change review passed after the two Gateway fixes; no unaddressed review findings remain. Baseline test maintenance and production handover remain explicitly pending as described above.
