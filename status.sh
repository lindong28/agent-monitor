#!/usr/bin/env bash
# agent-monitor status — read-only status for web and the default rollup schedule.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
SERVICE="${1:-all}"

usage() {
  echo "usage: ./status.sh [web|rollup-daemon|connector]" >&2
}

status_connector() {
  if [ "$(uname -s)" = Darwin ]; then
    "$ROOT/agent-monitor" connect status
  else
    echo "connector: no startup definition installed on this platform"
  fi
}

status_web() {
  "$ROOT/agent-monitor" status
}

status_rollup_daemon() {
  local python
  python="$ROOT/.venv/bin/python"
  [ -x "$python" ] || python="$(command -v python3)"
  "$python" -m agent_monitor.runtime.rollup_service status
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "$SERVICE" in
  all)
    exec "$ROOT/agent-monitor" service status
    ;;
  web)
    status_web
    ;;
  rollup-daemon)
    status_rollup_daemon
    ;;
  connector)
    status_connector
    ;;
  *)
    usage
    exit 2
    ;;
esac
