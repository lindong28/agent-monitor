# Explain Codex pauses at the account action

Accepted: 2026-10-28. Scope: dashboard explanation and navigation; no change to provider admission, recovery approval or snapshot schemas.

A shared local Codex pause disabled the next-round button while the account panel only reported the previous successful batch. Repeated account-level resume buttons also conflated account restrictions with the shared provider pause.

Show the blocking reason beside the batch controls and in the collapsed summary. Match the current pause ID to exactly one complete machine/account record in the existing quota snapshot, and navigate to that record's existing recovery control. Missing, ambiguous or unreadable records keep protection and explain why navigation is unavailable. Account resume only clears that account's restrictions; it no longer sends a shared pause ID.

Reusing the existing recovery control preserves its explicit confirmation, one-probe limit, identity validation and cache-save requirement. A new endpoint or copied recovery implementation would duplicate those responsibilities. A tooltip alone would leave the mixed resume action unresolved. Navigation does not query a provider, clear a pause or send a batch.

The independent decision review passed all seven criteria. The observed RPC failure does not establish its underlying network or provider cause, and frontend acceptance cannot establish live recovery. Real recovery requires approval for the specific probe on the account-owning machine and its existing egress. This record was added after the first frontend edits; that sequencing missed the decision gate's before-edit recording requirement, without changing the reviewed scope.
