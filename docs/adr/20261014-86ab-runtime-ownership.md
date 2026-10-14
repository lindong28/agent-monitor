# Require mutation admission and process-instance ownership

Date: 2026-10-14. Status: accepted after independent decision review; implementation and deployment checks remain separate.

The upstream audit demonstrated cross-origin restart/refresh actions and a standalone PID file terminating an unrelated fixture process. Explicit refresh, restart and account-memory changes must use POST with the existing Host/Origin/custom-header browser boundary; migrate Web and CLI callers together. Ordinary due maintenance remains server-controlled. This remains a trusted personal-network service, not authenticated access for arbitrary LAN clients.

Record and check kernel process-start identity before signalling standalone processes; unknown/mismatched ownership refuses the action and preserves diagnostic state. Linux may use a pidfd; Darwin start-identity comparison followed by kill does not claim atomic immunity to arbitrary PID churn. A bare legacy PID is not automatically adopted. Retain the existing bounded graceful shutdown.

For supervised workers, observe leader exit independently of stdout EOF using WNOWAIT, reserve its PID until same-group cleanup completes, then reap exactly once under shared ownership. All consumers use the supervisor's wait/poll result. This handles descendants retaining stdout and avoids signalling an already-reaped group's reused ID. It does not claim control over arbitrary descendants that create their own sessions.

The audit's reexec finding is withdrawn: QUOTA_REFRESH.shutdown already reaches sync_process.shutdown through quota_sync.Manager.shutdown. Preserve that chain, rather than adding duplicate shutdown. Alternatives rejected include merely trusting kill -0/command substrings, killing a reaped PGID, or waiting for stdout EOF before observing leader exit. Controlled positive/negative process and HTTP examples validate the changed boundaries; actual production incidence is unknown.

Evidence and the final seven-item decision review are in `/Users/lindong/task-artifacts/upstream-contract-repair-20261014/runtime-decision*.md`. This refines [sync-process ownership](20261008-c4e2-sync-process.md) and [child reaping](20261011-a72c-child-process-reaping.md), preserving their existing authority and bounded shutdown goals.
