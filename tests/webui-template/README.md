# Shared UI checks

The current design is the directly observed Pulse workspace, recorded in [Product design](../../docs/design.md). `effective-design.json` binds local visual rules to that record; it does not claim a Pulse catalog template exists. `project.json` owns selectors and safe read-only samples; `scope.json` pins both files. The directory name is retained for existing tooling consumers. Ark and Vercel outcomes in the design history describe those earlier versions, not current acceptance.

The optional template verifier accepts the same local rule/binding structure:

```sh
python3 ~/.claude/skills/web-ui-workflows/workflows/apply-ui-template/verify-ui.py \
  --scope tests/webui-template/scope.json \
  --output /path/to/new/persistent/evidence \
  --jobs 2 --capacity-reason 'Two browsers share the Hub two-query-worker capacity and local CPU' \
  --timeout 45 --direct --screenshots
```

`base_url` names a task-only preview; supply a running read-only preview and refresh retained-session samples before using this optional batch. The full six-route coverage and current interaction observations belong in the [Pulse adaptation record](../../docs/plans/20261019-pulse-ui.md). Missing real samples remain UNCHECKED. Manual rules always require actual screenshot reading and user journeys; machine token/geometry checks cannot establish full visual acceptance. Do not send messages, authorize accounts or remove history to validate styling.

## Historical quota and chart failure samples

`check-overview-layout.py` exercises the production HTML, CSS and renderers with two synthetic accounts, long identity text, 37%/99% historical values, and $29,374.52/$12.34 chart amounts. It blocks every network request and never calls account login, send, removal or forced refresh. Install Playwright separately if unavailable; it is development tooling only. Supply the existing vendored Chart.js path explicitly:

```sh
/usr/bin/python3 tests/webui-template/check-overview-layout.py --chart-js /path/to/agent-monitor/web/vendor/chart.umd.min.js --output /path/to/new/persistent/evidence
```

The script checks 390×844, 1024×844 and 1440×1000, four requested horizontal offsets (desktop may have no overflow), actual canvas text painting, search/clear and filter → table → account-operation ordering. The original 64px chart padding is a deliberate positive control inside the isolated browser; the production 96px setting is restored before screenshots. The current provenance checks follow the October 18 label decision: each row keeps its historical qualification in the observation column, reachable at the end of horizontal scrolling. Inspect the screenshots separately: geometric PASS is not a complete visual judgment or a production deployment assertion. `project.json` retains live history and current workspace-order checks; it does not claim keyboard steps reproduce a precise horizontal offset. The isolated script owns that repeatable sample.

2026-10-10 evidence: `/Users/lindong/.codex/artifacts/monitor-coverage-20261010/`. `baseline` preserves the original failure readings; the final draw-capture run is identified in `docs/design.md`. The newer account-action provenance branch is covered by `tests/test_quota_explorer.py`; a fresh account reading must not be relabelled as the old machine observation. Current-template SHA changes must update `effective-design.json` and then `scope.json`; historical reading snapshots remain historical.
