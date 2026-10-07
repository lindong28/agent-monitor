# Codex account actions

Status: accepted for local implementation, 2026-10-07. One independent decision review passed. Deployment, publication and real account acceptance remain separate.

## Decision and scope

The user requested Web-based sign-in for several Codex accounts followed by one message per account, and approved the official authorization-page flow. Add a dedicated account-action panel beside the existing quota table. Use Codex app-server managed device authorization, one private `CODEX_HOME` per account profile, and a fixed short message after verifying the signed-in email and workspace identity. Reuse valid credentials on subsequent explicit clicks. Preserve the ordinary CLI's account, machine snapshots, account-memory identities and accounting semantics.

The backend persists credentials only on the serving machine under private profile directories. It exposes operation state and observed quota values, never OAuth tokens or email verification codes. Device codes are displayed only while authorization is pending. The official page owns email verification. Device authorization must be enabled in the account or workspace settings.

Use an isolated HOME and working directory, disabled shell/code-mode/apps/plugins/hooks/multi-agent/web-search, and `environments: []` with experimental API negotiation. Reject unexpected server tool requests. A read-only sandbox is an additional constraint, not a claim that read-only commands cannot execute. The fixed message is not a general command or prompt endpoint.

Each profile has one operation at a time, protected across processes by a file lock. Login and message stages have bounded deadlines; cancellation and process cleanup are explicit. Persist the last operation before sending. Interrupted sends remain unknown after restart and are never replayed automatically. Keep message completion and quota observation separate; use the server's `resetsAt`, never a local seven-day calculation. These action readings remain separate from machine-collected snapshots.

## Alternatives and existing boundaries

Switching the default `~/.codex/auth.json` would affect current CLI sessions. Browser automation for email OTP has no verified integration here. OAuth localhost callbacks require additional routing when the browser and Hub run on different machines; device authorization avoids that callback dependency.

The existing personal LAN service has no application authentication (see the 2026-08-20 entry in `docs/issues/general.md`). Keep that existing network trust scope: anyone who can access the dashboard can invoke fixed account actions. Require same-origin JSON POSTs for the new operations, but do not claim those checks authenticate LAN clients. Public or untrusted-network exposure is outside this design. Credentials do not enter HTTP responses, snapshots or exports. No remote-machine credentials are imported. The extraction ADR's read-only collection boundary and the existing `(account_id, email)` distinction remain intact.

Operations run only following a click, with visible failure status and bounded completion; there is no unattended weekly scheduler or new push-alert service.

A separate quota-only action permits another observation after a failed quota query without sending another message. Each profile displays the latest operation and its observation time; this is not an operation-history ledger.

## Evidence and acceptance boundary

Read the official [app-server auth](https://developers.openai.com/codex/app-server#auth-endpoints) and [authentication](https://developers.openai.com/codex/auth) documentation. Locally, Codex CLI 0.158.0 generated schemas confirming device-code login and thread/turn environment isolation. This establishes interface availability, not successful login or inference.

Verify isolated credential storage, identity mismatch refusal, protocol framing, completion versus unknown outcomes, quota failures, duplicate-click exclusion, cancellation, restart and secret-free HTTP responses with isolated fixtures. Exercise the actual local CLI without credentials separately. Browser checks cover the implemented controls and representative operation states. Real email authorization, model response and quota-window behavior require a user's authorized account; report that coverage separately. Source completion is not deployment proof.

Local validation on 2026-10-07: the actual CLI accepted an isolated, unauthenticated `initialize` / `account/read` probe, returning no account; its child exited after cleanup. The final affected suite passed 85 tests (17 new account/HTTP/time tests and 68 existing Web/static/overview tests). The new tests use synthetic stdio scenarios, two identity-mismatch types, valid/invalid HTTP admission, and two client timezones against one server timezone; no real provider store or model call was used. The complete suite before the final timezone fix ran 813 tests with 4 failures / 10 errors; the same 11 failing methods reproduced identical failure headers/counts on baseline `8182b64`, recorded in `ISSUE-TEST-20261004-6b2a`.

An isolated headless browser exercised synthetic authorization, cancel, send success, quota failure, quota-only refresh, page reload, SPA return and a deliberately delayed stale list response. Desktop 1280×633 and mobile 390×844 were inspected; the latter had no document overflow. Browser timestamps showed the server's GMT+8. Existing mobile navigation branding overlap remains `ISSUE-UX-20261006-23be`. One independent change review found a timezone contract violation, fixed by reusing the existing formatter; the reviewer confirmed the fix and a reversing mutation made the new test fail in both client zones. The remaining multi-process recovery race is recorded as `ISSUE-CODEX-20261007-8e41`, outside the ordinary single-service runtime envelope.
