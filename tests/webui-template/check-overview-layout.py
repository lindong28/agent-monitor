"""Offline regression: production layout/renderers, synthetic accounts, no provider I/O."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--chart-js", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
html = re.sub(r"<script\b[^>]*>.*?</script>", "", (ROOT / "web/index.html").read_text(), flags=re.S)
html = re.sub(r"<link\b[^>]*>", "", html)
source = (ROOT / "web/app.js").read_text().replace(
    "window.AgentMonitor = {", "window.AgentMonitor = { renderQuotaAccounts, renderOverview,")
bindings = json.loads((ROOT / 'tests/webui-template/project.json').read_text())['roles']
identity = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['web/app.js','web/styles.css','web/index.html','tests/webui-template/project.json']}
(args.output/'source-sha256.json').write_text(json.dumps(identity,indent=2)+'\n')
observations = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--mute-audio"])
    try:
        for width, height in [(390, 844), (1440, 1000)]:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_default_timeout(10000)
            page.route("**/*", lambda route: route.fulfill(status=200, body="{}", content_type="application/json"))
            page.goto("http://fixture.test/fixture")
            page.set_content(html)
            page.add_style_tag(content=(ROOT / "web/styles.css").read_text())
            page.add_script_tag(path=str(args.chart_js))
            page.add_script_tag(content=source)
            page.evaluate("""() => {
              const account = (id, presence) => ({account_id:id, account_label:id+'@example.test',
                account_state:'known', account_plan:presence==='remembered'?'self_serve_business_prolite':'pro', presence, machines:['fixture'],
                five_hour_used_pct:37, seven_day_used_pct:99, updated_at:'2026-10-01T12:34:00Z'});
              AgentMonitor.renderOverview({today:{cost_usd:1}, week:{cost_usd:2},
                range:{cost_usd:3, tokens:123}, rate_limits:{claude:{accounts:[account('current','in_use')]},
                  codex:{accounts:[account('long-history-account','remembered')]}},
                top_projects_week:[{project:'fixture/long-project-directory',cost_usd:29374.52},
                  {project:'small-project',cost_usd:12.34}], model_mix_month:[]}, '30d');
            }""")
            page.locator('.quota-past-toggle').click()
            page.locator('.quota-table-wrap').evaluate("el=>el.scrollIntoView({block:'start'})")
            for position in [0, 300, 400, 10000]:
                page.locator('.quota-table-wrap').evaluate('(el,x)=>{el.scrollLeft=x}', position)
                reading = page.evaluate("""() => {
                  const scroller=document.querySelector('.quota-table-wrap'), clip=scroller.getBoundingClientRect();
                  const visible = el => {
                    if (!el) return false;
                    const range=document.createRange(); range.selectNodeContents(el); const b=range.getBoundingClientRect();
                    return b.width>0 && b.left>=clip.left-1 && b.right<=clip.right+1;
                  };
                  const note=document.querySelector('.remembered .quota-account-observation');
                  return {scrollLeft:scroller.scrollLeft, historyTitle:visible(document.querySelector('.quota-past-toggle')),
                    historyNote:visible(note), note:note?.textContent || '',
                    batchBeforeFilters:Boolean(document.querySelector('#codex-account-actions').compareDocumentPosition(document.querySelector('#quota-explorer')) & Node.DOCUMENT_POSITION_FOLLOWING),
                    documentOverflow:document.documentElement.scrollWidth>innerWidth};
                }""")
                for role in ['history-context','quota-filter-adjacency']:
                    result=page.evaluate(bindings[role]['expression'])
                    reading[role]=result
                observations.append({'width':width,'position':position,**reading})
                page.screenshot(path=str(args.output / f'history-{width}-{position}.png'))
            chart = page.evaluate("""() => {
              const chart=Chart.getChart('top-projects-chart');
              const read = () => {
                const labels=[], ctx=chart.ctx, original=ctx.fillText;
                ctx.fillText=function(text,x,y,...rest) {
                  if (String(text).startsWith('$') && this.textAlign==='left')
                    labels.push({text, right:x+this.measureText(text).width, y});
                  return original.call(this,text,x,y,...rest);
                };
                try {chart.stop(); chart.update('none'); chart.draw();} finally {ctx.fillText=original;}
                return {canvasWidth:chart.width, rightPadding:chart.options.layout.padding.right, labels};
              };
              const current=read(), padding=chart.options.layout.padding.right;
              chart.options.layout.padding.right=64;
              const oldPadding=read();
              chart.options.layout.padding.right=padding; chart.update('none');
              return {...current, oldPadding};
            }""")
            observations.append({'width':width,'chart':chart})
            page.locator('#top-projects-chart').scroll_into_view_if_needed()
            page.screenshot(path=str(args.output / f'chart-{width}.png'))
            page.locator('#quota-search').fill('no-match-fixture')
            empty=page.locator('#quota-match-count').inner_text()
            page.locator('#quota-clear').click()
            restored=page.locator('#quota-match-count').inner_text()
            observations.append({'width':width,'empty':empty,'restored':restored})
            page.close()
    finally:
        browser.close()
(args.output / 'observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2)+'\n')
failures=[]
for r in observations:
    if 'scrollLeft' in r:
        for key in ['historyTitle','historyNote','batchBeforeFilters']:
            if not r[key]: failures.append(f"{r['width']}px / scroll {r['scrollLeft']}: {key}")
        if r['documentOverflow']: failures.append(f"{r['width']}px: document overflow")
        for role in ['history-context','quota-filter-adjacency']:
            if not r[role]['pass']: failures.append(f"{r['width']}px: {role} binding")
    if 'chart' in r:
        if len(r['chart']['labels']) < 2 or any(label['right']>r['chart']['canvasWidth'] for label in r['chart']['labels']):
            failures.append(f"{r['width']}px: chart amount clipped or missing")
        control=r['chart']['oldPadding']
        if not any(label['right']>control['canvasWidth'] for label in control['labels']):
            failures.append(f"{r['width']}px: old-padding control did not detect clipping")
    if 'empty' in r and ('匹配 0 / 2' not in r['empty'] or '匹配 2 / 2' not in r['restored']):
        failures.append(f"{r['width']}px: filter/clear regression")
print(json.dumps({'failures':failures,'output':str(args.output)},ensure_ascii=False))
raise SystemExit(bool(failures))
