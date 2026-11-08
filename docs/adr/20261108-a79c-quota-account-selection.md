# Quota account selection

Accepted 2026-11-08. One independent decision review approved this scope before implementation; implementation and browser validation remained pending at decision time.

The requested reader tasks are to distinguish disabled accounts in red, collapse them with historical accounts by default, and order quota rows by remaining quota or by remaining quota with an approaching reset. Managed account state is already available to Overview; match it by workspace ID and email without merging the managed and collected identity sets. Disabled state changes must invalidate the rendered view even when the quota observation is unchanged. Display grouping does not change persisted presence, history retention or removal eligibility.

Reuse the existing sort and quota-window controls. Add a combined ordering that prioritizes a known remaining balance in the selected window and a future reset, then orders by ascending reset and descending remaining balance for equal resets. Other rows remain stable at the end. Apply selected ordering across providers within the active and collapsed groups; preserve existing provider order for the default sort. This compares recorded percentages within the selected window, not absolute tokens, subscription capacity or actual account availability. Other window values and observation age remain visible.

The existing reset-only ordering can put exhausted accounts first, so it cannot satisfy the combined task alone. A weighted recommendation score has no agreed interpretation and is unnecessary. A new backend field or storage migration would duplicate state already delivered to the browser. Keep the existing history label when there are no disabled rows; use a combined label when there are. Retain account management links for disabled accounts while omitting their direct quota-refresh action.

Related boundaries remain in [dashboard design](../design.md) and [usage](../public-usage.md): unknown values stay unknown, historical values do not assert present availability, and provider operations retain their existing approval gate. No provider request is added by filtering or sorting.
