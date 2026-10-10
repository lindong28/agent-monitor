# Template checks

These project bindings consume the installed user-scope `web-ui-workflows` runner. They are optional development tooling, not an Agent Monitor runtime dependency. `effective-design.json` pins the current Prompt Planet `style:vercel-gateway-console` source and SHA256; `project.json` owns selectors and safe, read-only states. [Product design](../../docs/design.md) owns project exceptions and observed visual coverage.

```sh
python3 ~/.claude/skills/web-ui-workflows/workflows/apply-ui-template/verify-ui.py \
  --scope tests/webui-template/scope.json \
  --output /path/to/new/persistent/evidence \
  --jobs 2 --capacity-reason 'Two browsers share the Hub two-query-worker capacity and local CPU' \
  --timeout 45 --direct --screenshots
```

The output directory must be new. The live target is MacStudio; for an isolated preview, copy `project.json` into the evidence directory and change only `base_url`. The two-browser bound corresponds to the current shared Hub, not a universal runner default. This suite never invokes account sends, login, deletion or forced Refresh. Ordinary navigation can use the product's existing read-triggered refresh behavior.

The default suite requires populated accounts, chart observations, sessions, requests and network data. A missing sample remains UNCHECKED; do not convert it to NA to obtain a green report. The missing-session case tests an explicit empty detail, not a successful session lookup. Also enter a real session through the current list, expand metadata and usage, return to the list, and record that identity and observation in evidence. The session-detail binding pins the actual sample observed in this run; choose a current retained session before a future run and update the project digest in scope.json. An unavailable sample remains UNCHECKED. Requests use the current first row, with its real detail response as readiness.

The nine manual rules preserve VG-V01–VG-V09; 24 machine rules check supporting tokens, geometry and preserved regressions. Their runner status intentionally remains UNCHECKED after the machine checks; visual reading and real interaction evidence are recorded separately. Machine PASS does not prove full design compliance, Canvas label visibility, table/chart value equivalence, business actions or response latency. Read desktop and narrow screenshots at readable scale and exercise filters, reset, keyboard expansion, history, dialogs and return paths. Check synthetic unknown/failure/authorization states in an isolated preview, never by sending a real message for styling validation.

For shared changes, rerun affected cases with repeated `--case ID`; other cases remain UNCHECKED. Frozen baseline and final results allow failing checks to be distinguished from successful ones. Do not import older PASS values or edit runner reports to close manual items.

The 2026-10-09 evidence is under `/Users/lindong/.codex/artifacts/agent-monitor-vercel-20261009/`: `final-live` is the full run, `final-live-reading` preserves its chart-label finding, and `chart-final` / `chart-final-reading` record the affected-case repair verification. Each observations file binds the original results SHA256 and actual evidence hashes. The partial report intentionally stays UNCHECKED for unselected cases and unrelated reading rules; the current outcome table in `docs/design.md` states which revision supplies each conclusion. Neither raw report is relabeled as an independent complete-scope pass.

## Historical quota and chart failure samples

`check-overview-layout.py` exercises the production HTML, CSS and renderers with two synthetic accounts, long identity text, 37%/99% historical values, and $29,374.52/$12.34 chart amounts. It blocks every network request and never calls account login, send, removal or forced refresh. Install Playwright separately if unavailable; it is development tooling only. Supply the existing vendored Chart.js path explicitly:

```sh
/usr/bin/python3 tests/webui-template/check-overview-layout.py --chart-js /path/to/agent-monitor/web/vendor/chart.umd.min.js --output /path/to/new/persistent/evidence
```

The script checks 390×844 and 1440×1000, four requested horizontal offsets (desktop may have no overflow), actual canvas text painting, search/clear and batch/filter ordering. The original 64px chart padding is a deliberate positive control inside the isolated browser; the production 96px setting is restored before screenshots. The historical group and provenance checks fail on the pre-fix source. Inspect the screenshots separately: geometric PASS is not a complete visual judgment or a production deployment assertion. `project.json` retains live history and workspace-order checks; it does not claim keyboard steps reproduce a precise horizontal offset. The isolated script owns that repeatable sample.

2026-10-10 evidence: `/Users/lindong/.codex/artifacts/monitor-coverage-20261010/`. `baseline` preserves the original failure readings; the final draw-capture run is identified in `docs/design.md`. The newer account-action provenance branch is covered by `tests/test_quota_explorer.py`; a fresh account reading must not be relabelled as the old machine observation. Current-template SHA changes must update `effective-design.json` and then `scope.json`; historical reading snapshots remain historical.
