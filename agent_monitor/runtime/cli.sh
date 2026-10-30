#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Module entrypoints must resolve this checkout even when called from elsewhere.
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
STATE_DIR="$ROOT_DIR/state"
PID_FILE="$STATE_DIR/pid"
PORT_FILE="$STATE_DIR/port"
LOG_FILE="$STATE_DIR/server.log"
BIND_HOST="${AGENT_MONITOR_BIND:-0.0.0.0}"
CONNECT_HOST="127.0.0.1"
BASE_PORT="${AGENT_MONITOR_PORT:-39001}"

# Resolve only this project's runtime, never a parent repository's environment.
# A system interpreter remains usable on minimal exporters; install.sh creates
# the complete local runtime. An explicit override is reported by `status`.
AGENT_MONITOR_PYTHON="${AGENT_MONITOR_PYTHON:-$(
  for candidate in "$ROOT_DIR/.venv/bin/python"; do
    [ -x "$candidate" ] && { echo "$candidate"; exit 0; }
  done
  command -v python3
)}"
: "${AGENT_MONITOR_PYTHON:=python3}"
# Put this checkout ahead of the caller's cwd without changing relative CLI paths.
PYTHON_MODULE=("$AGENT_MONITOR_PYTHON" -c 'import sys; sys.path[0]=sys.argv.pop(1); from agent_monitor.runtime.service_logs import initialize; initialize(sys.path[0]); import runpy; runpy.run_module(sys.argv.pop(1),run_name="__main__",alter_sys=True)' "$ROOT_DIR")
LOCAL_WEB=""
if [ "${1:-}" = local ]; then
  LOCAL_WEB=local
  shift
  case "${1:-}" in
    start|stop|restart|status|open) ;;
    *) echo "usage: agent-monitor local {start|stop|restart|status|open}" >&2; exit 2 ;;
  esac
  if [ "$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub role)" = hub ]; then
    echo "This checkout owns the central Hub; use agent-monitor $1 to manage that service. A separate local Web is not managed in this Hub checkout." >&2
    exit 2
  fi
fi

usage() {
  echo "Archive maintenance: agent-monitor archive [stats|compact --backup NEW_PERSISTENT_PATH] [--archive PATH] [--rollup-db PATH] [--budget-seconds N] [--json]"
  echo "Service management: agent-monitor service {status|restart local-web|restart hub|restart collector|restart connector|deploy [--expect SHA]|rebuild-runtime --python /absolute/python}"
  echo "Local dashboard: agent-monitor local {start|stop|restart|status|open}"
  echo "Component status: agent-monitor service status [web|rollup-daemon|connector]"
  echo "Provider holds: agent-monitor upstream-holds {status|resume} {claude|codex} [--pause-id ID]"
  echo "Quota pause status and approved recovery: agent-monitor quota-recover --help"
  echo "usage: agent-monitor {start|stop|restart|status|open|refresh|connect [install --server URL|status|stop|restart|--server URL]|network [--json] [--force]|session-quota SESSION_ID [--json]|export (--out PATH|--version)|machines (accept NAME [--yes]|retire NAME...)|rollup [--check [--json]|adopt-timezone|blockers|recover|recover-reviewed]}"
}

is_running() {
  "${PYTHON_MODULE[@]}" agent_monitor.runtime.process_identity check "$STATE_DIR" "$ROOT_DIR"
}

find_port() {
  local port="$BASE_PORT"
  while nc -z "$CONNECT_HOST" "$port" >/dev/null 2>&1; do
    if [ "${AGENT_MONITOR_EXACT_PORT:-0}" = 1 ]; then
      echo "Configured local Web port is occupied; no replacement was started." >&2
      return 1
    fi
    port=$((port + 1))
  done
  echo "$port"
}

