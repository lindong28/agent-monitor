# Accounting and parser contracts

These are maintenance boundaries for the current implementation, not provider billing guarantees. `parsers/` normalizes observed agent logs, `usage_archive.py` retains observations, `rollup.py` maintains aggregates, and `token_cost.py` derives known-cost subtotals. Tests use synthetic observations; they do not establish live provider behavior.

## Usage and cost

Claude observations with the same response identity are deduplicated. A later usage vector may replace an earlier one only when no component decreases; incomparable vectors remain source errors rather than a componentwise invented maximum. Codex cumulative deltas require the previous counter at the inherited history boundary. Copied parent history does not count as child use, and current parent EOF is not a historical boundary. Missing ancestry, counters or models remain unknown; later independently known deltas can remain usable.

Known costs are subtotals over priceable observations. Known zero is distinct from unknown: costs `[2, 0, unknown]` have known subtotal 2 and one unknown observation; all-unknown does not become zero. Source scan completeness, price coverage, admitted machine coverage, retained date coverage and detailed-session retention are separate dimensions. Estimates are not invoices or reconstructed historical tariffs. Arithmetic changes to `token_cost.py` must preserve its shared contract and corresponding tests.

Aggregate authority is independent of session retention: `daily_usage_rollup` owns usage keys, with legacy project-derived fallback only for absent keys; project queries use `daily_rollup`. An identity blocker may prevent project attribution while independent usage totals remain available. Existing archive keys need explicit correction/replay when parser behavior changes; a parser fix alone does not repair already retained records. Date boundaries use Asia/Shanghai.

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
