#!/usr/bin/env bash
# agent-monitor stop — thin wrapper over the agent-monitor dispatcher.
exec "$(cd "$(dirname "$0")" && pwd)/agent-monitor" stop "$@"