start() {
  if ! "${PYTHON_MODULE[@]}" agent_monitor.runtime.deployment_guard held "$ROOT_DIR"; then
    "${PYTHON_MODULE[@]}" agent_monitor.runtime.deployment_guard run "$ROOT_DIR" -- bash "$ROOT_DIR/agent_monitor/runtime/cli.sh" ${LOCAL_WEB:+"$LOCAL_WEB"} start
    return
  fi
  local hub_role
  hub_role=standalone
  [ -n "$LOCAL_WEB" ] || hub_role="$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub role)"
  if [ "$hub_role" = hub ]; then
    "${PYTHON_MODULE[@]}" agent_monitor.runtime.hub install-web
    return
  fi
  mkdir -p "$STATE_DIR"
  if is_running; then
    local port
    port="$(cat "$PORT_FILE" 2>/dev/null || echo "$BASE_PORT")"
    local pid
    pid="$("${PYTHON_MODULE[@]}" agent_monitor.runtime.process_identity pid "$STATE_DIR" "$ROOT_DIR")" || return $?
    echo "agent-monitor already running: http://$CONNECT_HOST:$port (pid $pid)"
    return 0
  else
    local state_result=$?
    [ "$state_result" -eq 1 ] || return "$state_result"
  fi

  local port
  port="$(find_port)"
  local pid
  pid="$("${PYTHON_MODULE[@]}" agent_monitor.cli.launch_web "$ROOT_DIR" "$BIND_HOST" "$port" "$LOG_FILE")"
  echo "$pid" > "$PID_FILE"
  echo "$port" > "$PORT_FILE"

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl --noproxy '*' -fsS "http://$CONNECT_HOST:$port/api/health" >/dev/null 2>&1; then
      echo "agent-monitor started: http://$CONNECT_HOST:$port (pid $pid)"
      return 0
    fi
    sleep 0.2
  done

  echo "agent-monitor start failed; see $LOG_FILE" >&2
  return 1
}

stop() {
  if ! "${PYTHON_MODULE[@]}" agent_monitor.runtime.deployment_guard held "$ROOT_DIR"; then
    "${PYTHON_MODULE[@]}" agent_monitor.runtime.deployment_guard run "$ROOT_DIR" -- bash "$ROOT_DIR/agent_monitor/runtime/cli.sh" ${LOCAL_WEB:+"$LOCAL_WEB"} stop
    return
  fi
  if [ -z "$LOCAL_WEB" ] && [ "$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub role)" = hub ]; then
    "${PYTHON_MODULE[@]}" agent_monitor.runtime.hub stop-web
    return
  fi
  if is_running; then
    :
  else
    local state_result=$?
    [ "$state_result" -eq 1 ] || return "$state_result"
    rm -f "$PID_FILE" "$PORT_FILE" "$STATE_DIR/pid-identity.json"
    echo "agent-monitor not running"
    return 0
  fi
  "${PYTHON_MODULE[@]}" agent_monitor.runtime.process_identity stop "$STATE_DIR" "$ROOT_DIR" || return $?
  rm -f "$PID_FILE" "$PORT_FILE" "$STATE_DIR/pid-identity.json"
  echo "agent-monitor stopped"
}

status() {
  local role hub_url
  role=standalone
  [ -n "$LOCAL_WEB" ] || role="$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub role)"
  if [ "$role" = hub ]; then
    "${PYTHON_MODULE[@]}" agent_monitor.runtime.hub status-web
    return
  fi
  if [ "$role" = client ]; then
    hub_url="$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub url)"
    echo "Role: Hub client. Dashboard target: $hub_url (availability not checked)."
    echo "Use open/refresh for the Hub; use local start/stop/restart/status/open for the optional local Web. Run agent-monitor service status for collector and connector observations."
    return 0
  fi
  if is_running; then
    :
  else
    local state_result=$?
    if [ "$state_result" -eq 1 ]; then
      echo "agent-monitor not running"
      return 0
    fi
    return "$state_result"
  fi
  local pid port uptime
  pid="$("${PYTHON_MODULE[@]}" agent_monitor.runtime.process_identity pid "$STATE_DIR" "$ROOT_DIR")" || return $?
  port="$(cat "$PORT_FILE" 2>/dev/null || echo "$BASE_PORT")"
  uptime="$(ps -o etime= -p "$pid" 2>/dev/null | awk '{$1=$1; print}')"
  echo "agent-monitor running: pid=$pid port=$port uptime=${uptime:-unknown} url=http://$CONNECT_HOST:$port"
  echo "python: $AGENT_MONITOR_PYTHON ($("$AGENT_MONITOR_PYTHON" -c 'import sys,sqlite3;print("%s, sqlite %s" % (sys.version.split()[0], sqlite3.sqlite_version))' 2>/dev/null || echo unknown))"
}

