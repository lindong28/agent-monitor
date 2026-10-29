#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT_DIR"
exec "${AGENT_MONITOR_PYTHON:-python3}" -m agent_monitor.runtime.deployment_guard install "$ROOT_DIR" -- "$@"
