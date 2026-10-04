#!/usr/bin/env bash
# agent-monitor status — read-only status for web and the default rollup schedule.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVICE="${1:-all}"
ROLLUP_LABEL="com.agent-monitor.rollup"
ROLLUP_PLIST="$HOME/Library/LaunchAgents/$ROLLUP_LABEL.plist"
ROLLUP_LOG="$ROOT/state/rollup-daemon.log"

usage() {
  echo "usage: ./status.sh [web|rollup-daemon]" >&2
}

status_web() {
  "$ROOT/agent-monitor" status
}

status_rollup_daemon() {
  local info pid last_exit interval
  info="$(launchctl print "gui/$(id -u)/$ROLLUP_LABEL" 2>/dev/null || true)"
  if [ -z "$info" ]; then
    if [ -f "$ROLLUP_PLIST" ]; then
      echo "$ROLLUP_LABEL installed but not loaded: plist=$ROLLUP_PLIST"
    else
      echo "$ROLLUP_LABEL not installed"
    fi
    return 0
  fi
  pid="$(printf '%s\n' "$info" | awk -F'= ' '/pid =/{print $2; exit}')"
  last_exit="$(printf '%s\n' "$info" | awk -F'= ' '/last exit code =/{print $2; exit}')"
  interval="$(printf '%s\n' "$info" | awk -F'= ' '/^[[:space:]]*run interval =/{print $2; exit}')"
  echo "$ROLLUP_LABEL loaded: interval=${interval:-unknown} pid=${pid:-not running} last_exit=${last_exit:-unknown} plist=$ROLLUP_PLIST log=$ROLLUP_LOG"
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "$SERVICE" in
  all)
    status_web
    status_rollup_daemon
    ;;
  web)
    status_web
    ;;
  rollup-daemon)
    status_rollup_daemon
    ;;
  *)
    usage
    exit 2
    ;;
esac