compile_ok() {
  # Use the same runtime inventory as the server; reject broken nested modules.
  "${PYTHON_MODULE[@]}" agent_monitor.cli.compile_sources "$ROOT_DIR" 2>/dev/null
}

refresh_if_stale() {
  local port stale
  port="$(cat "$PORT_FILE" 2>/dev/null || echo "$BASE_PORT")"
  stale="$(curl --noproxy '*' -fsS "http://$CONNECT_HOST:$port/api/health?asset_watch=1" 2>/dev/null \
    | "$AGENT_MONITOR_PYTHON" -c 'import json,sys; print("1" if json.load(sys.stdin).get("stale") else "0")' 2>/dev/null || echo 0)"
  [ "$stale" = "1" ] || return 0
  if compile_ok; then
    echo "agent-monitor: 检测到代码已更新，重启以加载新代码…"
    stop >/dev/null || return $?
    start
  else
    echo "agent-monitor: 代码已更新但存在语法错误，未重启；打开当前(旧)版本。" >&2
  fi
}

request_fresh_generation() {
  local port response pending
  port="$(cat "$PORT_FILE" 2>/dev/null || echo "$BASE_PORT")"
  if ! response="$(curl --noproxy '*' -fsS -X POST -H 'X-Agent-Monitor: action' -H "Origin: http://$CONNECT_HOST:$port" "http://$CONNECT_HOST:$port/api/refresh?ack=1" 2>/dev/null)"; then
    echo "agent-monitor: 未能请求最新用量快照；将打开现有数据。请在页面中点 Refresh 重试。" >&2
    return 0
  fi
  pending="$(
    printf '%s' "$response" \
      | "$AGENT_MONITOR_PYTHON" -c 'import json,sys; receipt=json.load(sys.stdin); print("1" if receipt.get("refresh_request",0)>0 else "0")' 2>/dev/null \
      || echo 0
  )"
  if [ "$pending" != "1" ]; then
    echo "agent-monitor: 未能请求最新用量快照；将打开现有数据。请在页面中点 Refresh 重试。" >&2
  fi
}

open_url() {
  local url="$1"
  if [ "$(uname -s)" = Darwin ] && command -v open >/dev/null 2>&1; then
    open "$url" && return 0
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" && return 0
  fi
  echo "Open this dashboard URL in your browser: $url"
}

open_dashboard() {
  local hub_url
  hub_url=""
  [ -n "$LOCAL_WEB" ] || hub_url="$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub url)"
  if [ -n "$hub_url" ]; then
    # The browser always reaches the same central service from every computer.
    if [ -n "${SSH_CONNECTION:-}" ]; then
      echo "$hub_url"
    else
      open_url "$hub_url"
    fi
    return
  fi
  if is_running; then
    refresh_if_stale
  else
    local state_result=$?
    [ "$state_result" -eq 1 ] || return "$state_result"
    start
  fi
  request_fresh_generation
  local port
  port="$(cat "$PORT_FILE")"

  if [ -n "${SSH_CONNECTION:-}" ]; then
    local ts_host
    ts_host="$(tailscale status --json 2>/dev/null | "$AGENT_MONITOR_PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName",""))' 2>/dev/null || true)"
    ts_host="${ts_host%%.*}"  # extract the short hostname from the device's full DNS name
    : "${ts_host:=$(hostname)}"
    local url="http://$ts_host:$port"
    printf '\e]8;;%s\e\\%s\e]8;;\e\\\n' "$url" "$url"
  else
    open_url "http://$CONNECT_HOST:$port"
  fi
}

run_rollup() {
  mkdir -p "$STATE_DIR"
  "${PYTHON_MODULE[@]}" agent_monitor.cli.rollup_run
}

run_rollup_check() {
  "${PYTHON_MODULE[@]}" agent_monitor.cli.rollup_check "$@"
}

run_rollup_identity() {
  "${PYTHON_MODULE[@]}" agent_monitor.accounting.rollup_identity "$@"
}

run_rollup_adopt_timezone() {
  "${PYTHON_MODULE[@]}" agent_monitor.cli.rollup_adopt_timezone "$@"
}

