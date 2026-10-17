# Focus account operations and recover invalid dates

Accepted after one independent L1 decision review on 2026-10-18. This supersedes only the quota-row navigation/query choice in [82cb](20261017-82cb-task-oriented-web-actions.md); batch protection and all other behavior remain in force.

Quota comparison rows no longer repeat account navigation and quota-query buttons. The adjacent “Codex 账号操作” disclosure is the common entry for individual quota queries, individual sends, selected-account rounds and recovery. Historical-record removal stays in the table. The panel keeps account identity, authorization, busy state, previous results and definitely-unsent-only resume semantics.

Keeping only the navigation shortcut would preserve exact workspace/email positioning. We instead accept locating the account again in the adjacent panel to keep comparison and execution in one place each. The observed deployment has 11 cards with distinct emails; this is a structural simplification, not a measured speed improvement. Reassess positioning if account scale or duplicate identities make discovery costly. No new menu, drawer, polling or provider operation is introduced.

Sessions must keep incomplete custom dates visibly incomplete, explain missing/invalid/reversed dates before requesting data, and recover when those same inputs are corrected. Valid ranges and cross-page context retain their existing meaning.

The authorized task is a full product-based UX review and implementation. This routine reversible presentation change does not authorize provider sends, login, credential removal or historical-record deletion as verification. Frozen input, raw browser observations, decision packet and review are under `/Users/lindong/task-artifacts/monitor-ux-round2-20261017/`; production verification is recorded separately.
