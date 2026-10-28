# Invalidate derived usage by actual price dependency

Date: 2026-10-28

The performance/storage request requires avoiding work on unrelated history while preserving the existing pricing policy and historical accounting. Calculation source changes still rebuild the projection. Catalog changes instead compare the resolved price identity, complete ordered price record and provenance source for each raw model actually present in the archive. Only events of changed dependencies become dirty. A model-count table maintained by independent raw-event triggers and an expression index avoid discovering/selecting those events with a full history scan each round.

Whole-catalog invalidation repeats work for unused models. A field whitelist risks omitting threshold, mode, geography or provenance dependencies. Complete selected records are a conservative smaller invalidation scope. Records must preserve recursive member order: the current pricing algorithm can choose different equal numeric threshold spellings according to insertion order. This change tracks that existing behavior without changing token_cost or price admission.

Raw facts, accounting formulas and full event consumers remain unchanged. Initial model-index/count construction and code-version changes may scan history once. Older writers continue to fire the new independent triggers; a downgrade and upgrade may rebuild derived state but must not lose facts. Model appearance/disappearance, unknown/known pricing, nullable models, provenance and ordered thresholds need isolated regression verification before deployment. This does not claim that all updates or complete exports become proportional only to new events.

A single independent decision review approved the scope, including ordered price dependencies. The 433,562-event archive contains 15 raw models; savings depend on which model changes and are not inferred from that count alone.
