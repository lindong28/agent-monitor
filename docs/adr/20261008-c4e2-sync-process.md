# Isolate Hub snapshot synchronization

Status: Accepted for local implementation on 2026-10-08 after one independent decision review; publication and deployment require separate authorization.

## Decision and scope

Run each Hub `sync_all` round in a fresh, supervised Python process. Keep the existing per-machine concurrency, statistics-to-quota progression, validation, retention, source identity and cadence. Return only explicit per-machine outcomes and a final result; generation leases remain in the worker. The Hub retains refresh counters, pending/queued state, and account-memory updates coordinated with account deletion. No new permanent service, external dependency, Gateway change or snapshot schema is introduced.

The worker exits when its round finishes or its parent control pipe closes, including same-PID exec. The EOF watcher needs Python execution time; explicit parent shutdown terminates and waits for the worker process group before reexec, including while the worker holds the GIL. Existing quota subprocesses create their own session and retain their existing timeout ownership. Malformed progress, missing final results and abnormal exit are failures, not successful refreshes. Existing sync-status observations remain the failure surface. Unattended push notification coverage remains the existing agent-monitor backend/operations TODO; this change does not introduce a new alert channel.

## Evidence and alternatives

`c37ce00` moved a measured 27.051s retention check outside the publication lock and indexed detail reads, yet two production requests still rendered in 15.096s/0.113s/12.909s. A separate process on the same host executed the detail function cold in 1.013s. Three paired loopback HTTP/direct-function probes measured 4.968s/1.007s, 0.044s/0.012s and 0.053s/0.007s. A live Hub sample showed JSON/GC work and the main thread waiting for Python execution, but was not simultaneous with the slow clicks. This supports an isolation experiment, not a precise attribution or latency guarantee.

Retaining only the lock change has not removed the residual wait. Moving retention alone leaves self-export and import validation allocating full histories in Hub threads. Rewriting validation as SQL/streaming changes more shared semantics; rewriting the full call-list projection is a separate, larger change. Process isolation preserves the existing operations while removing their Python heap and execution-lock competition with HTTP.

The independent reviewer confirmed all seven decision criteria and identified same-PID `os.execv` cleanup and parent-owned account deletion coordination as required implementation boundaries. Implementation review found no blocking issue and identified process-local validation caches as a residual cost: parent account-memory updates or the first HTTP request still validate each new generation. This remains with the performance issue; removing validation is outside this decision. The earlier calls-explorer and extraction ADRs remain compatible: neither requires synchronization to share the HTTP process. Real concurrent production performance remains unverified; HTTP's own full-history list reads and shared CPU/disk pressure are not resolved by this decision. Rollback restores the previous source implementation without rolling back saved generations.
