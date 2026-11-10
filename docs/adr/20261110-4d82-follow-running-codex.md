# Follow running Codex accounts for quota updates

Status: accepted; independent decision review passed on 2026-11-10. Implementation and deployment evidence are separate.

## Context and decision

Named `codex-account` tasks use isolated credentials and shared session history. History cannot identify the executing account. Static quota enrollment is useful, but does not follow whichever accounts are currently running tasks. Keep manual identity-bound enrollment and add an independent, default-off `follow_running` opt-in that includes present and future registered profiles while their tasks run. Use the existing quota interval (one hour for this installation), execution route, pause controls and egress approval. Starting a task does not synchronously query quota; ending it does not request a final reading.

The launcher writes one atomic local record per actual coding child in `<profile>/running-tasks/<uuid>.json`; normal exit removes its own record. Version 1 binds profile name, complete profile identity (`account_id`, `email`, `subject`), PID, native process start identity and start timestamp. Linux identity is process start ticks, boot ID and UID; Darwin identity is start seconds/microseconds and UID. No credentials, prompts or command lines are recorded. Definitively dead/reused records are removed on a later launch; malformed or unreadable records remain diagnostic evidence. Unknown process identity never enrolls an account.

Only the coding `run_once` path requests registration; login/logout/check/help/version are excluded. Monitor reads this small public file protocol independently, without a runtime dependency on the launcher repository. It validates the full profile binding and native process identity, counts concurrent tasks, and makes at most one account refresh due per interval. Follow-only accounts are revalidated immediately before dispatch because a batch can wait after its initial activity scan. Manual selection is unaffected by task exit.

The settings panel reads live activity and its observation time. Activity is not added to quota snapshots: quota observation time must remain the time of the provider reading. Enabling follow uses the existing one-time egress confirmation, bound to the machine, selected fixed profiles, revision and follow mode; its text explicitly covers future running registered profiles. Disabling follow stops future dynamic enrollment but cannot cancel a request already dispatched.

## Alternatives and consequences

Shared logs cannot safely attribute execution. Global process scans cannot reliably recover account identity; held locks also cover maintenance. Launcher-side polling or a new heartbeat daemon would duplicate scheduling and failure controls. Therefore use child lifecycle records and the existing scheduler. Several tasks on one account share one quota query; running accounts remain independent of the default account. Pre-upgrade tasks have no records and are not inferred retroactively. macOS/Linux process probes are supported; unsupported or unreadable probes report unknown. Registration failure warns without preventing the coding task.

This extends, rather than replaces, identity-bound manual enrollment in `20261103-9481-codex-quota-execution`. Shared usage attribution and historical snapshot schemas remain unchanged. Turning off follow is the rollback for automatic eligibility; prior quota readings and manual selections remain available.
