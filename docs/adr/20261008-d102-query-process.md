# Isolate full-history HTTP queries

Status: Accepted for local implementation on 2026-10-08. The owner approved a limit of two concurrent full-history queries; publication and deployment require separate permission.

## Decision

Run the Hub's calls, calls-page, call-filters and calls-export HTTP projections and their JSON encoding in fresh supervised processes. Retain the existing Gateway functions, query semantics, source admission, generation leases, complete export and response schemas. The HTTP parent receives status and encoded bytes after checking worker exit and response length. It does not decode the business JSON. Detail and health requests do not acquire query slots. Standalone mode keeps its existing direct path.

Two slots bound simultaneous full-history heaps; subsequent full requests wait for a slot before admission. Workers share the sync supervisor's shutdown gate and process-group cleanup, including same-PID reexec. Parent-pipe EOF also terminates a worker. Existing HTTP 500 handling reports execution/protocol failure; existing projection errors retain their status and body. There is no new persistent service, provider request, Gateway dependency or schema change.

## Evidence and tradeoff

After sync isolation, the real 30d page still took 37.773s and a new-generation detail took 8.132s before a 0.044s repeat. Inspection found these four HTTP paths still decode/project full snapshot histories in the parent. Page filters and rows already share an observation; Gateway's local-ledger cache/index cannot directly serve this multi-source snapshot path. The parent reached approximately 12.0GB RSS while the separate sync worker reached 13.4GB. MacStudio reported 128GiB physical memory and 16 logical CPUs at the decision point.

The owner selected two concurrent query tasks after this cost was surfaced. This separates Python execution/GC contention; it does not improve the full-history algorithm or promise shorter list loads. Fresh workers repeat admission and allocations. The parent retains the complete encoded response in bytes, so large exports still consume memory there. Cold detail admission, shared disk/CPU pressure and snapshot refresh remain separate performance surfaces. No small response cap or shorter timeout is introduced. One independent decision review found all seven criteria satisfied; implementation and live acceptance are separate.
