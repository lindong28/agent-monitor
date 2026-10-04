#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT_DIR/lib/install-output.sh"
BIN_DIR="$HOME/.local/bin"
VENDOR_DIR="$ROOT_DIR/web/vendor"
CHART_FILE="$VENDOR_DIR/chart.umd.min.js"
CHART_URL="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
# web/vendor/ is gitignored, so the stylesheet's typefaces are fetched here like
# Chart.js is. They are not decoration: without them the page falls back to the
# system stack, which renders fine and therefore hides the regression.
FONT_DIR="$VENDOR_DIR/fonts"
FONT_FILES=(
  "ibm-plex-sans:ibm-plex-sans-latin-400-normal.woff2"
  "ibm-plex-sans:ibm-plex-sans-latin-500-normal.woff2"
  "ibm-plex-sans:ibm-plex-sans-latin-600-normal.woff2"
  "ibm-plex-sans:ibm-plex-sans-latin-400-italic.woff2"
  "ibm-plex-mono:ibm-plex-mono-latin-400-normal.woff2"
)
FONT_LICENSE_URL="https://raw.githubusercontent.com/IBM/plex/master/LICENSE.txt"
NO_SERVICES=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-services) NO_SERVICES=1 ;;
    *) ARGS+=("$arg") ;;
  esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}
SERVICE="${1:-web}"
ROLLUP_LABEL="com.agent-monitor.rollup"
ROLLUP_PLIST="$HOME/Library/LaunchAgents/$ROLLUP_LABEL.plist"
ROLLUP_LOG="$ROOT_DIR/state/rollup-daemon.log"
ROLLUP_INTERVAL_SECONDS="${AGENT_MONITOR_ROLLUP_INTERVAL_SECONDS:-3600}"
ROLLUP_COMMAND="refresh"
VENV_PY="$ROOT_DIR/.venv/bin/python"

usage() {
  echo "usage: ./install.sh [web|rollup-daemon|hub MACHINE] [--no-services]" >&2
}

install_runtime() {
  local runtime_state=present digest marker="$ROOT_DIR/.venv/.agent-monitor-requirements.sha256"
  if [ ! -x "$VENV_PY" ]; then
    "${AGENT_MONITOR_PYTHON:-python3}" -m venv "$ROOT_DIR/.venv"
    runtime_state=installed
  fi
  digest="$("$VENV_PY" - "$ROOT_DIR/requirements.txt" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
  if [ ! -f "$marker" ] || [ "$(cat "$marker")" != "$digest" ] \
      || ! "$VENV_PY" -c 'import requests, colorama' >/dev/null 2>&1; then
    if ! "$VENV_PY" -m pip install --disable-pip-version-check -r "$ROOT_DIR/requirements.txt"; then
      echo "agent-monitor runtime: failed; CLI/assets/services were not installed. Resolve the pip error and rerun ./install.sh." >&2
      emit_install_ledger artifact "agent-monitor Python runtime" failed "dependency installation failed; rerun ./install.sh"
      return 1
    fi
    printf '%s\n' "$digest" > "$marker"
    [ "$runtime_state" = installed ] || runtime_state=updated
  fi
  echo "agent-monitor runtime: $runtime_state ($VENV_PY)"
  emit_install_ledger artifact "agent-monitor Python runtime" "$runtime_state" "$VENV_PY"
}

