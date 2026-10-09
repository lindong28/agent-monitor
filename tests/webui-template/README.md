# Template checks

These project bindings consume the installed user-scope `web-ui-workflows` runner. They are optional development tooling, not an Agent Monitor runtime dependency. `effective-design.json` pins Prompt Planet `style:vercel-gateway-console` v2; `project.json` owns selectors and safe, read-only states. [Product design](../../docs/design.md) owns project exceptions and observed visual coverage.

```sh
python3 ~/.claude/skills/web-ui-workflows/workflows/apply-ui-template/verify-ui.py \
  --design tests/webui-template/effective-design.json \
  --project tests/webui-template/project.json \
  --output /path/to/new/persistent/evidence \
  --jobs 2 --capacity-reason 'Two browsers share the Hub two-query-worker capacity and local CPU' \
  --timeout 45 --direct --screenshots
```

The output directory must be new. The live target is MacStudio; for an isolated preview, copy `project.json` into the evidence directory and change only `base_url`. The two-browser bound corresponds to the current shared Hub, not a universal runner default. This suite never invokes account sends, login, deletion or forced Refresh. Ordinary navigation can use the product's existing read-triggered refresh behavior.

The default suite requires populated accounts, chart observations, sessions, requests and network data. A missing sample remains UNCHECKED; do not convert it to NA to obtain a green report. The missing-session case tests an explicit empty detail, not a successful session lookup. Also enter a real session through the current list, expand metadata and usage, return to the list, and record that identity and observation in evidence. Session IDs are not baked into these reusable bindings. Requests use the current first row, with its real detail response as readiness.

The 15 manual rules preserve the full theme, eight component descriptions and six acceptance clauses. Their runner status intentionally remains UNCHECKED after the machine checks; visual reading and real interaction evidence are recorded separately. Machine PASS does not prove full design compliance, Canvas label visibility, table/chart value equivalence, business actions or response latency. Read desktop and narrow screenshots at readable scale and exercise filters, reset, keyboard expansion, history, dialogs and return paths. Check synthetic unknown/failure/authorization states in an isolated preview, never by sending a real message for styling validation.

For shared changes, rerun affected cases with repeated `--case ID`; other cases remain UNCHECKED. Frozen baseline and final results allow failing checks to be distinguished from successful ones. Do not import older PASS values or edit runner reports to close manual items.
