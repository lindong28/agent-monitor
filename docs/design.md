# Dashboard presentation

`web/styles.css` is shared by Overview, Explore, Sessions, Collection, Network and Guide. This guide preserves the implemented presentation boundaries without requiring the original template service or another project's UI at runtime.

Use the existing CSS tokens and components for typography, neutral surfaces, tables, disclosures and actions. The dashboard uses Chinese labels. Keep account identity, quota value, reset/observation time and available action associated in the same row or narrow-screen card. Put traceability in accessible details, while keeping limitations that change interpretation beside the affected result. Do not repeat a conclusion already clear from the value or group context.

Agent-only trends use stable colors: Claude Code blue `#2a78d6`, Codex orange `#eb6834`, independent of order/date/filter. Comparable rankings use the existing horizontal bar presentation with category, numeric scale and end value; do not replace these with ambiguous progress tracks. Preserve drill-through context between totals, filtered sessions and details.

Overview groups its three cost readings in one panel; only the selected-range reading and trend follow the range selector. Explore groups dates, presets, query summary and advanced controls, with shared result explanations for charts/rankings. Daily pages omit the machine-status box; current machine health and technical evidence belong on Collection. Manual-refresh receipts and cost-completeness qualifiers remain beside the relevant actions and values.

Keep navigation, page heading, filters and expanded content usable at narrow widths. A CSS change in a shared component affects every consumer, including expanded rows and error/empty states. Use the README sample to inspect actual session navigation, plus the existing Web tests, before claiming a UI change is usable. Screenshots or aggregate test counts alone do not establish the reading/task flow. Accounting, identity and provider-action semantics are defined by the backend, not by presentation changes.
