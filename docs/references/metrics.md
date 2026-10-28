# Accounting and parser contracts

These are maintenance boundaries for the current implementation, not provider billing guarantees. `parsers/` normalizes observed agent logs, `agent_monitor/ingestion/usage_archive.py` retains observations, `agent_monitor/accounting/rollup.py` maintains aggregates, and `token_cost.py` derives known-cost subtotals. Tests use synthetic observations; they do not establish live provider behavior.

## Usage and cost

Claude observations with the same response identity are deduplicated. A later usage vector may replace an earlier one only when no component decreases; incomparable vectors remain source errors rather than a componentwise invented maximum. Codex cumulative deltas require the previous counter at the inherited history boundary. Copied parent history does not count as child use, and current parent EOF is not a historical boundary. Missing ancestry, counters or models remain unknown; later independently known deltas can remain usable.

Known costs are subtotals over priceable observations. Known zero is distinct from unknown: costs `[2, 0, unknown]` have known subtotal 2 and one unknown observation; all-unknown does not become zero. Source scan completeness, price coverage, admitted machine coverage, retained date coverage and detailed-session retention are separate dimensions. Estimates are not invoices or reconstructed historical tariffs. Arithmetic changes to `token_cost.py` must preserve its shared contract and corresponding tests.

Aggregate authority is independent of session retention: `daily_usage_rollup` owns usage keys, with legacy project-derived fallback only for absent keys; project queries use `daily_rollup`. An identity blocker may prevent project attribution while independent usage totals remain available. Existing archive keys need explicit correction/replay when parser behavior changes; a parser fix alone does not repair already retained records. Date boundaries use Asia/Shanghai.

For nominal pricing, [pricing_fetcher.py](../../agent_monitor/accounting/pricing_fetcher.py) checks supported service-context values before applying rates. Its Claude Code path explicitly admits `inference_geo=not_available`; that sentinel does not name a region or authorize arbitrary future values. Unsupported context remains unpriced with `unsupported_service_context` provenance. Recorded costs follow the separate recorded-cost validation path.

## Account attribution and quota plans