run_machine_retire() {
  "${PYTHON_MODULE[@]}" agent_monitor.cli.machine_retire "$@"
}

run_machine_accept() {
  "${PYTHON_MODULE[@]}" agent_monitor.cli.machine_accept "$@"
}


run_network() {
  "${PYTHON_MODULE[@]}" agent_monitor.network.network_report "$@"
}

run_export() {
  "${PYTHON_MODULE[@]}" agent_monitor.cli.export "$@"
}

run_session_quota() {
  "${PYTHON_MODULE[@]}" agent_monitor.cli.session_quota "$@"
}

if [ -z "$LOCAL_WEB" ]; then
  case "${1:-}" in
    start|stop|restart)
      if [ "$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub role)" = client ]; then
        echo "This machine is a Hub client. Run this Web management command on the Hub host; no local service was changed. For this machine's local Web, use: agent-monitor local $1" >&2
        exit 2
      fi
      ;;
  esac
fi

case "${1:-}" in
  archive)
    shift
    exec "${PYTHON_MODULE[@]}" agent_monitor.cli.archive_maintenance "$@"
    ;;
  service)
    shift
    exec "${PYTHON_MODULE[@]}" agent_monitor.runtime.service_manager "$@"
    ;;
  upstream-holds)
    shift
    exec "${PYTHON_MODULE[@]}" agent_monitor.accounts.upstream_controls "$@"
    ;;
  quota-recover)
    shift
    "${PYTHON_MODULE[@]}" agent_monitor.quota.quota_recovery "$@"
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    if ! "${PYTHON_MODULE[@]}" agent_monitor.runtime.deployment_guard held "$ROOT_DIR"; then
      exec "${PYTHON_MODULE[@]}" agent_monitor.runtime.deployment_guard run "$ROOT_DIR" -- bash "$ROOT_DIR/agent_monitor/runtime/cli.sh" ${LOCAL_WEB:+"$LOCAL_WEB"} restart
    fi
    if [ -z "$LOCAL_WEB" ] && [ "$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub role)" = hub ]; then
      "${PYTHON_MODULE[@]}" agent_monitor.runtime.hub stop-web && "${PYTHON_MODULE[@]}" agent_monitor.runtime.hub install-web
      exit $?
    fi
    stop >/dev/null || exit $?
    start
    ;;
  status)
    status
    ;;
  open)
    open_dashboard
    ;;
  network)
    shift
    run_network "$@"
    ;;
  session-quota)
    shift
    run_session_quota "$@"
    ;;
  connect)
    shift
    exec "${PYTHON_MODULE[@]}" agent_monitor.network.client_connector "$@"
    ;;
  export)
    shift
    run_export "$@"
    ;;
  machines)
    shift
    case "${1:-}" in
      retire)
        shift
        run_machine_retire "$@"
        ;;
      accept)
        shift
        run_machine_accept "$@"
        ;;
      *)
        echo "unknown machines command: ${1:-}" >&2
        usage >&2
        exit 2
        ;;
    esac
    ;;
  refresh)
    if [ -n "$("${PYTHON_MODULE[@]}" agent_monitor.runtime.hub url)" ] || is_running; then
      "${PYTHON_MODULE[@]}" agent_monitor.cli.refresh
    else
      state_result=$?
      [ "$state_result" -eq 1 ] || exit "$state_result"
      run_rollup
      echo "agent-monitor refresh: partial — local rollup finished; dashboard is stopped, so cross-machine data and Quota were not refreshed. Run 'agent-monitor start', then 'agent-monitor refresh'." >&2
      exit 1
    fi
    ;;
  collect)
    "${PYTHON_MODULE[@]}" agent_monitor.runtime.hub collect
    ;;
  rollup)
    shift
    case "${1:-}" in
      "")
        run_rollup
        ;;
      --check)
        shift
        run_rollup_check "$@"
        ;;
      adopt-timezone)
        shift
        run_rollup_adopt_timezone "$@"
        ;;
      blockers|recover|recover-reviewed|-h|--help)
        run_rollup_identity "$@"
        ;;
      *)
        echo "unknown rollup command: $1" >&2
        run_rollup_identity --help >&2
        exit 2
        ;;
    esac
    ;;
  *)
    usage
    exit 2
    ;;
esac
