# Open engineering observations

Reader: Agent. These source observations are outside the 2026-10-28 recovery guidance change; they are not evidence of the incident's root cause.

## ISSUE-RECOVERY-20261028-8c27 — Legacy resume can clear the shared pause

Status: open. Owner: agent-monitor backend maintenance. Priority: review before changing recovery admission.

At baseline 82b8219, `http_server.py` forwards optional `pause_id` on `/api/codex-accounts/resume`; `accounts/codex_accounts.py:Manager.resume` calls `Controls.resume`. `tests/test_codex_account_http.py` explicitly expects the shared pause to be cleared without an RPC. The current UI omits that field. Consequently the quota recovery endpoint's valid-reading condition is not a universal invariant over every administrative API.

Follow-up: inventory legitimate resume callers and decide the compatibility policy, then change admission and tests together if required. This is not a new UI regression and this task does not silently remove the existing operator API. No live bypass was executed.

## ISSUE-DIAGNOSTICS-20261028-b731 — Post-send quota failure loses detailed cause

Status: open. Owner: agent-monitor account operations. Priority: diagnostic follow-up.

At baseline 82b8219, the post-send quota `except ActionError` in `accounts/codex_accounts.py:Manager.run` saves category/stage and calls `lease.pause("quotaReadFailed")` without the failure details. A successful message is correctly retained, but this path cannot supply the same detailed receipt as quota recovery. The original incident has not been attributed to this path.

Follow-up: preserve a bounded redacted cause through the post-send failure path with an isolated RPC failure case; keep successful message state and prevent replay. This is a producer-side change outside the current shared recovery UI presentation scope; the UI explicitly reports when old diagnostic details were not recorded.
