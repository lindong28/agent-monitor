# Cached-first system network detection

Accepted 2026-11-10 after the maintainer requested implementation of the proposed network-page gap fixes. Partially supersedes [the unified dashboard decision](20261031-unified-dashboard.md): network page entry is no longer cache-only. System-only scope, account identity and provider egress approval remain unchanged.

On page entry, render saved results immediately. When no attempt exists or its cache/cooldown has expired, issue one same-origin POST through the existing system diagnostic endpoint. Manual detection uses the same admission. No periodic probing, model-provider probe or process scan is added. Keep the existing 60-second cache and longer upstream Retry-After; a click does not bypass them.

NetworkReport owns one latest-attempt/last-usable-result record in state/network_report.json, bound to the serving hostname. Restore the result and wall-clock retry deadline at startup; do not persist monotonic timestamps. Atomically replace this bounded record after an attempt. Credential-bearing diagnostic strings are sanitized and bounded before storage. This record is runtime data, excluded from Git, and does not alter usage/account snapshot schemas or existing state symlinks/locks. An older release ignores the new file; unreadable or incompatible records produce a visible warning and can be replaced by a subsequent diagnostic.

Serialize diagnostic execution separately from the short cache lock so cached reads do not wait for public I/O. Keep a prior usable result when the command or public-IP stage fails; preserve the failed attempt alongside it. The UI and terminal identify retained results as historical, preserve observation time and expose retry timing. A partial risk/reputation result retains its existing coverage semantics. Save failures leave the current in-memory result usable and warn that restart recovery is unavailable.

Alternatives: frontend-only auto-clicking leaves restart loss, failure replacement and read blocking unresolved. An always-running scheduler adds requests without a reader. A database service adds lifecycle/dependency costs for a single bounded per-host record. The selected change reuses the existing state directory and diagnostic admission.

Verification belongs to task evidence: isolated lifecycle tests, a separate-process restoration check, and real browser consumption of synthetic probes. These do not establish live upstream availability; deployed consumer and authorized upstream verification are separate checks.
