# Changelog

## 2026-10-08

- Preserve Codex quota readings obtained automatically after sending or by quota-only refresh, independently of replaceable operation records. Later failed queries and restarts retain the last successful reading and its original observation time in both the account card and quota table.
- Batch request candidates before reading their child attempts, avoiding repeated full-table scans when Hub sources still use older exporters without lookup indexes.
- Open LLM Calls with a request page first. Load full-range totals, costs, independent attempts and complete filter choices on explicit action; preserve filtering, detail, pagination and exports. Hub snapshots gain an optional request-time index during export.
- Isolate Hub full-history calls queries and JSON encoding from detail requests, with at most two concurrent query workers. Preserve complete exports and existing response semantics.
- Fix stale export cleanup when macOS `/tmp` is a symlink, retaining the existing age, owner and filename restrictions without following child symlinks.
- Isolate Hub snapshot synchronization in a supervised process so its full-history export and publication checks do not share Python execution with HTTP requests. Preserve progressive refresh status and account-memory coordination; first admission of each new snapshot still validates in the HTTP process.
- Keep Hub readers available during snapshot history validation; recheck competing publications before switching generations. Build detail lookup indexes at export and avoid duplicate validation of identical transfer manifests.
- Accept exact Gateway schema 9/10 statistics snapshots, including schema 10's missing-usage reason. Read only the selected Hub request and its complete attempt chain for details, preserving source metadata, ambiguity checks and time-window semantics.
- Add historical routing diagnostics to LLM request details: distinguish explicit-pin resolution, first attempts, same-route retries and route switches; explain candidate eligibility separately from recorded attempts. Preserve machine identity, raw evidence and unknown values without changing Gateway execution or collection.

## 2026-10-07

- Use the newest matching Codex Web-action or machine observation in the quota table, with explicit source labels and unchanged historical deletion records. Show relative reset times, distinguish expired records from observed resets, simplify successful batch/card content, and preserve historical disclosure state during refresh.
- Discover Codex batch accounts automatically from current and historical login records; remove the manual account-add step. Deduplicate identities, surface incomplete records, and exclude removed identities from subsequent sends while preserving batch results.
- Add one-click Codex batches for all saved accounts, persistent progress, bounded parallel processing and independent authorization. Refreshing or repeating a request does not resend the batch; continue only definitely unsent accounts, or explicitly start a new round. Preserve message results when refreshing quota.
- Add provider filters and account/plan/machine search to Overview quotas, with matched records, current/history account counts and separate unknown-machine counts. Preserve original windows, readings, history actions and collection notices; filtering does not change provider queries.
- Replace inline session expansion with a navigable detail page: retained-session summaries, model filtering, paginated usage records and expandable source identifiers. Preserve list filters and page on return; keep unknown costs, estimates and observed time spans explicit.
- Add explicit Codex account login and single-message actions to the overview. Official device authorization, isolated saved logins, account identity checks, cancellation and quota-only refresh avoid switching the regular CLI account. Display server-reported reset times and distinguish completed, failed and uncertain sends. No automatic weekly sending is introduced.

## 2026-10-06

- Add machine-aware request diagnostics and complete filtered JSON exports to LLM Calls. Compact summaries and a filter rail keep requests prominent; cost and attempt audit details remain expandable. Unknown costs, source gaps and independent request/attempt time windows remain explicit.
- Group overview cost summaries and their existing trend on one panel. Keep today's, this week's and the selected range's labels distinct, with the full-width account quota table below. The existing blue theme, machine warnings, accounting and drill-down behavior are preserved.

## 2026-10-04

- Extracted the CLI, Web dashboard, backend and network diagnostics into the independent agent-monitor project; the canonical command is now `agent-monitor`.
- Installation creates this project's Python environment and supports `--no-services` for staging runtime, assets and CLI links before service cutover.
- Renamed runtime environment controls and macOS services; preserved usage history formats, quota behavior and accounting. Historical deployment evidence remains under its original tt-web identity.
