#!/usr/bin/env bash
# Optional parent-installer ledger protocol. Human output remains on stdout/stderr.
# Extracted from ai-agent-config 3d61487d; no parent checkout is needed.
emit_install_ledger() {
  local kind="$1"
  local unit="$2"
  local state="$3"
  local detail="$4"
  local fd="${INSTALL_LEDGER_FD:-}"
  [ -n "$fd" ] || return 0
  unit="${unit//$'\t'/ }"
  unit="${unit//$'\n'/ }"
  detail="${detail//$'\t'/ }"
  detail="${detail//$'\n'/ }"
  printf '%s\t%s\t%s\t%s\n' "$kind" "$unit" "$state" "$detail" >&"$fd"
}
