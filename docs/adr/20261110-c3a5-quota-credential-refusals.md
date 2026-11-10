# Quota credential refusals and historical pause attribution

Status: accepted 2026-11-10. Independent decision review: one review and focused correction verification; both findings resolved before implementation. Extends the account isolation, explicit authentication recovery and credential quarantine ADRs from November 4–7.

An observed batch completed six accounts, received HTTP 401 with `token_expired` for the next account and skipped the last account under a shared pause. That code reports an upstream refusal; it does not independently establish actual credential expiration.

Recognize `token_expired` alongside `token_revoked` only in the existing exact Codex quota transport envelope for automatic account isolation. Keep method, RPC code, HTTP/body status, identity and credential-generation checks. Safe diagnostic fields explain results and offer explicit recovery; they are not isolation authority. Reuse official managed `account/read(refreshToken=true)` recovery, then read quota only after confirming new credentials for the same identity. Ordinary queries remain one quota RPC, without automatic refresh or retry.

Carry verified credential rejection from the child through a private result field, separately from diagnostics. Under existing locks, the parent checks current identity and generation before persisting the rejection. A repeated verified account refusal stays account-local; unknown, transport or identity failures retain shared protection. The private proof is not published in recovery UI receipts.

Historical failures may lack strict admission evidence. Reuse the explicit operator attribution action, bound to the current shared pause, original failed unsent quota operation, source and identity. For a known quota authentication refusal, attribution creates an account hold before clearing that exact shared pause, without requiring the operator to disable the account. Preserve the old failure and attribution basis; do not invent a historical rejected generation, overwrite another hold or claim authentication success. Independent unfinished accounts still require their own ordinary egress confirmation. Existing disabled-account attribution remains supported.

Keeping the generic shared stop reproduces the failure; replaying every account duplicates successes without repairing credentials; making every 401 account-local lacks scope evidence. A second recovery endpoint or state store duplicates existing owners. Existing machine-local controls, profile locks and retention are retained; no credential or snapshot migration is required. Downgrade must retain account holds and requires a version that understands them.

Synthetic checks must cover both codes, normal success, unknown/contradictory transport failures, identity/generation changes, no-new-credential recovery and explicit historical attribution. Live recovery and actual credential expiration remain unverified until separately observed through the owning machine's approved route.