[parsers/accounts.py](../../parsers/accounts.py) reads current local account metadata. In the default-home passive path, [quota_service.rate_limits](../../agent_monitor/quota/quota_service.py) pairs that metadata with a stored quota observation. The stamp does not prove which account produced an older observation after a sign-in change. Managed Codex homes instead supply their registry profile identity through `managed_codex_rate_limits`; their credential plan is unavailable. Cross-account session-link exclusions remain a separate safeguard described in [architecture](../public-architecture.md#usage-path). These quota identities must not be treated as retrospective usage or billing attribution.

`quota_service.rate_limit_block` publishes two independent facts: `reading_plan` comes from `RateLimits.plan`, carried with the quota event, while `credential_plan` comes from the supplied account metadata. Their clocks can differ. An elapsed reset does not create a fresh observation: the Codex parser retains the stored percentage and plan. A mismatch does not establish which source is stale, and one available source is not evidence that two sources agree.

[quota_view.plan_to_show](../../agent_monitor/quota/quota_view.py) derives the display value from the reading plan first, then the credential plan. It uses legacy `account_plan` only when both raw keys are absent; present-but-null keys do not permit that fallback. [web/quota.js](../../web/quota.js) reports disagreement only for a known account with two nonempty string sources. [Account memory](../../agent_monitor/quota/account_memory.py) persists the derived `account_plan`, not the raw pair; remembered rows expose both raw fields as null. Adding them to persisted records would require a separate schema change.

Regression entrypoints are [test_account_identity.py](../../tests/test_account_identity.py), [test_codex_rate_limits.py](../../tests/test_codex_rate_limits.py), [test_overview_api_shape.py](../../tests/test_overview_api_shape.py) and [test_account_memory.py](../../tests/test_account_memory.py), using the [isolated development procedure](../../CONTRIBUTING.md#development-setup). They distinguish reading/credential disagreement, a missing source, legacy key absence and remembered observations with synthetic inputs; they do not verify a real provider account's current plan.

Earlier source comments cited `ADR-024` and `20260822-586a`, whose records are not included in this repository. This section records the current implementation and its regression boundaries, not a verification of those historical approvals or a new waiver of account-attribution risk.

## Account-read diagnostics

[codex_accounts.py](../../agent_monitor/accounts/codex_accounts.py) maps a fixed set of `account/read` error messages with JSON-RPC code `-32603` to bounded diagnostic reasons. Unrecognized messages keep the generic reason and are not retained verbatim. The mapping explains a failed request; it does not decide admission, retry or recovery. [test_account_read_diagnostics.py](../../tests/test_account_read_diagnostics.py) covers this local mapping with synthetic responses.

The implementation records Codex 0.162.1 commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d` as its source pin. That existing pin is not a fresh upstream verification here. The former `20261023-b72e` ADR pointer has no included record; it is not required to find or maintain this implementation boundary.

## Project identity

Project aliases canonicalize stored paths consistently for pivot axes, filter values and filter matching. Equivalent-looking home-relative paths from different machines may share a row; this does not prove the directories contain the same work. Preserve the machine dimension so readers can separate them. Do not change canonicalization in one consumer without checking the others, or silently rewrite retained source identities. `tests/test_project_alias.py` covers the shared mapping.

## Codex paginated child baseline

`parsers/codex.py` permits an initial zero baseline only for a discrete source-verified version set, `history_mode=paginated`, `thread_source=subagent`, no `history_base`, and a settings checkpoint at the explicit `subagent_history_start_ordinal`. The relevant upstream chain filters parent TokenCount before seeding the child, records the boundary, and starts a fresh counter context. A later resume checkpoint, arbitrary ordinal or `total == last` does not establish zero. Never reset an existing counter.

| Codex version | Official source commit | Checkpoint owner |
| --- | --- | --- |
| 0.147.0 | `be6e8eac029b183056b7e4402879f15d2c85f61b` | Absent `thread_id` allowed only at the exact initial boundary; explicit null/empty/foreign owners do not qualify |
| 0.152.0 | `316795b3cf2a45e90d121d9f46499d4658b2645c` | Must match metadata id |
| 0.153.4 | `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` | Must match metadata id |
| 0.154.0 | `6b9826e3aa83b1a5947db50f4332cb9c65f1b340` | Must match metadata id |
| 0.158.0 | `064c6b8c737f5b41d171fdda80bd9ef10ad06eb3` | Must match metadata id |
| 0.162.1 | `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d` | Must match metadata id |

Source repository: [openai/codex](https://github.com/openai/codex). At each pinned commit, inspect `codex-rs/core/src/agent/control/spawn.rs`, `codex-rs/core/src/session/mod.rs` and `codex-rs/thread-store/src/live_thread.rs` together with counter context reconstruction. This table carries the existing parser's source pins, not a new verification of upstream releases. Extending `_PAGINATED_ZERO_BASELINE_VERSIONS` requires checking that full chain and updating tests/table together; the version set is not an interval or a future-version promise.

## Validation and observations

Run the affected modules with the isolated HOME procedure in [CONTRIBUTING](../../CONTRIBUTING.md): `test_metric_accounting.py`, `test_source_metric_repairs.py`, `test_codex_forks.py`, `test_history_contracts.py` and `test_token_cost.py`. Include repeated observations, unresolved ancestry, known-zero and unknown-price cases as relevant. Snapshot schema admission establishes identity/retention, not completeness of every provider call.

Quota is a timestamped account observation, not usage or a current permission to send. An elapsed reset timestamp does not prove recovery. Existing account applicability, pauses and uncertain-send outcomes survive restarts; do not clear them as part of an accounting change. For operational diagnostics, see [service diagnostics](../operations/services.md).
