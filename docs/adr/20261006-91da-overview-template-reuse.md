# Overview template reuse

Status: implemented locally, 2026-10-06. Remote publication and production deployment await separate authorization; neither is claimed here.

The owner selected agent-monitor as the second product for the Web UI template reuse roadmap. Apply `preset:usage-observability-console` version 1 from Prompt Planet to `/`, retaining this project's blue visual system, statistics and account semantics. The published definition matches the local version archive byte for byte.

Group today's, this week's and the selected range's cost summaries with the existing cost trend on one surface. Keep their original order and labels: only the third summary and the trend follow the range selector. Keep machine coverage and incomplete-data warnings visible, the account quota table full width, and the fixed weekly directory/monthly model charts with their existing drill-down links. No new metrics, charts, requests or backend changes are needed.

A literal four-metric grid would invent unavailable metrics or conflate windows. Leaving the chart below the quota table separates the selected total from its history. Account cards would lose the comparison columns and repeat the previously observed wide-layout waste. Existing disclosure behavior from ADR 20260826-c80f and the accounting and range constraints remain in force.

Scope: overview markup and scoped styles only. No shared component package or cross-project default is introduced. The reusable part is the relationship between filters, values, trends and drill-down; the blue theme, account quotas, machine coverage and fixed windows remain project overrides.

One independent L1 decision review passed all seven criteria. Backend latency, the live version-drift banner and existing range-scope/contract-drift issues remain outside this change. A local revert restores the previous layout; this does not itself roll back a deployment.

## Local validation and boundaries

The real `http://macstudio:39001/` baseline was read without refreshing or restarting it. Its version-drift banner was present and one overview resource took 11,411 ms. This observation does not establish a latency distribution or a performance improvement.

The isolated preview serves this checkout's HTML/CSS/JavaScript against synthetic read-only data: two machines, two current and two remembered accounts, two agents, two directory/model rows, and distinct 7-day/30-day series. Selecting 7d changes the selected total to $21 and the curve to seven buckets; today's $4.25 and this week's $17.50 remain fixed. The trend link opens Explore with range=7d, day/agent/cost context, and the overview navigation returns with the range retained. Cost explanation and remembered-account disclosure were opened and read; quota unknown values and the 82% warning remain visible. This is frontend fixture validation, not production backend E2E.

At 1440×900 the cost curve starts at y=318 and quota rows at y=715; values and curve can be read together. At 390×844, today's/week's values share a row and the range spans the next row; the chart is 324 CSS px wide and the 1040px quota table scrolls within its region. Sampled widths 1800, 1440, 1152, 901, 899 and 390 have no document horizontal overflow. The first three simulate 80%/100%/125% zoom in one 1440px-wide window: 26px cost text becomes 20.8/26/32.5 screen pixels and the chart becomes 256/320/400 screen pixels tall. This does not cover every width, native zoom or mobile hardware.

`interaction-latency` using an explicitly connected isolated browser measured the cost-explanation disclosure at 37.3 ms on its first measured opening and 38.2 ms on repeat; clicking the unchanged trend label returned no change in both 0.5s controls. These are warmed local page samples, not cold navigation or production response measurements. LCP and first meaningful data paint were not measured. `visual-budget` on the populated 1440×900 preview reports 13 repeated elements, 6 saturated colors, 4 hue buckets, 5 border styles, 4 radii and 1.4 page screens; these are descriptive counts, not aesthetic acceptance.

Coverage: deep review of the cost panel and range/drill-down flow; adjacent regression of account quotas, disclosures, machine coverage and the two fixed-window charts. Other product routes, refresh/restart/account deletion, backend assertions, production latency and complete accessibility/UX-contract acceptance are excluded. The previously recorded range-scope and contract-layer drift remain in `docs/issues/ux-issues.md` and `docs/issues/ux-contract-issues.md`.
