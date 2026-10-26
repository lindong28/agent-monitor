#!/usr/bin/env bash
# agent-monitor uninstall — remove agent-monitor services and ~/.local/bin symlinks.
# Keeps source, state/, and vendored assets. Idempotent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVICE="${1:-all}"

PYTHON="${AGENT_MONITOR_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
if ! "$PYTHON" "$ROOT/deployment_guard.py" held "$ROOT"; then
  exec "$PYTHON" "$ROOT/deployment_guard.py" run "$ROOT" -- bash "$ROOT/uninstall.sh" "$@"
fi

usage() {
  echo "usage: ./uninstall.sh [web|rollup-daemon|connector]" >&2
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
    "$python" "$ROOT/hub.py" uninstall-web || return $?
  fi
  if [ "$("$python" "$ROOT/hub.py" role)" = client ]; then
    "$ROOT/agent-monitor" local stop || return $?
  else
    "$ROOT/agent-monitor" stop || return $?
  fi

  for link in "$HOME/.local/bin/agent-monitor" "$HOME/.local/bin/ip-check"; do
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$ROOT/$(basename "$link")" ]; then
      rm -f "$link"
      echo "→ removed $link"
    else
      echo "✓ $link absent or not owned by this checkout; preserved"
    fi
  done
  echo "  (source + state/ kept)"
}

uninstall_rollup_daemon() {
  local python
  python="$ROOT/.venv/bin/python"
  [ -x "$python" ] || python="$(command -v python3)"
  "$python" "$ROOT/rollup_service.py" uninstall
}

uninstall_connector() {
  if [ "$(uname -s)" = Darwin ]; then
    "$ROOT/agent-monitor" connect stop
  else
    echo "connector: no managed service on this platform"
  fi
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "$SERVICE" in
  all)
    result=0
    uninstall_web || result=1
    uninstall_rollup_daemon || result=1
    uninstall_connector || result=1
    exit "$result"
    ;;
  web)
    uninstall_web
    ;;
  rollup-daemon)
    uninstall_rollup_daemon
    ;;
  connector)
    uninstall_connector
    ;;
  *)
    usage
    exit 2
    ;;
esac
