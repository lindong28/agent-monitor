# Open engineering observations

Reader: Agent. These source observations are outside the 2026-10-28 recovery guidance change; they are not evidence of the incident's root cause.

## Hub forwarding loop detection through the HTTP bridge

Owner: Hub forwarding maintenance. The existing adapter test observes the hop marker before the HTTP bridge. Verify end-to-end loop detection across the supported forwarding path when maintaining this boundary. The observed overview connection timeout occurred before forwarding and has a [separate recovery path](../adr/20261125-7c58-hub-read-timeout.md).

## ISSUE-DIAGNOSTICS-20261116-e43e — Automatic quota transport failure needs causal evidence

Status: diagnostic gaps repaired; original transport cause remains unknown. Owner: agent-monitor quota maintenance.

The MacBook incident had already consumed the permitted two quota RPC attempts before pausing. Its generic `-32603` transport wrapper did not distinguish the failing network layer or establish an authentication failure. The approved recovery succeeded, and a later active-query reading was saved locally and consumed by the normal overview. That recovery does not identify the original cause or justify a larger retry budget.

Normal quota subprocess failures now retain bounded, credential-redacted client diagnostics and the initialized client version. Available proxy-bridge facts are retained with their scope: automatic-batch observations may include other accounts and cannot establish which connection belonged to the failed RPC. Recovery records preserve their matching original pause, and the UI presents a new pause separately from historical recovery. Legacy records only recover an original failure when the pause identity matches exactly. See [quota recovery diagnostics](../operations/services.md#paused-codex-quota-queries) for interpretation and handling boundaries.

Acceptance used isolated child processes with native and explicit credential homes, oversized stderr containing synthetic credentials, and successive pauses. Bridge checks covered success, failure, unavailable observations and unscoped routes. In the real browser against the isolated recovery service, cancelling preview made zero provider-boundary calls; one confirmed synthetic recovery saved and published a quota reading. A subsequent injected pause displayed the current failure, client version/log and batch-scope caveat alongside the original HTTP 401; copying diagnostics included both failures and did not issue another query. These are synthetic provider results, not a new live recovery or proof that every transport cause is distinguishable.

Continuation: when this generic failure recurs, use the saved client/bridge evidence and applicable upstream documentation to decide whether a specific safe automatic handler is warranted. If the client still supplies no causal detail, report that remaining uncertainty and preserve the existing bounded recovery path; do not infer login failure, change egress, or add automatic retries from `-32603` alone. No additional live provider probe is required while the account is working.

On 2026-11-18 the MacBook paused again after the same two-opportunity transport failure. Its saved stderr prefix contained only startup configuration warnings. A separately approved recovery for the bound account succeeded and saved a new active observation through the original route. New captures retain bounded beginning and final output, and Overview links directly to the current machine recovery card; see [the decision](../adr/20261118-4c9a-quota-recovery-entry-and-diagnostics.md). These changes improve access and evidence, without establishing the transport cause or adding retries.

Checking normal continuation also exposed a local collector failure: `save_cache` indexed `refresh_error` when merging an older passive reading with a newer saved observation. A passive reading intentionally omits that field because no active query ran. The merge now preserves the saved active outcome in that case, while retaining existing active-failure updates and managed-account merging. Isolated regression checks cover both providers, prior success/failure, and a newer recovery write interleaved before a scheduled collection save. A restored one-time query alone does not establish subsequent scheduler success; deployed collection results remain a separate check.

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
