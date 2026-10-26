#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${AGENT_MONITOR_PYTHON:-python3}" "$ROOT_DIR/deployment_guard.py" install "$ROOT_DIR" -- "$@"
# AGENT_MONITOR_INSTALL_BODY
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$ROOT_DIR/lib/install-output.sh"
BIN_DIR="$HOME/.local/bin"
VENDOR_DIR="$ROOT_DIR/web/vendor"
CHART_FILE="$VENDOR_DIR/chart.umd.min.js"
CHART_URL="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
CHART_SHA256="0e2326c6868072bec1592760c6729043caeea2960a2b46cee6a2192aac6abff0"
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
ROLLUP_COMMAND="rollup"
VENV_PY="$ROOT_DIR/.venv/bin/python"

usage() {
  echo "usage: ./install.sh [web|rollup-daemon|hub MACHINE] [--no-services]" >&2
}

require_python() {
  "$1" - <<'PYTHON'
import sys
if sys.version_info < (3, 10):
    sys.exit("agent-monitor requires Python >=3.10; selected interpreter: " + sys.executable)
PYTHON
}

install_runtime() {
  local runtime_state=present digest marker="$ROOT_DIR/.venv/.agent-monitor-requirements.sha256"
  if [ -x "$VENV_PY" ]; then
    if ! require_python "$VENV_PY"; then
      echo "Existing .venv was preserved. Use /path/to/python3.10-or-newer runtime_rebuild.py --python /path/to/python3.10-or-newer from the published main checkout; it preserves state and restores the old runtime if rebuilding fails." >&2
      return 1
    fi
  else
    require_python "${AGENT_MONITOR_PYTHON:-python3}"
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
  "$VENV_PY" "$ROOT_DIR/deployment_guard.py" check "$ROOT_DIR"
  if [ "$(uname -s)" != Darwin ]; then
    echo "→ agent-monitor rollup: skipped on $(uname -s) (requires macOS launchd)"
    emit_install_ledger service "agent-monitor rollup" skipped "Platform: macOS-only launchd schedule"
    return 0
  fi
  local rollup_state
  if ! rollup_state="$("$VENV_PY" "$ROOT_DIR/rollup_service.py" install --command "$ROLLUP_COMMAND" --interval "$ROLLUP_INTERVAL_SECONDS")"; then
    emit_install_ledger service "agent-monitor rollup" failed "schedule convergence failed; see diagnostics"
    return 1
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
    if [ "$NO_SERVICES" = 1 ]; then
      echo "hub MACHINE changes the live role and cannot be staged with --no-services. Use ./install.sh --no-services to stage assets without changing the role, then rerun ./install.sh hub MACHINE to apply it." >&2
      exit 2
    fi
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
if ! proxy_runtime_state="$("$VENV_PY" "$ROOT_DIR/proxy_runtime/install.py")"; then
  emit_install_ledger artifact "agent-monitor system proxy runtime" failed "resolver installation failed; existing services unchanged"
  exit 1
fi
echo "agent-monitor system proxy runtime: $proxy_runtime_state (Chromium system resolver)"
emit_install_ledger artifact "agent-monitor system proxy runtime" "$proxy_runtime_state" "Electron 45.0.0; system proxy resolver"
if [ "$SERVICE" = hub ]; then
  "$VENV_PY" "$ROOT_DIR/deployment_guard.py" check "$ROOT_DIR"
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

chart_valid() {
  "$VENV_PY" - "$1" "$CHART_SHA256" <<'PYTHON'
import hashlib, pathlib, sys
try:
    valid = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() == sys.argv[2]
except OSError:
    valid = False
sys.exit(0 if valid else 1)
PYTHON
}
if ! chart_valid "$CHART_FILE"; then
  [ ! -e "$CHART_FILE" ] || chart_state=updated
  chart_candidate="$(mktemp "$CHART_FILE.XXXXXX")"
  if ! curl --fail --location --silent --show-error --connect-timeout 15 --max-time 120 "$CHART_URL" --output "$chart_candidate" \
      || ! chart_valid "$chart_candidate"; then
    rm -f "$chart_candidate"
    echo "Chart.js download or checksum verification failed; existing asset preserved. Rerun ./install.sh to retry." >&2
    exit 1
  fi
  mv -f "$chart_candidate" "$CHART_FILE"
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
  emit_install_ledger service "agent-monitor connector" skipped "--no-services; existing services unchanged"
  exit 0
fi
install_rollup_daemon
if [ -f "$ROOT_DIR/state/hub-machine" ]; then
  "$VENV_PY" "$ROOT_DIR/hub.py" install-web
fi
if [ "$(uname -s)" = Darwin ]; then
  "$VENV_PY" "$ROOT_DIR/connector_service.py" converge
fi
"$VENV_PY" "$ROOT_DIR/local_web.py"

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
