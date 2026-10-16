import subprocess
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class MetricWebTests(unittest.TestCase):
    def test_cost_coverage(self):
        script = r'''
const fs = require("fs"), assert = require("assert");
global.document = {readyState: "loading", addEventListener() {}};
global.window = {};
eval(fs.readFileSync("web/app.js", "utf8"));
global.AgentMonitor = window.AgentMonitor;
eval(fs.readFileSync("web/pivot.js", "utf8"));
const data = {dimensions: ["day", "agent"], rows: [
  {key: ["d1", "codex"], value: 0, cost_coverage: {known: 1, unknown: 0}},
  {key: ["d2", "codex"], value: null, cost_coverage: {known: 0, unknown: 2}},
  {key: ["d1", "claude"], value: 3, cost_coverage: {known: 2, unknown: 1}},
]};
const chart = window.AgentMonitorPivot.chartData(data, "cost");
assert.deepStrictEqual(chart.rows[0].cost_coverage[JSON.stringify(["codex"])], {known: 1, unknown: 0});
assert.equal(chart.rows[1].values[JSON.stringify(["codex"])], null);
const rank = window.AgentMonitorPivot.rankingData(data);
const codex = rank.rows.find(row => row.key[0] === "codex");
assert.deepStrictEqual(codex.cost_coverage, {known: 1, unknown: 2});
assert.equal(codex.value, 0);
assert.equal(codex.periods[1].value, null);
assert.deepStrictEqual(codex.periods[1].cost_coverage, {known: 0, unknown: 2});
assert.match(AgentMonitor.costReading(null, {known: 0, unknown: 2}), /无已知金额/);
assert.match(AgentMonitor.costReading(0, {known: 1, unknown: 0}), /0.00/);
assert.match(AgentMonitor.costReading(3, {known: 2, unknown: 1}), /已知小计/);
assert.match(AgentMonitor.costReading(3), /定价覆盖未知/);
assert.match(AgentMonitor.costReading(null, {known: 0, unknown: 0}), /无记录/);
delete data.rows[1].cost_coverage;
assert.equal(window.AgentMonitorPivot.rankingData(data).rows.find(row => row.key[0] === "codex").cost_coverage, null);
'''
        result=subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_network_missing_data_is_not_health(self):
        script = r'''
const fs=require("fs"), assert=require("assert");
const nodes=Object.fromEntries(["#network-local","#network-risk","#network-banner","#network-verdict","#network-updated"].map(key=>[key,{}]));
global.document={readyState:"loading",addEventListener(){},querySelector(key){return nodes[key];}};
global.window={};
eval(fs.readFileSync("web/app.js","utf8").replace("window.AgentMonitor = {", "window.AgentMonitor = {renderNetworkBanner,renderLocalNetwork,renderRiskNetwork,"));
const ui=window.AgentMonitor;
ui.renderLocalNetwork({local:{ipv6:null,dns:[{ip:"192.0.2.1",country:null}],dns_has_cn:false}});
assert(!nodes["#network-local"].innerHTML.includes("已关闭"));
assert(!nodes["#network-local"].innerHTML.includes("无国内 DNS"));
assert(nodes["#network-local"].innerHTML.includes("未知"));
ui.renderLocalNetwork({local:{ipv6:"2001:db8::1",ipv6_leaked:true,dns:[{ip:"192.0.2.1",country:"US"}],dns_has_cn:false}});
assert(nodes["#network-local"].innerHTML.includes("公网泄漏未核实"));
ui.renderRiskNetwork({public:{},risk:null});
assert(!nodes["#network-risk"].innerHTML.includes(">否<"));
ui.renderRiskNetwork({public:{proxy:false,hosting:false},risk:null});
assert(nodes["#network-risk"].innerHTML.includes(">否<"));
ui.renderNetworkBanner({verdict:"low",coverage:{missing:["risk"],limitations:["ipv6_leak_unverified"]}});
assert(nodes["#network-verdict"].textContent.includes("部分"));
assert(!nodes["#network-verdict"].textContent.includes("风险低"));
ui.renderNetworkBanner({verdict:"high",coverage:{missing:["risk"]}});
assert.equal(nodes["#network-verdict"].textContent, "本地规则提示异常");
ui.renderNetworkBanner({verdict:"high",coverage:{missing:["risk"]},conclusions:[{level:"bad",text:"时区不一致"},{level:"warn",text:"DNS 未知"}]});
assert.equal(nodes["#network-verdict"].textContent, "本地规则检出 1 项异常");
assert(nodes["#network-updated"].textContent.includes("缺测：风险分"));
assert(nodes["#network-updated"].textContent.includes("IPv6 公网泄漏未核实"));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_batch_reset_uses_latest_whole_observation(self):
        script = r'''
const fs=require("fs"),assert=require("assert");
global.document={readyState:"loading",addEventListener(){}};global.window={};
eval(fs.readFileSync("web/app.js","utf8"));
const source=fs.readFileSync("web/codex-accounts.js","utf8");
const renderSource=source.slice(source.indexOf("  function renderBatch()"),source.indexOf("  batchStart.addEventListener"));
const batchStart={},batchNext={},batchRetry={},batchStatus={},batchHelp={};
const unavailable=[],submittingBatch=false,timestamp=x=>x;
const before={observed_at:"2026-10-14T00:00:00Z",seven_day_resets_at:Date.now()/1000+3600};
const op={message_status:"succeeded",before};
const batch={id:"b",created_at:"2026-10-13T00:00:00Z",items:[{profile_id:"p",operation:op}]};
const accounts=[{id:"p"}];
eval(renderSource);
renderBatch(); assert(batchStatus.textContent.includes("已知 1/1"));
op.after={observed_at:"2026-10-14T01:00:00Z",seven_day_resets_at:null};
renderBatch(); assert(batchStatus.textContent.includes("已知 0/1"));
accounts[0].operation={refresh_only:true,started_at:"2026-10-14T02:00:00Z",before:{...before,observed_at:"2026-10-14T02:00:00Z"}};
renderBatch(); assert(batchStatus.textContent.includes("已知 1/1"));
accounts[0].operation.started_at="2026-10-12T00:00:00Z";
renderBatch(); assert(batchStatus.textContent.includes("已知 0/1"));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_codex_five_hour_observation_and_absence(self):
        script = r'''
const fs=require("fs"),assert=require("assert");
const node=()=>({children:[],textContent:"",classList:{add(){}},style:{setProperty(){}},setAttribute(){},appendChild(child){this.children.push(child);}});
global.document={readyState:"loading",addEventListener(){},createElement:node};global.window={};
eval(fs.readFileSync("web/app.js","utf8").replace("window.AgentMonitor = {", "window.AgentMonitor = {quotaWindowCell,QUOTA_PROVIDERS,QUOTA_WINDOW_5H,"));
const ui=window.AgentMonitor,provider=ui.QUOTA_PROVIDERS.find(p=>p.key==="codex");
const text=n=>n.textContent+n.children.map(text).join(" ");
const known=text(ui.quotaWindowCell(provider,ui.QUOTA_WINDOW_5H,{five_hour_used_pct:73,five_hour_resets_at:Date.now()/1000+3600}));
assert(known.includes("73%"));assert(!known.includes("不适用"));
const absent=text(ui.quotaWindowCell(provider,ui.QUOTA_WINDOW_5H,{}));
assert(absent.includes("—"));assert(absent.includes("重置时间未知"));assert(!absent.includes("没有"));
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
