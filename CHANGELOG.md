# Changelog

## 2026-10-07

- Add one-click Codex batches for all saved accounts, persistent progress, bounded parallel processing and independent authorization. Refreshing or repeating a request does not resend the batch; continue only definitely unsent accounts, or explicitly start a new round. Preserve message results when refreshing quota.
- Replace inline session expansion with a navigable detail page: retained-session summaries, model filtering, paginated usage records and expandable source identifiers. Preserve list filters and page on return; keep unknown costs, estimates and observed time spans explicit.
- Add explicit Codex account login and single-message actions to the overview. Official device authorization, isolated saved logins, account identity checks, cancellation and quota-only refresh avoid switching the regular CLI account. Display server-reported reset times and distinguish completed, failed and uncertain sends. No automatic weekly sending is introduced.

## 2026-10-06

- Add machine-aware request diagnostics and complete filtered JSON exports to LLM Calls. Compact summaries and a filter rail keep requests prominent; cost and attempt audit details remain expandable. Unknown costs, source gaps and independent request/attempt time windows remain explicit.
- Group overview cost summaries and their existing trend on one panel. Keep today's, this week's and the selected range's labels distinct, with the full-width account quota table below. The existing blue theme, machine warnings, accounting and drill-down behavior are preserved.

## 2026-10-04

- Extracted the CLI, Web dashboard, backend and network diagnostics into the independent agent-monitor project; the canonical command is now `agent-monitor`.
- Installation creates this project's Python environment and supports `--no-services` for staging runtime, assets and CLI links before service cutover.
- Renamed runtime environment controls and macOS services; preserved usage history formats, quota behavior and accounting. Historical deployment evidence remains under its original tt-web identity.
