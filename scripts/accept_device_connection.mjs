#!/usr/bin/env node
// Explicit development acceptance; the product has no browser-harness dependency.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {parseArgs} from 'node:util';
const {values:args} = parseArgs({options:{
  'agent-browser':{type:'string'}, browser:{type:'string'}, python:{type:'string'},
  'disclosures-only':{type:'boolean'}, 'network-only':{type:'boolean'}, launchd:{type:'boolean'}, help:{type:'boolean'},
}});
if (args.help) {
  console.log('Usage: npm ci && node scripts/accept_device_connection.mjs --browser /absolute/path/to/Chrome [--python /path/to/python] [--launchd] [--disclosures-only] [--network-only]\nRuns six synthetic accounts using the pinned public agent-browser CLI and an isolated headless Chrome. --network-only checks local process networks instead of accounts. --agent-browser can select an alternate CLI executable. --launchd additionally creates and removes an isolated macOS test job. No provider account requests.');
  process.exit(0);
}
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const tool = args['agent-browser'] || path.join(repo,'node_modules/.bin/agent-browser');
assert.ok(path.isAbsolute(tool) && fs.existsSync(tool), 'Run npm ci first, or supply --agent-browser /absolute/path/to/agent-browser');
assert.ok(args.browser && path.isAbsolute(args.browser) && fs.existsSync(args.browser), 'Supply --browser /absolute/path/to/Chrome (see CONTRIBUTING.md)');
const python = args.python || path.join(repo,'.venv/bin/python');
const evidence = process.env.AGENT_MONITOR_TEST_ARTIFACTS || path.join(os.homedir(),'task-artifacts','agent-monitor','device-acceptance');
fs.mkdirSync(evidence,{recursive:true});
const root = fs.mkdtempSync(path.join(evidence,'monitor-accept-'));
// Unix-domain socket paths have a small OS limit; evidence stays in the durable root.
const sockets = fs.mkdtempSync(path.join(os.tmpdir(),'am-sock-'));
// A fresh HOME alone does not override CLI/provider/profile environment variables.
const env = {PATH:process.env.PATH || '/usr/bin:/bin', LANG:process.env.LANG || 'en_US.UTF-8',
  HOME:root, TMPDIR:root, XDG_CONFIG_HOME:path.join(root,'config'), XDG_CACHE_HOME:path.join(root,'cache'),
  CLAUDE_CONFIG_DIR:path.join(root,'.claude'), CODEX_HOME:path.join(root,'.codex'),
  AGENT_BROWSER_SOCKET_DIR:sockets, AGENT_BROWSER_IDLE_TIMEOUT_MS:'30000', AGENT_BROWSER_DEFAULT_TIMEOUT:'3000',
  AGENT_BROWSER_ARGS:'--mute-audio,--use-mock-keychain,--password-store=basic,--disable-background-networking,--disable-component-update,--disable-sync,--no-proxy-server'};
