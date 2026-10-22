# One bounded quota probe while paused

Accepted 2026-10-22 after independent decision review. The user approved investigating and recovering the MacBook quota path and distinguishing paused from skipped machines. The approved account was `lindong28@gmail.com`; the current local identity differs, so no live probe is authorized by this decision alone.

Keep the provider pause in place while admitting an explicitly requested probe under the existing provider lock. Persist one `probe_attempt` slot inside the matching pause before starting a child. Reuse the bounded quota reader, without message sending, login, account switching or retries. Ordinary callers still cannot enter. A spent slot survives success and interruption; failure keeps the old pause in `last_pause` and creates a new pause. A new pause is not authorization to retry. This replaces the proposed resume-then-query sequence, whose unlocked gap lets a background collector enter first. Direct reader calls bypassing the lease were rejected.

The explicit recovery helper checks the expected local identity, reads only Codex, checks identity again, persists that provider's reading through the existing cache writer, then resumes only the original pause ID. Any failure before completion leaves the pause in place. No scheduler or Web caller invokes this helper. One business quota read does not promise only one HTTP request inside the Codex CLI. A crash after consuming the slot can prevent a probe that never reached the provider; this conservative ambiguity is intentional.

Retain the existing file storage: one local provider writer owns the stable flock, atomic JSON replacement persists admission, and the cache has its own lock. Current plus previous pause and one attempt slot remain bounded; no database or growing retry ledger is needed. This synchronous operator path reports failures directly and adds no unattended service or push alert requirement.

UI summaries count the application's outer paused, skipped and busy messages separately from other query errors. Nested cause text cannot change the outer classification. Raw details remain available; old skipped records without an originating machine do not acquire an invented one.

Historical evidence: Codex daemon model-list failures near the 2026-10-21 pause include timeouts and a later `401 token_revoked`, but no matching account or pause identifier. These are clues from another call path, not a proven initial cause. The current legacy pause has no original diagnostic; other historical evidence has not established causality.
