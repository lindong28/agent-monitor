# Architecture decisions

| Decision | Status |
| --- | --- |
| [20261014-58e1: usage evidence and nominal valuation](20261014-58e1-usage-evidence.md) | Accepted; quality, mode, provenance and strict statistics v3 with legacy readers |
| [20261014-f20a: account source contracts](20261014-f20a-account-source-contracts.md) | Accepted; selected identities, target quota bucket and restricted external capabilities |
| [20261014-86ab: runtime ownership](20261014-86ab-runtime-ownership.md) | Accepted; explicit mutation POSTs and process identity/cleanup boundaries |
| [20261014-97cd: storage and publication](20261014-97cd-storage-publication.md) | Accepted; locked private reads, commit timestamp, automatic freshness and remote leases |
| [20261014-a8de: quota parent lifetime](20261014-a8de-quota-parent-lifetime.md) | Accepted; dedicated parent pipe and unchanged total budget for detached readers |
| [20261014-c82a: Ark top-level areas and documentation](20261014-c82a-ark-top-navigation.md) | Accepted; console/guide navigation, local page/help search and copyable guide commands with console context retained |
| [20261014-7d2f: Ark Console visual style](20261014-7d2f-ark-console-style.md) | Accepted design; independent reusable template and shared Monitor visual layer, product semantics preserved |
| [20261012-6a92: Web UI workflows](20261012-6a92-ui-workflows.md) | Accepted; explicit scope, calendar comparison, event-filtered session drilldown and quota sorting |
| [20261011-7c3a: account round recovery](20261011-7c3a-account-round-recovery.md) | Accepted; scoped HTTP transport, safe failures and quota recovery without message replay |
| [20261011-a8d3: prepared aggregate query views](20261011-a8d3-prepared-query-view.md) | Accepted; leased admission outside requests, short publication lock and concurrent Explore loading |
| [20261011-64e1: independent quota refresh](20261011-64e1-independent-quota-refresh.md) | Accepted; quota queries and display no longer wait for statistics, membership remains snapshot-owned |
| [20261011-a72c: reap account children before restart](20261011-a72c-child-process-reaping.md) | Accepted; bounded account drain for reexec and SIGTERM, no automatic message replay |
| [20261008-f7c1: Codex authorization queue](20261008-f7c1-codex-authorization-queue.md) | Accepted; one manual authorization at a time, known rate limits stop pending code requests |
| [20261008-e92b: retain successful Codex quota observations](20261008-e92b-retain-codex-quota.md) | Accepted; independent latest reading survives operation replacement, deployment separate |
| [20261008-f43a: requests before optional analysis](20261008-f43a-request-first-loading.md) | Accepted by user; partially supersedes d83e/H2 loading order, deployment separate |
| [20261008-d102: isolate full-history HTTP queries](20261008-d102-query-process.md) | Accepted; two concurrent query workers, deployment separate |
| [20261008-c4e2: isolate snapshot synchronization](20261008-c4e2-sync-process.md) | Accepted for local implementation; live performance and deployment separate |
| [20261008-a61f: historical routing diagnostics](20261008-a61f-routing-diagnostics.md) | Accepted for local implementation; read-only evidence, publication and deployment separate |
| [20261007-a62e: Codex quota display observations](20261007-a62e-codex-quota-display.md) | Accepted; combines observations for display while preserving machine storage and deletion semantics |
| [20261007-d2af: known Codex account directory](20261007-d2af-codex-known-accounts.md) | Accepted; replaces manual-add membership in b923 |
| [20261007-b923: one-click Codex account batches](20261007-b923-codex-account-batches.md) | Accepted for local implementation; real account acceptance and deployment separate |
| [20261007-b45f: quota explorer](20261007-b45f-quota-explorer.md) | Accepted for local implementation; publication and deployment separate |
| [20261007-e781: navigable session detail](20261007-e781-session-detail.md) | Accepted for local implementation; publication and deployment separate |
| [20261007-c814: Codex account actions](20261007-c814-codex-account-actions.md) | Accepted for local implementation; real account acceptance and deployment separate |
| [20261006-d83e: machine-aware call exploration](20261006-d83e-calls-explorer.md) | Accepted for local implementation; publication and deployment separate |
| [20261006-91da: overview template reuse](20261006-91da-overview-template-reuse.md) | Accepted for local implementation; production verification separate |
| [20261004-a902: independent agent-monitor project](20261004-a902-standalone-project.md) | Accepted for extraction; deployment evidence recorded separately |

Earlier decisions remain in the source repository at the immutable commit linked by the extraction decision and historical references.

- [20261012-b97e — Product boundary separation](20261012-b97e-product-boundary-separation.md): independent Monitor and Gateway with preserved audit history.

- [20261013-e7a4 — Published main deployment](20261013-e7a4-published-main-deployment.md): accepted; scoped production publication and independent project entrypoints.

- [20261013-42b6 — Usage observation correction](20261013-42b6-usage-observation-correction.md): preserve actual usage across pagination and streaming, with retained observations and visible unknowns.

- [20261013-b4d8 — Session indexes and runtime observations](20261013-b4d8-runtime-performance.md): accepted; bounded process-local diagnostics and preserved query semantics.

- [20261014-a7c2 — Known-cost subtotals with pricing coverage](20261014-a7c2-known-cost-coverage.md): accepted; implementation and production verification tracked separately.

- [20261014-b8d3 — Attribute Codex usage to its actual history and turn](20261014-b8d3-codex-history-attribution.md): accepted; implementation and production verification tracked separately.

- [20261014-c9e4 — Require applicable rates before declaring cost known](20261014-c9e4-complete-pricing.md): accepted; implementation and production verification tracked separately.

- [20261014-da15 — Preserve quota observations and display reported windows](20261014-da15-reported-quota-windows.md): accepted; implementation and production verification tracked separately.

- [20261014-eb26 — Separate network heuristics from missing evidence](20261014-eb26-network-evidence-coverage.md): accepted; implementation and production verification tracked separately.

- [20261014-fc37 — Count only events not successfully emitted](20261014-fc37-diagnostic-event-loss.md): accepted; implementation and production verification tracked separately.

- [20261014-0d48 — Report orphan disposition using replay authority](20261014-0d48-authoritative-orphan-disposition.md): accepted; implementation and production verification tracked separately.
