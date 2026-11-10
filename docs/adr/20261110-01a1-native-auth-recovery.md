# Explicit authentication recovery for existing Codex sources

Decision review: L1, one independent reviewer, passed all seven criteria on 2026-11-10. Implementation review and live acceptance remain separate.

## Problem and decision

The native macmini account returned HTTP 401 / `token_expired` after the operator removed an obsolete pause for an abandoned account. This establishes an upstream authentication refusal, not its underlying cause. The recovery panel could offer official authentication recovery only for Monitor accounts, leaving the existing native source without the corresponding action.

Extend the existing explicit `renew_auth` action to native, configured and discovered-profile sources. Preserve the original credential file, account identity, machine, provider lease, egress policy, deadline and save-before-resume order. Bind the resolved credential path by a non-secret digest in the preview, request and server admission. Named profiles retain their operation lock; passing a native path must not create a profile-parent lock in the user's home. Monitor sources retain their failed, known-unsent Manager-operation prerequisites; device reauthorization remains Monitor-only.

For non-Monitor recovery, retain the owning user's HOME and selected CODEX_HOME, strip alternate authentication environment inputs, require file-backed ChatGPT OAuth credentials and reject nonstandard OAuth provenance. Use the official app-server file credential store without introducing a new forced-login-method restriction. Preserve existing user/system/managed configuration and route bridge; this does not guarantee complete configuration isolation or add custom endpoint support. After one official refresh RPC, require the same identity and a changed access token before at most one quota RPC. Only a saved valid quota observation clears the pause. No messages, enrollment, credential copying, default-account switching or automatic retries are added.

## Alternatives and existing decisions

Repeating the rejected quota credential does not establish a new recovery premise. Automatic refreshing changes authorization; copying into Monitor changes credential ownership; a separate OAuth client duplicates supplier behavior. Keeping the initial Monitor-only limit leaves a demonstrated user task incomplete. This extends the source coverage in [shared quota execution](20261103-9481-codex-quota-execution.md) and the initial capability in [explicit auth recovery](20261106-7c21-explicit-auth-recovery.md), retaining their safeguards. The same-machine source resolver remains authoritative. Administrative resume remains a distinct operation and is not evidence of provider recovery.

## Evidence and limits

Local source inspection identified the Monitor restriction in status, validation, service admission and the quota loader, plus a Monitor-specific HOME assumption in child construction. Existing `read_bound` already checks same-identity changed credentials before quota. Fixed upstream [AuthManager source](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/login/src/auth/manager.rs) establishes OAuth provenance and environment overrides, persistent refresh writes and login restriction checks. CLI app-server has no general ignore-config flag. Actual target CLI/shim/config compatibility must be checked before live use; an upstream function's existence alone does not prove its side effects occur on this path.

Offline acceptance must cover native/configured/profile sources, existing Monitor behavior, source and identity changes, exact approval binding, unchanged/missing/new credentials, persistence failure and normal quota consumption. Live authentication requires its own exact operator approval; clearing an obsolete pause does not authorize it. Official internal HTTP/OAuth request counts are not separately bounded by the RPC count. Refresh may persist new credentials before a later failure, which code rollback cannot undo; uncertain results must not be retried automatically.
