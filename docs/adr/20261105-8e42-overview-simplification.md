# Overview information and action simplification

Accepted 2026-11-05 after the operator requested implementation of the overview simplification review. One independent decision review approved the scope before implementation.

The overview must support comparing dated quota observations, understanding current restrictions, and finding the correct account operation without mistaking historical success for current availability. Keep raw observations, account histories and diagnostics accessible. Preserve accounting windows, provider confirmation, restrictions and account identity.

Use short per-row states with detailed explanations in the existing observation disclosure. Show the sorting window only for quota/reset sorting. Explain ordinary plan provenance once while retaining fallback, unknown and differing-source states. Simplify redundant navigation and time labels without changing cost arithmetic or date scope.

Keep the quota and managed-account collections distinct: their membership differs. A quota row may locate an existing operation card only by matching both workspace ID and email. Reuse the managed profile selection for selected quota queries and sending, with separate action groups; selecting all retains whole-set query capability. Do not turn the quota filter into an implicit send selection. Reuse the existing operation and egress-confirmation paths.

Show current pauses and active work next to actions. Retain dated completed receipts in operation history and each account's existing details. Keep retry/continuation actions accessible. Move random-message and connection implementation explanations into the existing help; leave quota consumption and any actionable restriction visible.

Deleting either account collection would lose its distinct consumers. Adding another account page would add navigation and selection-state handling without being required by this task. Adding disclosures alone would not resolve duplicate action scopes or misleading current-versus-historical states. The chosen design reuses the existing overview and account module boundaries.

This decision refines the presentation in 20261105-655f2-quota-plan-sources and the existing dashboard design; it does not change quota interpretation, subscription entitlements, account eligibility, provider scheduling or recovery policy. Before delivery, verify normal and paused states, asynchronous account loading, complete identity matching, filtering/sorting, retained details and narrow-screen interaction. Implementation and provider availability were unverified when accepting this decision; real provider requests are not needed for this presentation acceptance.
