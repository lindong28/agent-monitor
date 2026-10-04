# Changelog

## 2026-10-04

- Extracted the CLI, Web dashboard, backend and network diagnostics into the independent agent-monitor project; the canonical command is now `agent-monitor`.
- Installation creates this project's Python environment and supports `--no-services` for staging runtime, assets and CLI links before service cutover.
- Renamed runtime environment controls and macOS services; preserved usage history formats, quota behavior and accounting. Historical deployment evidence remains under its original tt-web identity.
