# Navigable retained-session detail

Status: accepted for local implementation, 2026-10-07. Publication and live deployment require separate authorization.

## Context and decision

The owner requested continuing the Vercel AI Gateway UI adaptation roadmap with agent-monitor session detail. The existing inline turn list had no object URL and grew inside the list. A full-width detail page now provides session identity, four summary metrics, disclosed metadata and a model-filtered, paginated usage table. List filters, ordering and page survive navigation. This replaces UX contract D3; it does not change collectors, API schemas, retention or backend performance.

The real Vercel public model page was opened and its Providers section selected during this unit: https://vercel.com/ai-gateway/models/claude-opus-5.5 . Object title, copy identifier, section hierarchy and detail tables inform the relationship pattern. Earlier authenticated request/API-key observations are recorded in ai-agent-config's `docs/references/vercel-ui-capability-map.md`. Vercel does not provide evidence here of a transcript-session feature; the adaptation is not a claim of full visual or functional equivalence.

An inline view could gain a URL, but would still compete with the parent list for space. A narrow request drawer is suitable for one request; a session can contain many models and retained entries. The existing visual tokens remain in use. Mobile observation exposed the existing brand/navigation flex overlap; preventing their shrink keeps the horizontal navigation readable.

## Identity and accounting

Session identity is machine plus session ID. Detail reads the full retained session, independently of the list range. Metadata uses all entries rather than `_session_stats`' first-entry model/project/agent fields. Token totals use the same four components as `extract_metric('total')`; costs remain unknown if any component is unknown. Source pricing is not represented as an invoice. The span between first and last records is not active duration; usage events are not necessarily requests.

Claude request IDs come from provider logs; Codex IDs are synthetic `token-count:<line>` values. No verified session-to-Gateway request mapping exists in these fields. Attempt-chain linkage remains a later mapping task, owned by the roadmap; it must not be approximated from model/time/ID resemblance. Agent-monitor backend performance and pre-existing assertions remain assigned to the owner's other session.

## Validation and limits

The independent decision review allowed this local unit. One implementation review found that detail Refresh only reread a snapshot; the fix restores collection before reading, and the existing 30-second/focus lifecycle. The original reviewer verified the fix with actual `initSessions()` calls. A regression test exercises this path against the real app.js.

Browser checks used an isolated preview with synthetic records: 105 records, two models, zero/small-positive/unknown costs, two machines sharing an ID, and an empty result. Checks covered filtering/clear, page two, list context restoration, deep-link/reload/back, escaped identifiers, failure/retry and a delayed response after leaving. One fixture click-to-detail sample was 16 ms; this is not a production performance result. Desktop 1440×900 and mobile 390×844 were inspected; the child table scrolls horizontally on mobile. Evidence and rerunnable browser driver: `~/.codex/artifacts/session-detail-phase44-20261007/`. Production UI, original-browser zoom and paid calls were not tested in this unit.

The reusable object-usage-detail candidate is prepared under Prompt Planet `review/object-usage-detail-v1/`; formal admission remains subject to approval of that concrete content. No new skill policy or shared component package is required.
