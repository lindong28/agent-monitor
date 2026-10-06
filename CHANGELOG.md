# Changelog

## 2026-10-06

- Group overview cost summaries and their existing trend on one panel. Keep today's, this week's and the selected range's labels distinct, with the full-width account quota table below. The existing blue theme, machine warnings, accounting and drill-down behavior are preserved.

## 2026-10-04

- Extracted the CLI, Web dashboard, backend and network diagnostics into the independent agent-monitor project; the canonical command is now `agent-monitor`.
- Installation creates this project's Python environment and supports `--no-services` for staging runtime, assets and CLI links before service cutover.
- Renamed runtime environment controls and macOS services; preserved usage history formats, quota behavior and accounting. Historical deployment evidence remains under its original tt-web identity.
