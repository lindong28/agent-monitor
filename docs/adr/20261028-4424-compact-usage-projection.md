# Compact retained usage projection

Date: 2026-10-28

The storage optimization request preserves retained raw facts, accounting and complete historical consumers. Following the incremental collection ADR, derived usage will store the previous bucket contribution independently of the mutable raw record, together with changed derived fields. Complete consumers reconstruct entries by joining raw facts with those fields. This replaces duplicate complete events without deleting history or periodically discarding incremental state.

The old contribution retains bucket identities, counters and the exact floating-point cost required by the existing integer cost accumulator. It remains usable after raw correction or deletion. Source-code signatures force a transactional rebuild on representation changes and on downgrade/re-upgrade; the existing three-column table interface stays compatible with previous writers.

Only compacting JSON separators still retains unused duplicate fields. Compression of complete objects was not benchmarked; this decision removes unnecessary fields before considering a codec. An overlay alone is insufficient because correction or deletion changes the raw row before the old contribution is withdrawn.

This decision changes internal representation, not retention, pricing policy, snapshot schema or query semantics. First migration is proportional to retained history; ordinary dirty-key updates remain incremental. Before deployment, isolated tests and a consistent archive copy must verify complete output, accounting, correction/deletion, rollback, downgrade/re-upgrade and unchanged-round work. Physical file compaction remains a separate maintenance operation; reusable SQLite pages are not reclaimed filesystem bytes.

One independent decision review approved this scope. Real dataset benefits and deployed behavior remain subject to implementation acceptance; no live migration is implied by this decision record.
