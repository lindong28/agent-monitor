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

The nine manual rules preserve VG-V01–VG-V09; 22 machine rules check supporting tokens, geometry and preserved regressions. Their runner status intentionally remains UNCHECKED after the machine checks; visual reading and real interaction evidence are recorded separately. Machine PASS does not prove full design compliance, Canvas label visibility, table/chart value equivalence, business actions or response latency. Read desktop and narrow screenshots at readable scale and exercise filters, reset, keyboard expansion, history, dialogs and return paths. Check synthetic unknown/failure/authorization states in an isolated preview, never by sending a real message for styling validation.

For shared changes, rerun affected cases with repeated `--case ID`; other cases remain UNCHECKED. Frozen baseline and final results allow failing checks to be distinguished from successful ones. Do not import older PASS values or edit runner reports to close manual items.

The 2026-10-09 evidence is under `/Users/lindong/.codex/artifacts/agent-monitor-vercel-20261009/`: `final-live` is the full run, `final-live-reading` preserves its chart-label finding, and `chart-final` / `chart-final-reading` record the affected-case repair verification. Each observations file binds the original results SHA256 and actual evidence hashes. The partial report intentionally stays UNCHECKED for unselected cases and unrelated reading rules; the current outcome table in `docs/design.md` states which revision supplies each conclusion. Neither raw report is relabeled as an independent complete-scope pass.