install_rollup_daemon() {
  if [ "$(uname -s)" != Darwin ]; then
    echo "→ agent-monitor rollup: skipped on $(uname -s) (requires macOS launchd)"
    emit_install_ledger service "agent-monitor rollup" skipped "Platform: macOS-only launchd schedule"
    return 0
  fi
  if ! command -v launchctl >/dev/null 2>&1; then
    echo "✗ agent-monitor rollup: launchctl unavailable; run this installer on a macOS host with launchd" >&2
    emit_install_ledger service "agent-monitor rollup" failed "launchctl unavailable"
    return 1
  fi
  local rollup_state loaded=0 info program candidate
  info="$(launchctl print "gui/$(id -u)/$ROLLUP_LABEL" 2>/dev/null)" && loaded=1
  if [ "$loaded" = 1 ]; then
    program="$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*program = //p' | head -n 1)"
    if [ "$program" != "$ROOT_DIR/agent-monitor" ]; then
      echo "✗ agent-monitor rollup: loaded job owner is ${program:-unknown}; inspect launchctl print gui/$(id -u)/$ROLLUP_LABEL and uninstall from that owner first" >&2
      emit_install_ledger service "agent-monitor rollup" failed "loaded job ownership conflict; incumbent preserved"
      return 1
    fi
  fi
  mkdir -p "$ROOT_DIR/state" "$HOME/Library/LaunchAgents"
  chmod +x "$ROOT_DIR/agent-monitor"
  candidate="$(mktemp "$ROLLUP_PLIST.XXXXXX")"
  if ! rollup_state="$(ROLLUP_LABEL="$ROLLUP_LABEL" \
  ROLLUP_PLIST="$ROLLUP_PLIST" \
  ROLLUP_CANDIDATE="$candidate" \
  ROLLUP_LOG="$ROLLUP_LOG" \
  ROLLUP_INTERVAL_SECONDS="$ROLLUP_INTERVAL_SECONDS" \
  ROLLUP_COMMAND="$ROLLUP_COMMAND" \
  ROOT_DIR="$ROOT_DIR" \
  python3 - <<'PY'
import os
import plistlib
import sys
from pathlib import Path

root = Path(os.environ["ROOT_DIR"])
interval = int(os.environ["ROLLUP_INTERVAL_SECONDS"])
if interval < 1:
    sys.exit("agent-monitor rollup: interval must be a positive number of seconds")
plist = {
    "Label": os.environ["ROLLUP_LABEL"],
    "ProgramArguments": [str(root / "agent-monitor"), os.environ["ROLLUP_COMMAND"]],
    "RunAtLoad": True,
    "StartInterval": interval,
    "WorkingDirectory": str(root),
    "StandardOutPath": os.environ["ROLLUP_LOG"],
    "StandardErrorPath": os.environ["ROLLUP_LOG"],
    "EnvironmentVariables": {"PYTHONPATH": str(root), "LLM_GATEWAY_ROOT": str(Path(os.environ.get("LLM_GATEWAY_ROOT", Path.home() / "research/llm-gateway")).expanduser().resolve())},
}
dest = Path(os.environ["ROLLUP_PLIST"])
state = "installed"
if dest.exists() or dest.is_symlink():
    old = plistlib.loads(dest.read_bytes())
    if old.get("ProgramArguments", [])[:1] != plist["ProgramArguments"][:1]:
        sys.exit(f"agent-monitor rollup: {dest} belongs to {old.get('ProgramArguments', [])[:1]}; uninstall from that owner first")
    state = "present" if old == plist else "updated"
Path(os.environ["ROLLUP_CANDIDATE"]).write_bytes(plistlib.dumps(plist, sort_keys=False))
print(state)
PY
)"; then
    rm -f "$candidate"
    emit_install_ledger service "agent-monitor rollup" failed "could not prepare owned launchd configuration; see diagnostics"
    return 1
  fi
  if [ "$loaded" = 1 ] && [ "$rollup_state" = present ]; then
    rm -f "$candidate"
    echo "✓ agent-monitor rollup: existing schedule unchanged (every ${ROLLUP_INTERVAL_SECONDS}s)"
  else
    [ "$rollup_state" != present ] || rollup_state=updated
    if [ "$loaded" = 1 ]; then
      if ! launchctl bootout "gui/$(id -u)/$ROLLUP_LABEL"; then
        rm -f "$candidate"
        echo "✗ agent-monitor rollup: could not unload the existing schedule; its plist was preserved. Check launchctl diagnostics and rerun ./install.sh rollup-daemon" >&2
        emit_install_ledger service "agent-monitor rollup" failed "could not unload existing schedule; rerun ./install.sh rollup-daemon"
        return 1
      fi
    fi
    mv -f "$candidate" "$ROLLUP_PLIST"
    if ! launchctl bootstrap "gui/$(id -u)" "$ROLLUP_PLIST"; then
      echo "✗ agent-monitor rollup: could not load schedule; log into the macOS desktop and rerun ./install.sh rollup-daemon" >&2
      emit_install_ledger service "agent-monitor rollup" failed "launchctl bootstrap failed; log into macOS desktop and rerun installer"
      return 1
    fi
    echo "✓ agent-monitor rollup: schedule loaded (every ${ROLLUP_INTERVAL_SECONDS}s)"
  fi
  emit_install_ledger service "agent-monitor rollup" "$rollup_state" "launchd schedule every ${ROLLUP_INTERVAL_SECONDS}s; last run not checked (./status.sh rollup-daemon)"
}

