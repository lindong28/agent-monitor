#!/usr/bin/env bash
# agent-monitor uninstall — remove agent-monitor services and ~/.local/bin symlinks.
# Keeps source, state/, and vendored assets. Idempotent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVICE="${1:-all}"
ROLLUP_LABEL="com.agent-monitor.rollup"
ROLLUP_PLIST="$HOME/Library/LaunchAgents/$ROLLUP_LABEL.plist"

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
  "$ROOT/agent-monitor" stop || return $?

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
  if ! command -v launchctl >/dev/null 2>&1; then
    echo "rollup schedule: launchd unavailable; no service changed"
    return 0
  fi
  python3 - "$ROOT" "$ROLLUP_PLIST" "$ROLLUP_LABEL" <<'PY' || return $?
import pathlib, plistlib, subprocess, sys, os
root, dest, label = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
expected = str(root / 'agent-monitor')
if dest.exists() or dest.is_symlink():
    try:
        value = plistlib.loads(dest.read_bytes())
        owned = value.get('Label') == label and value.get('ProgramArguments', [])[:1] == [expected]
    except (OSError, ValueError, TypeError):
        owned = False
    if not owned:
        sys.exit('rollup schedule: foreign or unknown startup definition preserved; use its owning checkout')
target = 'gui/%s/%s' % (os.getuid(), label)
result = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True, timeout=15)
if result.returncode == 0:
    programs = [line.strip().removeprefix('program = ') for line in result.stdout.splitlines() if line.strip().startswith('program = ')]
    if programs != [expected]:
        sys.exit('rollup schedule: foreign or unknown loaded job preserved; use its owning checkout')
    subprocess.run(['launchctl', 'bootout', target], check=True, timeout=15)
elif result.returncode != 113:
    sys.exit('rollup schedule: cannot inspect loaded job; startup definition preserved')
dest.unlink(missing_ok=True)
print('rollup schedule: owned integration removed; collected data and locks retained')
PY
  echo "  (rollup.db and logs kept in $ROOT/state/)"
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