const config=path.join(root,'browser-config.json'), launcher=path.join(root,'chrome');
fs.writeFileSync(config,'{}');
// Preserve Chrome's sandbox even when the upstream CLI adds container defaults.
const quote=value=>"'"+value.replaceAll("'","'\\''")+"'";
fs.writeFileSync(launcher,`#!/bin/sh\nfor arg do\n  case "$arg" in --no-sandbox|--no-sandbox=*|--disable-setuid-sandbox|--disable-setuid-sandbox=*) echo 'Refusing sandbox-disabled browser acceptance' >&2; exit 1;; esac\ndone\nexec ${quote(args.browser)} "$@"\n`,{mode:0o700});
const execute = promisify(execFile), children = [];
const sleep = ms=>new Promise(r=>setTimeout(r,ms));
const budget = new AbortController();
let fixture, url, lifecycle, browserAttempted=false, daemonPid;
const pidFile=path.join(env.AGENT_BROWSER_SOCKET_DIR,'accept.pid');
const interrupt = signal => {
  failure ||= new Error('Acceptance interrupted by '+signal);
  budget.abort(failure);
  if (lifecycle) lifecycle.kill('SIGTERM');
};
const onInterrupt=()=>interrupt('SIGINT'), onTerminate=()=>interrupt('SIGTERM');
process.on('SIGINT', onInterrupt);
process.on('SIGTERM', onTerminate);
async function until(name, predicate, budget=10000) {
  const end=Date.now()+budget;
  do { if (await predicate()) return; await sleep(100); } while(Date.now()<end);
  throw new Error(name+' did not complete within the test observation budget');
}
async function browserCommand(argv, cleanup=false, pin=true) {
  if (!cleanup) budget.signal.throwIfAborted();
  let result;
  const started=Date.now();
  const command=argv[0]+(argv[1] && argv[0]!=='eval' ? " "+argv[1] : "");
  fs.appendFileSync(path.join(root,'commands.jsonl'),JSON.stringify({event:"start",command,at:new Date().toISOString()})+"\n");
  try {
    result=await new Promise((resolve,reject) => {
      // npm's launcher spawns a native child. Killing only the launcher leaves
      // that child holding the request socket and inherited output pipes.
      const child=spawn(tool,['--session','accept','--config',config,'--executable-path',launcher,pin?'--pin-tab':'--no-pin-tab','--json',...argv],
        {env,cwd:root,detached:true,stdio:['ignore','pipe','pipe']});
      let stdout='',stderr='',termination,killTimer;
      const signalGroup = signal => {
        if (!child.pid) return;
        try {process.kill(-child.pid,signal);} catch(error) {if(error.code!=='ESRCH') termination ||= error;}
      };
      const terminate = reason => {
        if (termination) return;
        termination=reason;
        signalGroup('SIGTERM');
        killTimer=setTimeout(()=>signalGroup('SIGKILL'),1000);
      };
      const abort=()=>terminate(budget.signal.reason || new Error('Acceptance cancelled'));
      const commandTimer=setTimeout(()=>terminate(new Error('Command exceeded its observation budget')),cleanup?10000:argv[0]==='click'?5000:35000);
      if (!cleanup) budget.signal.addEventListener('abort',abort,{once:true});
      const dispose=()=>{clearTimeout(commandTimer);clearTimeout(killTimer);budget.signal.removeEventListener('abort',abort);};
      const capture=key=>chunk=>{
        if(key==='stdout') stdout+=chunk; else stderr+=chunk;
        if(Buffer.byteLength(stdout)+Buffer.byteLength(stderr)>1024*1024) terminate(new Error('Command output exceeded its limit'));
      };
      child.stdout.on('data',capture('stdout'));child.stderr.on('data',capture('stderr'));
      child.once('error',error=>{dispose();reject(error);});
      child.once('close',(code,signal)=>{
        dispose();
        fs.appendFileSync(path.join(root,'commands.jsonl'),JSON.stringify({event:'process_closed',command,pid:child.pid,code,signal})+'\n');
        if(termination || code!==0) reject(Object.assign(termination || new Error(`Command exited ${code}`),{stdout,stderr,code,killed:!!termination}));
        else resolve({stdout,stderr});
      });
      if (!cleanup && budget.signal.aborted) abort();
    });
  } catch(error) {
    fs.appendFileSync(path.join(root,'commands.jsonl'),JSON.stringify({event:"error",command,ms:Date.now()-started,code:error.code,killed:error.killed})+"\n");
    let reason;
    try {reason=JSON.parse(error.stdout).error;} catch {}
    throw Object.assign(new Error('agent-browser '+argv[0]+': '+(reason || error.stderr?.trim() || error.message),{cause:error}),{code:error.code,killed:error.killed});
  }
  fs.appendFileSync(path.join(root,'commands.jsonl'),JSON.stringify({event:"end",command,ms:Date.now()-started})+"\n");
  const resultJSON=JSON.parse(result.stdout);
  assert.equal(resultJSON.success,true,resultJSON.error || 'agent-browser command failed');
  return resultJSON;
}
const bt=(...argv)=>browserCommand(argv);
async function read(expression) { return (await bt('eval',expression)).data.result; }
async function click(selector, expect) {
  // Reconcile the native session with a read before dispatching the next write.
  // A verified popup effect does not imply that its CLI daemon is already idle.
  const started=Date.now();
  assert.equal(await read(`(() => {const e=document.querySelector(${JSON.stringify(selector)});return !!e && !e.disabled && !e.hidden})()`),true,'control ready');
  console.log('Native readiness read: '+selector+' ('+(Date.now()-started)+' ms).');
  assert.notEqual(await read(expect),true,'click result must not already hold');
  await bt('scrollintoview',selector);
  let receiptError;
  try {await bt('click',selector);} catch(error) {receiptError=error;}
  // A popup can close before the CLI acknowledges the click. Never resend it:
  // the pinned original page and fixture counters establish the actual effect.
  await until('click result: '+selector,()=>read(expect));
  if(receiptError) console.log('Click receipt unavailable ('+(receiptError.killed?'timeout':receiptError.code || 'error')+'); verified its result on the pinned page without repeating the click.');
}
async function stats() { return (await fetch(url+'/fixture/stats',{signal:AbortSignal.timeout(3000)})).json(); }
async function configure(value) { const r=await fetch(url+'/fixture/config',{method:'POST',body:JSON.stringify(value),signal:AbortSignal.timeout(3000)}); assert.equal(r.status,200); }
async function startFixture() {
  fixture=spawn(python,[path.join(repo,'tests/fixtures/account_egress_ui.py'),'--loopback'],{env,cwd:repo,stdio:['ignore','pipe','pipe']});
  children.push(fixture);
  let line='',error='';
  fixture.on('error',e=>{failure=e;});
  fixture.stdout.on('data',b=>line+=b);
  fixture.stderr.on('data',b=>error+=b);
  await until('fixture startup',()=>{if(failure)throw failure;if(fixture.exitCode!==null)throw new Error('Fixture failed: '+error);return line.includes('\n');});
  url=JSON.parse(line.split('\n')[0]).url;
  assert.equal(new URL(url).hostname,'127.0.0.1');
}
async function stopChildren() {
  if(url && fixture?.exitCode===null) {
    try { await fetch(url+'/fixture/stop',{method:'POST',body:'{}',signal:AbortSignal.timeout(2000)}); } catch {}
  }
  for(const p of children) {
    if(p.exitCode===null && p.signalCode===null) p.kill('SIGTERM');
    await until('owned child exit',()=>p.exitCode!==null || p.signalCode!==null,2000).catch(async()=>{
      p.kill('SIGKILL'); await until('owned child kill',()=>p.exitCode!==null || p.signalCode!==null,2000);
    });
  }
  if(daemonPid) {
    await until('owned browser daemon exit',()=>{
      try{process.kill(daemonPid,0);return false;}catch(e){if(e.code==='ESRCH')return true;throw e;}
    },10000);
  }
}
// Seven network popup interactions can each spend 5s in click plus up to
// 35s reconciling the native CLI response (observed: about 30s per popup).
// Keep 20s for ordinary local steps; product 18/26s deadlines are unchanged.
const acceptanceBudgetMs=args['network-only'] ? 7 * (5000 + 35000) + 20000 : 180000;
const timer=setTimeout(()=>{
  budget.abort(new Error(`Browser acceptance exceeded its ${acceptanceBudgetMs/1000} second test budget`));
  failure ||= budget.signal.reason;
},acceptanceBudgetMs);
let failure;
try {
  console.log(args['network-only'] ? 'Checking two synthetic process networks in an isolated browser; provider calls are disabled by the fixture.' : 'Checking six synthetic accounts in an isolated browser; provider calls are disabled by the fixture.');
  await startFixture();
  assert.equal((await fetch(url,{signal:AbortSignal.timeout(3000)})).status,200,'fixture page reachable');
  browserAttempted=true;
  await bt('open',url);
  daemonPid=Number(fs.readFileSync(pidFile,'utf8'));
  assert.ok(Number.isInteger(daemonPid) && daemonPid>1,'owned daemon identity');
  // In agent-browser 0.39.4 a local launch does not apply the initial pin to
  // BrowserManager. Toggle it before any popup exists to bind the original tab.
  await browserCommand(['get','url'],false,false);
  assert.equal((await bt('get','url')).data.url,new URL(url).href,'pinned fixture origin');
  console.log('Isolated browser opened the local fixture.');
  if (args['network-only']) {
    await bt('open',url+'/network');
    await bt('snapshot','-i');
    assert.equal((await stats()).server_reads,0,'local page never automatically probes Hub');
    await click('#process-network-refresh',"document.querySelector('#process-network-status').textContent.includes('fixture-browser-device')");
    const resultText = "document.querySelector('#process-network-result').innerText";
    const selection = "document.querySelector('#process-network-select').value";
    assert.equal(await read(selection),'','first report requires an explicit choice');
    assert.match(await read(resultText),/选择进程/);
    await bt('fill','#process-network-search','fixture-claude');
    assert.equal(await read("document.querySelector('#process-network-select').options.length"),2,'search leaves one matching process and the placeholder');
    await bt('select','#process-network-select','101:123');
    assert.match(await read(resultText),/fixture-claude/);
    assert.match(await read(resultText),/fixture-service.test/);
    assert.match(await read(resultText),/fixture-exit/);
    await bt('fill','#process-network-search','');
    assert.equal((await stats()).process_reads,1,'one real connector popup collected one snapshot');
    assert.equal(await read("document.querySelector('#process-network-cancel').getClientRects().length"),0,'finished read hides cancellation action');
    await click('#process-network-probe',`${resultText}.includes('192.0.2.10')`);
    await click('#process-network-third-party',`${resultText}.includes('192.0.2.20')`);
    assert.deepEqual((await stats()).process_probe_kinds,['provider','third_party'],'origin acknowledgement redeems a ticket bound to each probe kind');
    assert.match(await read(resultText),/供应商固定目标/);
    assert.match(await read(resultText),/第三方出口观测/);
    assert.match(await read("document.querySelector('#process-network-third-party-help').innerText"),/HTTP 非加密.*非商业/);
    await configure({process_mode:'reordered'});
    await click('#process-network-refresh',"document.querySelector('#process-network-status').textContent.includes('00:00:02Z')");
    assert.equal(await read(selection),'101:123','refresh retains full identity even when order changes');
    assert.match(await read(resultText),/历史回执/);
    assert.match(await read(resultText),/192.0.2.10/);
    assert.match(await read(resultText),/192.0.2.20/);
    await configure({process_mode:'blocked'});
    await click('#process-network-refresh',"document.querySelector('#process-network-status').textContent.includes('00:00:03Z') && document.querySelector('#process-network-probe').disabled");
    assert.match(await read("document.querySelector('#process-network-probe-help').innerText"),/合成绕过规则尚未核实.*核对原有策略/);
    assert.equal(await read("document.querySelector('#process-network-third-party').disabled"),false,'provider refusal does not disable third-party evidence');
    assert.equal((await stats()).process_probes,2,'blocked admission does not issue another probe');
    await bt('screenshot',path.join(root,'process-network.png'));
    await click('#process-network-help > summary',"document.querySelector('#process-network-help').open");
    assert.match(await read("document.querySelector('#process-network-command').innerText"),/agent-monitor connect --server/);
    await bt('set','viewport','390','844');
    await bt('screenshot',path.join(root,'process-network-390.png'));
    assert.equal(await read('document.documentElement.scrollWidth<=innerWidth'),true,'local report fits narrow viewport');
    await click('#server-network > summary',"document.querySelector('#server-network').open");
    assert.equal((await stats()).server_reads,0,'opening details does not issue a server diagnostic');
    await click('#refresh',"document.querySelector('#network-checked-host').textContent.includes('fixture-server-device')");
    assert.equal((await stats()).server_reads,1,'explicit detection issues one server request');
    assert.match(await read("document.querySelector('#process-network-status').textContent"),/fixture-browser-device/);
    const retained = await read(resultText);
    await configure({pair_mode:'silent'});
    await click('#process-network-refresh',"document.querySelector('#process-network-cancel').hidden===false");
    assert.equal(await read(resultText),retained,'pending refresh retains old evidence');
    await click('#process-network-cancel',"document.querySelector('#process-network-status').textContent.includes('已取消')");
    assert.equal(await read(resultText),retained,'cancelled refresh retains old evidence');
    assert.equal(await read(selection),'101:123');
    assert.match(await read("document.querySelector('#process-network-status').textContent"),/保留.*00:00:03Z/);
    assert.equal((await stats()).server_reads,1,'cancelled local report does not fall back to server');
    await configure({pair_mode:'ready',process_mode:'removed'});
    await click('#process-network-refresh',"document.querySelector('#process-network-status').textContent.includes('原选进程实例已不在')");
    assert.equal(await read(selection),'','disappeared process is not silently replaced');
    fs.writeFileSync(path.join(root,'process-network-evidence.json'),JSON.stringify(await stats(),null,2));
    console.log('PASS: real loopback popup, explicit/searchable selection, reordered refresh, two probe kinds, blocked provider, retained cancellation evidence, disappearance, explicit server action and narrow layout.');
  } else {
  const primary='#codex-batch-next', dialog='dialog.account-egress-dialog';
  const ready=`!document.hidden && !!document.querySelector('${primary}') && !document.querySelector('${primary}').disabled && document.querySelector('${primary}').textContent.includes('6')`;
  const preview=`!!document.querySelector('${dialog} .account-egress-address')`;
  const closed=`!document.querySelector('${dialog}')`;
  await until('account list',()=>read(ready));
  assert.match(await read(`document.querySelector('${primary}').textContent`),/6/);
  // Exercise the complete disclosure unit before any synthetic send action.
  await configure({disclosure:true});
  await bt('open',url);
  await until('disclosure scenario',()=>read("document.querySelector('#codex-batch-status').textContent.includes('未发送 2')"));
  await bt('set','viewport','1440','1000');
  const card='#codex-account-list > :first-child', detail=card+' details';
  const firstSummary=await read(`document.querySelector('${detail} > summary').getBoundingClientRect().toJSON()`);
  const firstActions=await read("(() => {const r=document.querySelector('#codex-account-list > :first-child .codex-account-footer').getBoundingClientRect();return {...r.toJSON(),y:r.y+scrollY}})()");
  await click(detail+' > summary',`document.querySelector('${detail}').open`);
  await bt('screenshot',path.join(root,'disclosure-account-expanded.png'));
  const expandedSummary=await read(`document.querySelector('${detail} > summary').getBoundingClientRect().toJSON()`);
  assert(Math.abs(firstSummary.x-expandedSummary.x)<2,'account disclosure trigger retains its horizontal position');
  const expandedActions=await read("(() => {const r=document.querySelector('#codex-account-list > :first-child .codex-account-footer').getBoundingClientRect();return {...r.toJSON(),y:r.y+scrollY}})()");
  fs.writeFileSync(path.join(root,'disclosure-geometry.json'),JSON.stringify({firstSummary,expandedSummary,firstActions,expandedActions},null,2));
  assert(Math.abs(firstActions.x-expandedActions.x)<2 && Math.abs(firstActions.y-expandedActions.y)<2,'adjacent account actions retain their position in document coordinates');
  assert.equal(await read(`(() => {const d=document.querySelector('${detail}');const body=d.querySelector('.disclosure-body');return !!body && body.checkVisibility() && body.getBoundingClientRect().width > 300})()`),true,'account detail has a readable owned body');
  const detailText=await read(`document.querySelector('${detail} .disclosure-body').innerText`);
  for (const field of ['workspace-0','已保存','发送成功','历史批次消息','历史连接失败','请描述今天的天气','turn/start','turn/completed','fixture-thread-0','fixture-turn-0','CLI 已确认','21%','清除本页登录态']) assert(detailText.includes(field),'retained detail field: '+field);
  fs.writeFileSync(path.join(root,'account-detail-text.txt'),detailText);
  const grouped=await read(`Object.fromEntries([...document.querySelectorAll('${detail} .account-detail-section')].map(s=>[s.querySelector('h4').innerText,s.innerText]))`);
  for (const [group,field] of [['本次操作','请描述今天的天气'],['配额观测','21%'],['历史记录','历史批次消息'],['技术细节','fixture-thread-0'],['账号管理','workspace-0']]) assert(grouped[group]?.includes(field),'field belongs to '+group);
  assert(!grouped['本次操作'].includes('历史批次消息'),'history is separate from current operation');
  fs.writeFileSync(path.join(root,'account-detail-groups.json'),JSON.stringify(grouped,null,2));
  await bt('scrollintoview',detail+' .disclosure-body');
  await bt('screenshot',path.join(root,'account-detail-groups-1440.png'));
  await bt('scrollintoview',detail+' .disclosure-body button');
  await bt('screenshot',path.join(root,'account-detail-end-1440.png'));
  const pausedCard='#codex-account-list > :nth-child(2)';
  assert.equal(await read(`document.querySelector('${pausedCard}').innerText.split('服务端明确限制了额度使用').length-1`),1,'one current quota warning per account');
  assert.match(await read("document.querySelector('#codex-batch-blocked').innerText"),/2.*额度.*仅刷新配额/);
  assert.equal(await read("document.querySelector('#codex-batch-retry').disabled"),false,'guidance does not change retry authorization');
  await click('#codex-device-help > summary',"document.querySelector('#codex-device-help').open");
  assert.equal(await read("(() => {const d=document.querySelector('#codex-device-help');return d.querySelector('button').getBoundingClientRect().top>d.querySelector('summary').getBoundingClientRect().bottom})()"),true,'first help action is separated from its summary');
  await click('#codex-sending-help > summary',"document.querySelector('#codex-batch-help').closest('details').open");
  assert.equal(await read("(() => {const d=document.querySelector('#codex-batch-help').closest('details');const b=d.querySelector('.disclosure-body');return b.checkVisibility() && b.getBoundingClientRect().top > d.querySelector('summary').getBoundingClientRect().bottom})()"),true,'sending rules have body spacing');
  assert.doesNotMatch(await read("document.querySelector('#codex-batch-help').innerText"),/本轮开始于|重置时间已知|需要授权/);
  await bt('screenshot',path.join(root,'disclosure-help-expanded.png'));
  await click(detail+' > summary',`!document.querySelector('${detail}').open`);
  assert.equal(await read(`document.querySelector('${detail} .disclosure-body').checkVisibility()`),false,'collapsed detail body is not painted');
  await bt('set','viewport','390','844');
  await click(pausedCard+' details > summary',`document.querySelector('${pausedCard} details').open`);
  assert.equal(await read(`(() => {const e=document.querySelector('${pausedCard} .disclosure-body'); const r=e.getBoundingClientRect(); return e.checkVisibility() && r.width>0 && r.right<=innerWidth})()`),true,'narrow paused detail remains within viewport');
  await bt('screenshot',path.join(root,'disclosure-paused-390.png'));
  await bt('scrollintoview',pausedCard+' .disclosure-body button');
  await bt('screenshot',path.join(root,'disclosure-paused-end-390.png'));
  assert.deepEqual(await read(`Array.from(document.querySelectorAll('${pausedCard} .account-detail-section > h4'), e=>e.innerText)`),['本次操作','配额观测','历史记录','技术细节','账号管理'],'account details have task-oriented reading groups');
  assert.match(await read(`document.querySelector('${pausedCard} .account-detail-section:nth-child(4)').innerText`),/quota_exhausted.*HTTP 429/,'current failure category and status remain in technical details');
  assert.equal(await read(`(() => {const sections=[...document.querySelectorAll('${pausedCard} .account-detail-section')];return sections.every(s=>s.checkVisibility() && s.getBoundingClientRect().right<=innerWidth)})()`),true,'groups remain readable without extra disclosure steps');
  await bt('set','viewport','1440','1000');
  await configure({disclosure:false});
  await bt('open',url);
  await until('ordinary account list',()=>read(ready));
  await click('#codex-account-list > :first-child details > summary',"document.querySelector('#codex-account-list > :first-child details').open");
  assert.deepEqual(await read("Array.from(document.querySelectorAll('#codex-account-list > :first-child .account-detail-section > h4'),e=>e.innerText)"),['账号管理'],'empty operation, quota, history and technical groups are omitted');
  await click('#codex-account-list > :first-child details > summary',"!document.querySelector('#codex-account-list > :first-child details').open");
  console.log('PASS: account/help disclosures keep their body, action position, warning and retry guidance.');

  if (!args['disclosures-only']) {
  await click(primary,preview);
  let counts=await stats();
  assert.deepEqual([counts.pair_requests,counts.status_requests,counts.previews,counts.starts.length],[1,1,1,0]);
  assert.equal(await read(`getComputedStyle(document.querySelector('${dialog} .account-egress-address.is-china')).color`),'rgb(196, 43, 43)');
  assert.equal(await read(`getComputedStyle([...document.querySelectorAll('${dialog} p')].find(e=>e.textContent.includes('出口地区为中国'))).color`),'rgb(196, 43, 43)');
  await click(`${dialog} button:not(.btn-primary)`,closed);
  assert.equal((await stats()).starts.length,0);
  console.log('PASS: primary action connects and previews; China warning is red; cancel sends zero batches.');
  await bt('open',url);
  await until('refreshed account list',()=>read(ready));
  await click(primary,preview);
  assert.equal((await stats()).starts.length,0);
  await click(`${dialog} .btn-primary`,closed);
  await until('confirmed batch receipt',async()=>(await stats()).starts.length===1);
  counts=await stats();
  assert.deepEqual([counts.pair_requests,counts.status_requests,counts.previews,counts.starts.length],[2,2,2,1]);
  assert.equal(counts.starts[0].profile_ids.length,6);
  console.log('PASS: refresh reconnects; explicit confirmation produces exactly one six-account batch.');
  await configure({pair_mode:'error'});
  await click(primary,"document.querySelector('#codex-device-status').textContent.includes('未能完成配对')");
  assert.deepEqual([(await stats()).previews,(await stats()).starts.length],[2,1]);
  await configure({pair_mode:'silent'});
  await click(primary,"!document.querySelector('#codex-device-cancel').hidden");
  await click('#codex-device-cancel',"document.querySelector('#codex-device-cancel').hidden");
  assert.deepEqual([(await stats()).previews,(await stats()).starts.length],[2,1]);
  console.log('PASS: pairing failure and cancellation do not preview or send another batch.');
  const panel='#codex-recovery-body .quota-recovery-panel';
  const probe=panel+' [data-recovery-action="probe"]', refresh=panel+' [data-recovery-action="read"]';
  const paused=`document.querySelector('${primary}').disabled && document.querySelector('${panel}')?.checkVisibility()`;
  const focused=`!!document.querySelector('${panel}') && document.querySelector('${panel}').textContent.includes('执行机器：fixture-hub')`;
  await configure({paused:true});
  await bt('open',url);
  await until('pause explanation',()=>read(paused));
  await until('visible recovery card',()=>read(focused));
  assert.equal((await stats()).recoveries.length,0,'reading status makes no recovery request');
  assert.equal((await stats()).starts.length,1,'reading status never sends a batch');
  assert.match(await read(`document.querySelector('${panel}').textContent`),/默认 Codex 目录/);
  for(const pause_record of ['stale','incomplete','duplicate']) {
    await configure({pause_record});
    await bt('open',url);
    await until('pause',()=>read(paused));
    await until('visible recovery card',()=>read(focused));
    assert.equal(await read(`document.querySelector('${probe}').disabled`),false,'fresh source status replaces stale overview');
  }
  await configure({pause_record:'matched',probe_used:true});
  await bt('open',url);
  await until('pause',()=>read(paused));
  await until('visible recovery card',()=>read(focused));
  assert.equal(await read(`document.querySelector('${probe}').disabled`),true);
  assert.match(await read(`document.querySelector('${panel}').innerText`),/检查机会已使用/);
  for(const [recovery_state, explanation] of [['active','已有操作正在进行'],['identity','查询账号身份不完整'],['unreadable','暂停记录无法读取'],['unavailable','暂时无法确认恢复状态']]) {
    await configure({recovery_state,probe_used:false});
    await bt('open',url);
    await until('pause',()=>read(paused));
    await until(explanation,()=>read(`document.querySelector('${panel}')?.innerText.includes('${explanation}')`));
    assert.equal(await read(`document.querySelector('${probe}').disabled`),true,explanation);
  }
  await configure({recovery_state:'ready'});
  await bt('open',url);
  await until('pause',()=>read(paused));
  await until('visible recovery card',()=>read(focused));
  assert.match(await read(`document.querySelector('${panel}').innerText`),/发送已暂停，需要先检查配额[\s\S]*检查配额并尝试恢复/);
  assert.equal(await read(`document.querySelector('${panel} .recovery-technical').open`),false);
  assert.doesNotMatch(await read(`document.querySelector('${panel}').innerText`),/workspace-0|凭据位置|RPC/);
  await bt('screenshot',path.join(root,'recovery-paused.png'));
  await bt('click',panel+' .recovery-technical > summary');
  // HTTP deployments may not expose the Clipboard API; the manual path must work.
  await bt('eval',"Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})");
  const readCount=(await stats()).recovery_reads;
  await click(panel+' [data-recovery-action="copy"]',`!document.querySelector('${panel} textarea').hidden`);
  const copied=await read(`document.querySelector('${panel} textarea').value`);
  assert.match(copied,/fixture-hub/); assert.match(copied,/不会发送或重发消息/);
  assert.equal((await stats()).recovery_reads,readCount,'copy uses displayed diagnostics only');
  assert.equal((await stats()).recoveries.length,0,'disabled reasons and copy do not probe');
  assert.equal(await read(`document.querySelector('${panel} textarea').clientWidth > 400`),true,'manual copy has readable width');
  await bt('screenshot',path.join(root,'recovery-guidance.png'));
  console.log('PASS: local status stays readable with stale/incomplete/ambiguous snapshots; used probe stays disabled; no provider request.');
  // Browser dialogs remain native: verify the exact target before accepting.
  async function confirmControl(selector, accept=true) {
    await bt('scrollintoview',selector);
    await bt('click',selector);
    const status=await bt('dialog','status');
    assert.ok(JSON.stringify(status).includes('fixture'),'confirmation names the synthetic target');
    await bt('dialog',accept?'accept':'dismiss');
  }
  await configure({probe_used:false,restricted:true});
  await bt('open',url);
  await until('restricted account',()=>read("[...document.querySelectorAll('#codex-account-list button')].some(b=>b.textContent==='核对后解除此账号限制')"));
  // Resume's confirmation describes scope, rather than echoing account identity.
  const resume='#codex-account-list > :first-child .codex-account-footer > .codex-account-controls > button:last-child';
  assert.equal(await read(`document.querySelector('${resume}').textContent`),'核对后解除此账号限制');
  await bt('scrollintoview',resume); await bt('click',resume);
  assert.ok(JSON.stringify(await bt('dialog','status')).includes('暂停仍保留'));
  await bt('dialog','accept');
  await until('account restriction cleared',async()=>(await stats()).resumes.length===1);
  assert.equal('pause_id' in (await stats()).resumes[0],false);
  assert.equal((await stats()).paused,true);
  for(const recovery_mode of ['failure','unknown','success']) {
    await configure({recovery_mode});
    await bt('open',url);
    await until('recoverable pause',()=>read(paused));
    await until('visible recovery card',()=>read(focused));
    await click('#codex-account-list input[type="checkbox"]', "document.querySelector('#codex-selection-count').textContent.includes('已选 5 / 6')");
    const before=(await stats()).recoveries.length;
    const pauseId=(await stats()).pause_id;
    await confirmControl(probe,false);
    assert.equal((await stats()).recoveries.length,before);
    await confirmControl(probe);
    await until('recovery receipt',async()=>(await stats()).recoveries.length===before+1);
    assert.deepEqual((await stats()).recoveries.at(-1),{machine:'fixture-hub',account_id:'workspace-0',account_label:'fixture0@example.test',pause_id:pauseId});
    await until('selection preserved after recovery',()=>read("document.querySelector('#codex-selection-count').textContent.includes('已选 5 / 6')"));
    if(recovery_mode==='success') {
      await until('unpaused readback',()=>read(`!document.hidden && !document.querySelector('${primary}').disabled && document.querySelector('${primary}').textContent.includes('所选 5')`));
      assert.equal(await read(`document.querySelector('${panel}').checkVisibility()`),true,'success stays visible after pause banner hides');
      assert.match(await read(`document.querySelector('${panel}').innerText`),/当前没有通道暂停[\s\S]*已有结果和选择保留/);
      await bt('click',panel+' [data-recovery-action="continue"]');
      await until('focus returns to send',()=>read(`document.activeElement === document.querySelector('${primary}')`));
      await bt('open',url);
      await until('unpaused page reload',()=>read(ready));
      await click('#codex-recovery-history > summary',focused);
      await bt('click',panel+' .recovery-technical > summary');
      assert.match(await read(`document.querySelector('${panel}').innerText`),/本次配额读取成功/);
      assert.equal(await read(`(() => {const outer=document.querySelector('${panel} .recovery-technical-body');const inner=outer.querySelector('.recovery-diagnostic .disclosure-body');const r=inner.getBoundingClientRect(),o=outer.getBoundingClientRect();return inner.checkVisibility() && r.left>o.left && r.bottom<o.bottom})()`),true,'nested diagnostic has a visible inset and an end before parent actions');
      await click(panel+' .recovery-diagnostic > summary',`!document.querySelector('${panel} .recovery-diagnostic').open`);
      assert.equal(await read(`document.querySelector('${panel} .recovery-diagnostic .disclosure-body').checkVisibility()`),false,'nested diagnostic collapses its painted content');
      await click(panel+' .recovery-diagnostic > summary',`document.querySelector('${panel} .recovery-diagnostic').open`);
      await bt('screenshot',path.join(root,'recovery-success.png'));
    }
    else {
      await until('new pause readback',()=>read(`document.querySelector('${panel}').textContent.includes('pause-after-${before+1}')`));
      assert.equal(await read(paused),true);
      await until('failed check guidance',()=>read(`document.querySelector('${panel} h3').innerText==='配额检查未成功，发送仍暂停'`));
      await click(panel+' [data-recovery-action="help"]',`!document.querySelector('${panel} .recovery-steps').hidden`);
      assert.match(await read(`document.querySelector('${panel} .recovery-steps').innerText`),/fixture-hub[\s\S]*保持原来的出口/);
      if(recovery_mode==='failure') await bt('screenshot',path.join(root,'recovery-failed.png'));
      assert.match(await read(`document.querySelector('${panel}').textContent`),/-32603/,'automatic readback retains stored diagnostics even for a JSON unknown receipt');
      await bt('open',url);
      await until('pause after reload',()=>read(paused));
      await until('visible recovery card',()=>read(focused));
      await bt('eval',`document.querySelector('${panel} details').open=true`);
      const text=await read(`document.querySelector('${panel}').innerText`);
      assert.match(text,/-32603/); assert.match(text,/上游 HTTP：未取得/);
      assert.match(text,/公网出口 IP／地区：未核实/); assert.match(text,/127.0.0.1:59527/);
      const reads=(await stats()).recovery_reads;
      await bt('click',refresh);
      await until('explicit state refresh',async()=>(await stats()).recovery_reads>reads);
      assert((await stats()).recovery_reads>reads);
      assert.equal((await stats()).recoveries.length,before+1,'readback never retries');
    }
    assert.equal((await stats()).starts.length,1,'recovery never automatically sends');
  }
  console.log('PASS: account resume omits shared pause ID; cancel queries zero times; failed/unknown probes retain pause; success enables explicit sending without replay.');
  await configure({paused:true});
  await bt('open',url);
  await until('quota diagnostics entry',()=>read("!!document.querySelector('#quota-accounts [data-codex-pause-id]')"));
  await bt('click','#quota-accounts .note-disclosure > summary');
  const quotaPanel='#quota-accounts [data-codex-pause-id]:nth-child(3) .quota-recovery-panel';
  const beforeQuota=(await stats()).recoveries.length;
  const sourceRecord='#quota-accounts [data-codex-pause-id]:nth-child(3) .recovery-source';
  assert.equal(await read(`document.querySelector('${sourceRecord}').open`),false,'raw collection error is collapsed');
  await click(sourceRecord+' > summary',`document.querySelector('${sourceRecord}').open`);
  assert(await read(`document.querySelector('${sourceRecord} p').innerText.length>0`),'raw collection record remains readable');
  await click(sourceRecord+' > summary',`!document.querySelector('${sourceRecord}').open`);
  const hiddenRecoveryControlsHaveNoLayout = expression => read(`(() => {
    const card=${expression};
    return !!card && [...card.querySelectorAll('[hidden]')].every(e => getComputedStyle(e).display==='none' && e.getClientRects().length===0);
  })()`);
  await click('#quota-accounts [data-codex-pause-id]:nth-child(3) > button',`document.querySelector('${quotaPanel}')?.innerText.includes('检查配额并尝试恢复')`);
  assert.match(await read(`document.querySelector('${quotaPanel}').innerText`),/发送已暂停[\s\S]*不会发送消息/);
  assert.equal((await stats()).recoveries.length,beforeQuota,'quota consumer reads without probing');
  assert.equal(await hiddenRecoveryControlsHaveNoLayout(`document.querySelector('${quotaPanel}')`),true,'paused quota card hides unavailable actions in actual layout');
  assert.equal((await stats()).starts.length,1);
  await click(quotaPanel+' .recovery-technical > summary',`document.querySelector('${quotaPanel} .recovery-technical').open`);
  assert.equal(await read(`(() => {const d=document.querySelector('${quotaPanel} .recovery-technical');const b=d.querySelector('.disclosure-body');return b.checkVisibility() && b.firstElementChild.getBoundingClientRect().left>d.getBoundingClientRect().left && b.getBoundingClientRect().top>d.querySelector('summary').getBoundingClientRect().bottom})()`),true,'shared quota consumer has a visible owned technical body');
  await bt('scrollintoview',quotaPanel+' .recovery-technical');
  await bt('screenshot',path.join(root,'quota-recovery-guidance.png'));
  console.log('PASS: quota diagnostics also expose the shared guidance without a recovery request or send.');
  const beforeRedraw=(await stats()).recovery_reads;
  await bt('eval',`window.acceptRecoveryCard=document.querySelector('${quotaPanel}'); window.AgentMonitor.renderQuotaAccounts(window.AgentMonitor.quotaSnapshot());`);
  assert.equal(await read(`window.acceptRecoveryCard===document.querySelector('${quotaPanel}') && window.acceptRecoveryCard.closest('details.note-disclosure').open`),true,'snapshot redraw preserves the open live card');
  assert.equal((await stats()).recovery_reads,beforeRedraw,'identical snapshot redraw does not issue another status request');
  // Another tab's successful recovery must reconcile the already-open account card.
  await configure({paused:false});
  await bt('eval',"document.dispatchEvent(new Event('agent-monitor:recovery-state'))");
  await until('external recovery updates existing card',()=>read(`!document.querySelector('${primary}').disabled && document.querySelector('${panel} h3').innerText==='当前没有通道暂停'`));
  assert.equal((await stats()).recoveries.length,beforeQuota);
  assert.equal((await stats()).starts.length,1);
  console.log('PASS: another consumer can remove the pause without leaving the account card stale.');
  await bt('eval',"fetch('/fixture/quota').then(r=>r.json()).then(q=>window.AgentMonitor.renderQuotaAccounts(q))");
  await until('recovery history survives snapshot clearing',()=>read("window.acceptRecoveryCard.isConnected && window.acceptRecoveryCard.closest('details.note-disclosure').open && window.acceptRecoveryCard.querySelector('h3').innerText==='当前没有通道暂停'"));
  assert.equal((await stats()).recoveries.length,beforeQuota);

  assert.equal(await hiddenRecoveryControlsHaveNoLayout('window.acceptRecoveryCard'),true,'recovered quota card hides unavailable actions in actual layout');
  console.log('PASS: snapshots preserve open recovery history and never replay a provider check.');

  }

  }
} catch(error) {
  failure=error;
  if (browserAttempted && fs.existsSync(pidFile)) {
    try {
      const observed=await read(`({url:location.href,hidden:document.hidden,pause:document.querySelector('[data-codex-pause-id="fixture-pause"]')?.textContent,dialog:document.querySelector('dialog')?.textContent,buttons:[...document.querySelectorAll('dialog button')].map(e=>({text:e.textContent,disabled:e.disabled,rect:e.getBoundingClientRect().toJSON()}))})`);
      console.error('Fixture failure evidence: '+JSON.stringify({counts:((s)=>({pairs:s.pair_requests,status:s.status_requests,previews:s.previews,starts:s.starts.length,recoveries:s.recoveries.length,resumes:s.resumes.length}))(await stats()),page:observed}));
    } catch { console.error('Fixture page failure evidence unavailable; original error retained.'); }
  }
}
finally {
  clearTimeout(timer);
  try {
    if(browserAttempted) {
      if(!daemonPid && fs.existsSync(pidFile)) daemonPid=Number(fs.readFileSync(pidFile,'utf8'));
      await browserCommand(['close'],true);
    }
  } catch(error) {
    failure ||= error;console.error('Test Chrome cleanup failed: '+error.message);
    if(Number.isInteger(daemonPid) && daemonPid>1) {
      try {process.kill(daemonPid,'SIGTERM');} catch(e) {if(e.code!=='ESRCH')console.error('Daemon termination failed: '+e.message);}
    }
  }
  // A browser cleanup error must not skip the fixture and native daemon cleanup.
  try { await stopChildren();fs.rmSync(sockets,{recursive:true,force:true});console.log('Isolated evidence retained at '+root); }
  catch(error) { failure ||= error;console.error('Cleanup incomplete; isolated evidence retained at '+root); }
}
if(failure) { console.error('FAIL: '+failure.message);process.exitCode=1; }
else if(args.launchd) {
  try {
    const pending=execute(python,[path.join(repo,'scripts/accept_connector_lifecycle.py')],{cwd:repo,timeout:360000});
    lifecycle=pending.child;
    const result=await pending;
    process.stdout.write(result.stdout);
  } catch(error) { console.error(error.stdout || '');console.error(error.stderr || 'Lifecycle acceptance failed');process.exitCode=1; }
} else console.log('PASS: browser acceptance complete and owned processes closed. LaunchAgent lifecycle not requested (--launchd).');

process.removeListener('SIGINT', onInterrupt);
process.removeListener('SIGTERM', onTerminate);