if [ "$#" -gt 1 ] && [ "$SERVICE" != hub ]; then
  usage
  exit 2
fi

case "$SERVICE" in
  -h|--help|help)
    usage
    exit 0
    ;;
  hub)
    if [ "$#" != 2 ]; then usage; exit 2; fi
    if [ "$(uname -s)" != Darwin ]; then
      echo "agent-monitor hub installation requires macOS launchd; standalone agent-monitor remains available." >&2
      exit 1
    fi
    ;;
  web) ;;
  rollup-daemon)
    ;;
  *)
    usage
    exit 2
    ;;
esac

install_runtime
if [ "$SERVICE" = hub ]; then
  "$VENV_PY" "$ROOT_DIR/hub.py" configure "$2"
fi
if [ -f "$ROOT_DIR/state/hub-machine" ]; then
  ROLLUP_INTERVAL_SECONDS="${AGENT_MONITOR_ROLLUP_INTERVAL_SECONDS:-$("$VENV_PY" "$ROOT_DIR/hub.py" interval)}"
  ROLLUP_COMMAND="collect"
fi
if [ "$SERVICE" = rollup-daemon ]; then
  if [ "$NO_SERVICES" = 1 ]; then
    echo "agent-monitor services: skipped (--no-services); existing services were not changed."
    emit_install_ledger service "agent-monitor rollup" skipped "--no-services; existing services unchanged"
  else
    install_rollup_daemon
  fi
  exit 0
fi

chart_state=present
[ -f "$CHART_FILE" ] || chart_state=installed
agent_monitor_state=installed
if [ -L "$BIN_DIR/agent-monitor" ] && [ "$(readlink "$BIN_DIR/agent-monitor")" = "$ROOT_DIR/agent-monitor" ]; then
  agent_monitor_state=present
elif [ -e "$BIN_DIR/agent-monitor" ] || [ -L "$BIN_DIR/agent-monitor" ]; then
  agent_monitor_state=updated
fi
ip_check_state=installed
if [ -L "$BIN_DIR/ip-check" ] && [ "$(readlink "$BIN_DIR/ip-check")" = "$ROOT_DIR/ip-check" ]; then
  ip_check_state=present
elif [ -e "$BIN_DIR/ip-check" ] || [ -L "$BIN_DIR/ip-check" ]; then
  ip_check_state=updated
fi

mkdir -p "$ROOT_DIR/state" "$VENDOR_DIR" "$BIN_DIR"
chmod +x "$ROOT_DIR/agent-monitor"

if [ ! -f "$CHART_FILE" ]; then
  curl --fail --location --silent --show-error "$CHART_URL" --output "$CHART_FILE"
fi

mkdir -p "$FONT_DIR"

