#!/usr/bin/env bash
# agent-monitor status — read-only status for web and the default rollup schedule.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
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
  "$python" "$ROOT/rollup_service.py" status
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "$SERVICE" in
  all)
    result=0
    status_web || result=1
    status_rollup_daemon || result=1
    status_connector || result=1
    exit "$result"
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
