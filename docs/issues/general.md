# Open engineering observations

Reader: Agent. These source observations are outside the 2026-10-28 recovery guidance change; they are not evidence of the incident's root cause.

## ISSUE-RECOVERY-20261028-8c27 — Legacy resume can clear the shared pause

Status: open. Owner: agent-monitor backend maintenance. Priority: review before changing recovery admission.

At baseline 82b8219, `http_server.py` forwards optional `pause_id` on `/api/codex-accounts/resume`; `accounts/codex_accounts.py:Manager.resume` calls `Controls.resume`. `tests/test_codex_account_http.py` explicitly expects the shared pause to be cleared without an RPC. The current UI omits that field. Consequently the quota recovery endpoint's valid-reading condition is not a universal invariant over every administrative API.

Follow-up: inventory legitimate resume callers and decide the compatibility policy, then change admission and tests together if required. This is not a new UI regression and this task does not silently remove the existing operator API. No live bypass was executed.

## ISSUE-DIAGNOSTICS-20261028-b731 — Post-send quota failure loses detailed cause

Status: resolved in the 2026-11-06 quota recurrence repair. Owner: agent-monitor account operations.

At baseline 82b8219, the post-send quota `except ActionError` in `accounts/codex_accounts.py:Manager.run` saves category/stage and calls `lease.pause("quotaReadFailed")` without the failure details. A successful message is correctly retained, but this path cannot supply the same detailed receipt as quota recovery. The original incident has not been attributed to this path.

Follow-up: preserve a bounded redacted cause through the post-send failure path with an isolated RPC failure case; keep successful message state and prevent replay. This is a producer-side change outside the current shared recovery UI presentation scope; the UI explicitly reports when old diagnostic details were not recorded.

The recurrence repair shares pause-failure construction with the normal action failure path, persists the safe RPC cause and operation identity, and preserves `message_status=succeeded` without replay. `tests/test_quota_recurrence.py` exercises this through an isolated stdio peer and saved account state. Historical missing fields are not backfilled.


## ISSUE-ACCEPTANCE-20261111-c7a2 — Default browser runner fails on a credential-detail assertion

Status: open. Owner: agent-monitor browser acceptance maintenance. Priority: normal; separate from recovery-route acceptance.

On 2026-11-11, after repairing the newly imported device module in both fixtures, the public default `scripts/accept_device_connection.mjs` reached the six-account disclosure scenario but failed `retained detail field: 已保存`. The same script assertion and the relevant account-detail rendering predate this change; the old baseline was not separately run in a browser. This result does not establish missing credentials or a product login failure. Earlier runner prerequisites also conflated list readiness with six selected accounts and read the old batch-status location; this change fixes those prerequisites through actual selection controls and the current send receipt, without changing product selection defaults.

Reproduce from a development checkout after `npm ci`: `node scripts/accept_device_connection.mjs --browser /absolute/path/to/Chrome --python "$PWD/.venv/bin/python"`. Inspect the expanded first account detail and compare its credential explanation with the runner assertion before deciding whether the product or assertion needs correction. Do not remove an assertion merely to get a green result. The default, facade and disclosures-only branches share this disclosure segment; later assertions in those branches remain unverified. Failure evidence for this observation was retained under the maintainer task `~/task-artifacts/agent-monitor/2026-11-11-recovery-route-c7a2/public-acceptance/monitor-accept-gM9U8E`.

The same runner's `--account-scopes-only` branch completed after the module migration: two paused targets switch, a single explicit recovery clears only its target, reload retains the other pause and an independent account remains usable. Separately, the recovery-route fixture exercised same-device preview, cancel with zero provider-boundary calls, explicit new-device confirmation, one synthetic quota result, normal persistence/publication and original-batch success after reload. These are isolated results, not proof of real-provider recovery or completion of the default visual/disclosure suite.