# A truncated or non-WOFF2 file is worse than a missing one: the browser falls back
# silently and `-f` on the next run treats the wreckage as installed. The magic bytes
# alone do not catch truncation — a file cut after its first four bytes still starts
# with wOF2 — so the header's declared total length is compared with the real size.
is_woff2() {
  [ -s "$1" ] || return 1
  [ "$(head -c 4 "$1" 2>/dev/null)" = "wOF2" ] || return 1
  local hex declared actual
  # WOFF2 header: signature(4) flavor(4) length(4) — `length` is the whole file, stored
  # big-endian. Read it byte-wise; `od --endian` is GNU-only and this also runs on macOS.
  hex="$(od -An -j8 -N4 -tx1 "$1" 2>/dev/null | tr -d ' \n')"
  [ "${#hex}" -eq 8 ] || return 1
  declared=$((16#$hex))
  actual="$(wc -c <"$1" | tr -d ' ')"
  [ "$declared" -eq "$actual" ]
}

font_total="${#FONT_FILES[@]}"
font_fetched=0
font_replaced=0
font_failed=0
font_failed_names=""
for entry in "${FONT_FILES[@]}"; do
  pkg="${entry%%:*}"
  name="${entry##*:}"
  is_woff2 "$FONT_DIR/$name" && continue
  # Distinguished so the report can say a corrupt copy was replaced. Without it the
  # reader is never told a bad file was on their disk.
  [ -e "$FONT_DIR/$name" ] && font_replaced=$((font_replaced + 1))
  rm -f "$FONT_DIR/$name"
  font_fetched=$((font_fetched + 1))
  # Fetch to a scratch path and promote only a verified file, so an interrupted
  # download cannot leave a plausible-looking one behind. The path carries this
  # process's pid: a fixed name lets two concurrent installs move or delete each
  # other's in-flight file.
  tmp="$FONT_DIR/.$name.$$.part"
  if curl --fail --location --silent --show-error \
    "https://cdn.jsdelivr.net/npm/@fontsource/$pkg/files/$name" --output "$tmp" \
    && is_woff2 "$tmp"; then
    mv -f "$tmp" "$FONT_DIR/$name"
  else
    rm -f "$tmp"
    font_failed=$((font_failed + 1))
    font_failed_names="${font_failed_names:+$font_failed_names, }$name"
  fi
done
[ -s "$FONT_DIR/LICENSE.txt" ] || curl --fail --location --silent --show-error \
  "$FONT_LICENSE_URL" --output "$FONT_DIR/LICENSE.txt" || rm -f "$FONT_DIR/LICENSE.txt"

font_have=$((font_total - font_failed))
if [ "$font_failed" -gt 0 ]; then
  # `degraded`, and neither of the two obvious neighbours. Not `failed`: that state
  # forces a non-zero component result even on a zero exit (lib/install-output.sh),
  # turning one CDN hiccup into a failed install. Not `skipped` either: this repo
  # already uses that word for a unit deliberately not attempted (a macOS-bound unit
  # on Linux), so a column scan would read this as "nothing went wrong".
  font_state=degraded
  font_detail="download failed for $font_failed of $font_total; UI renders in the system font stack — re-run ./install.sh"
  # The fix rides on an indented continuation line. The root installer forwards a
  # successful component's warnings along with the lines indented under them, so it
  # arrives on that path too — see lib/install-output.sh.
  echo "WARN: agent-monitor fonts: $font_failed of $font_total IBM Plex files could not be downloaded ($font_failed_names)" >&2
  echo "      agent-monitor renders in the system font stack until then — re-run ./install.sh once the network allows it" >&2
elif [ "$font_fetched" -gt 0 ]; then
  font_state=installed
  font_detail="IBM Plex Sans/Mono $font_total/$font_total, $font_fetched downloaded"
  [ "$font_replaced" -eq 0 ] \
    || font_detail="$font_detail (of which $font_replaced replaced an incomplete copy on disk)"
else
  font_state=present
  font_detail="IBM Plex Sans/Mono $font_total/$font_total, already present and verified"
fi

ln -sfn "$ROOT_DIR/agent-monitor" "$BIN_DIR/agent-monitor"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "WARN: ~/.local/bin not in PATH; please add it before using agent-monitor directly" ;;
esac

# The qualification rides in the line that says "installed", not above it: this is the
# last thing said about agent-monitor, it is what a scrolled terminal or a pasted tail shows,
# and the degradation it reports is otherwise invisible — the pages still render.
case "$font_state" in
  degraded) echo "agent-monitor installed, with the UI degraded — $font_have of $font_total fonts present, see the warning above" ;;
  installed)
    font_note="$font_fetched downloaded"
    [ "$font_replaced" -eq 0 ] \
      || font_note="$font_note, $font_replaced of them replacing an incomplete copy already on disk"
    echo "agent-monitor installed — fonts $font_total/$font_total ($font_note), Chart.js $chart_state"
    ;;
  *) echo "agent-monitor installed — fonts $font_total/$font_total already present, Chart.js $chart_state" ;;
