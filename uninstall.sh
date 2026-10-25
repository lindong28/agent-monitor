#!/usr/bin/env bash
# agent-monitor uninstall — remove agent-monitor services and ~/.local/bin symlinks.
# Keeps source, state/, and vendored assets. Idempotent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVICE="${1:-all}"
ROLLUP_LABEL="com.agent-monitor.rollup"
ROLLUP_PLIST="$HOME/Library/LaunchAgents/$ROLLUP_LABEL.plist"

usage() {
  echo "usage: ./uninstall.sh [web|rollup-daemon]" >&2
}

uninstall_web() {
  local python
  python="$(
    for candidate in "$ROOT/.venv/bin/python"; do
      [ -x "$candidate" ] && { echo "$candidate"; exit 0; }
    done
    command -v python3
  )"
  if command -v launchctl >/dev/null 2>&1; then
    "$python" "$ROOT/hub.py" uninstall-web
  fi
  "$ROOT/agent-monitor" stop 2>/dev/null || true

  for link in "$HOME/.local/bin/agent-monitor" "$HOME/.local/bin/ip-check"; do
    if [ -L "$link" ]; then
      rm -f "$link"
      echo "→ removed $link"
    else
      echo "✓ $link absent"
    fi
  done
  echo "  (source + state/ kept)"
}

uninstall_rollup_daemon() {
  launchctl bootout "gui/$(id -u)/$ROLLUP_LABEL" >/dev/null 2>&1 || true
  if [ -f "$ROLLUP_PLIST" ]; then
    rm -f "$ROLLUP_PLIST"
    echo "→ removed $ROLLUP_PLIST"
  else
    echo "✓ $ROLLUP_PLIST absent"
  fi
  echo "  (rollup.db and logs kept in $ROOT/state/)"
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "$SERVICE" in
  all)
    uninstall_web
    uninstall_rollup_daemon
    ;;
  web)
    uninstall_web
    ;;
  rollup-daemon)
    uninstall_rollup_daemon
    ;;
  *)
    usage
    exit 2
    ;;
esac
