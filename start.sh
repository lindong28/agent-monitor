#!/usr/bin/env bash
# agent-monitor start — thin wrapper over the agent-monitor dispatcher (manual PID-file daemon).
exec "$(cd "$(dirname "$0")" && pwd)/agent-monitor" start "$@"