esac
emit_install_ledger artifact "agent-monitor CLI" "$agent_monitor_state" "$BIN_DIR/agent-monitor"
emit_install_ledger artifact "agent-monitor Chart.js asset" "$chart_state" "Chart.js 4.4.0"
emit_install_ledger artifact "agent-monitor typefaces" "$font_state" "$font_detail"

# --- ip-check sub-feature ---
IPCHECK_BIN="$ROOT_DIR/ip-check"
chmod +x "$IPCHECK_BIN"
ln -sfn "$IPCHECK_BIN" "$BIN_DIR/ip-check"

echo "ip-check installed"
emit_install_ledger artifact "ip-check CLI" "$ip_check_state" "$BIN_DIR/ip-check"
if [ "$NO_SERVICES" = 1 ]; then
  echo "agent-monitor installed in staging mode: runtime, assets and CLI links ready; services and network probe skipped (--no-services)."
  emit_install_ledger service "agent-monitor rollup" skipped "--no-services; existing services unchanged"
  emit_install_ledger service "agent-monitor hub" skipped "--no-services; existing services unchanged"
  exit 0
fi
install_rollup_daemon
if [ -f "$ROOT_DIR/state/hub-machine" ]; then
  "$VENV_PY" "$ROOT_DIR/hub.py" install-web
fi

# --- post-install network health hint ---
# Run ip-check once and surface a remediation pointer ONLY when it finds a real
# risk (verdict "high": IPv6 leak / CN DNS / timezone mismatch). Stays silent when
# the environment is clean (low / proxy-in-use). Never aborts the install — a
# network probe must not block setup, so every failure path returns 0. Note: this
# makes one set of outbound calls (ip-api etc.), adding a few seconds to install.
network_health_hint() {
  [ -x "$BIN_DIR/ip-check" ] || return 0
  local py="$VENV_PY"; [ -x "$py" ] || py="python3"
  "$py" -c "import requests" >/dev/null 2>&1 || return 0   # deps missing → ip-check can't run; the WARN above already covers it
  local json
  json="$("$BIN_DIR/ip-check" --json 2>/dev/null)" || return 0
  # Script via stdin (`-`); JSON + runbook path via argv so stdin isn't contended.
  "$py" - "$json" "$ROOT_DIR/NETWORK-REMEDIATION.md" <<'PY' || true
import sys, json
raw, runbook = sys.argv[1], sys.argv[2]
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)
if data.get("verdict") != "high":
    sys.exit(0)
findings = [c["text"] for c in data.get("conclusions", []) if c.get("level") == "bad"]
print("")
print("⚠ ip-check found network risks (verdict: high):")
for text in findings:
    print("    ✗ " + text)
print("  How to fix: " + runbook)
print("  Re-check after fixing: ip-check")
PY
}
network_health_hint || true
