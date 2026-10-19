# Pulse reference for the Monitor interface

Accepted 2026-10-19. Supersedes the visual tokens and shell placement in [7d2f](20261014-7d2f-ark-console-style.md) and [c82a](20261014-c82a-ark-top-navigation.md); their navigation, search, copy and context-preservation behavior remains effective.

The user explicitly selected agent-monitor as the target, direct adaptation of Philo Pulse, and implementation through acceptance. Authenticated observation of Pulse's feed, dashboard and page-sharing interface found a common white sidebar, neutral gray canvas, thin-bordered white cards, 24px page titles and restrained blue actions. Its separately authored report has a different editorial presentation; that does not replace the workspace reference.

Consolidate Monitor's brand, area switch, search and local navigation into a white desktop rail. Use the available main width directly on the neutral canvas, with open cost summaries and white sections. Preserve console/guide return addresses, local filters, account identities and actions, known/unknown cost semantics, technical evidence, chart meanings and existing data requests. Narrow navigation is a Monitor adaptation, with a compact header and horizontally scrollable destinations. No new framework or runtime dependency is needed.

Returning to Pulse's upstream references would re-decide a combination the user already prefers. Creating a reusable template first would add an unverified deliverable. Recoloring the Ark shell would leave the requested layout redesign unfinished. The shared implementation is local to Monitor; template extraction is optional after actual reuse demand appears.

Independent decision review: one read-only reviewer, seven criteria all 成立, verdict 放行; no findings. Historical decisions and observations were checked in the current ADR corpus, design record and UX contract. This accepts the approach, not its implementation. Full-page, narrow-screen, interaction and live release results remain unverified at this decision point; the implementation plan records their eventual disposition. Git reversal is the rollback path.

Reference screenshots, computed-style observations and reviewer receipt: `/Users/lindong/task-artifacts/agent-monitor-pulse-ui-20261020/`. Execution and acceptance: [plan](../plans/20261019-pulse-ui.md).
