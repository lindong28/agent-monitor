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
  launchd:{type:'boolean'}, help:{type:'boolean'},
}});
if (args.help) {
  console.log('Usage: npm ci && node scripts/accept_device_connection.mjs --browser /absolute/path/to/Chrome [--python /path/to/python] [--launchd]\nRuns six synthetic accounts using the pinned public agent-browser CLI and an isolated headless Chrome. --agent-browser can select an alternate CLI executable. --launchd additionally creates and removes an isolated macOS test job. No provider account requests.');
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
// A fresh HOME alone does not override CLI/provider/profile environment variables.
const env = {PATH:process.env.PATH || '/usr/bin:/bin', LANG:process.env.LANG || 'en_US.UTF-8',
  HOME:root, TMPDIR:root, XDG_CONFIG_HOME:path.join(root,'config'), XDG_CACHE_HOME:path.join(root,'cache'),
  CLAUDE_CONFIG_DIR:path.join(root,'.claude'), CODEX_HOME:path.join(root,'.codex'),
  AGENT_BROWSER_SOCKET_DIR:path.join(root,'sockets'), AGENT_BROWSER_IDLE_TIMEOUT_MS:'30000', AGENT_BROWSER_DEFAULT_TIMEOUT:'3000',
  AGENT_BROWSER_ARGS:'--mute-audio,--disable-background-networking,--disable-component-update,--disable-sync,--no-proxy-server'};
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
  let result;
  try {
    result=await execute(tool,['--session','accept','--config',config,'--executable-path',launcher,pin?'--pin-tab':'--no-pin-tab','--json',...argv],
      {env,cwd:root,timeout:cleanup?10000:argv[0]==='click'?5000:35000,signal:cleanup?undefined:budget.signal,maxBuffer:1024*1024});
  } catch(error) {
    let reason;
    try {reason=JSON.parse(error.stdout).error;} catch {}
    throw Object.assign(new Error('agent-browser '+argv[0]+': '+(reason || error.stderr?.trim() || error.message),{cause:error}),{code:error.code,killed:error.killed});
  }
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
const timer=setTimeout(()=>{
  budget.abort(new Error('Browser acceptance exceeded its 180 second test budget'));
  failure ||= budget.signal.reason;
},180000);
let failure;
try {
  console.log('Checking six synthetic accounts in an isolated browser; provider calls are disabled by the fixture.');
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
  const primary='#codex-batch-next', dialog='dialog.account-egress-dialog';
  const ready=`!document.hidden && !!document.querySelector('${primary}') && !document.querySelector('${primary}').disabled && document.querySelector('${primary}').textContent.includes('6')`;
  const preview=`!!document.querySelector('${dialog} .account-egress-address')`;
  const closed=`!document.querySelector('${dialog}')`;
  await until('account list',()=>read(ready));
  assert.match(await read(`document.querySelector('${primary}').textContent`),/6/);
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
  const recovery='#codex-pause-recovery', panel='#codex-recovery-body .quota-recovery-panel';
  const probe=panel+' > button:nth-of-type(2)', refresh=panel+' > button:first-of-type';
  const paused=`document.querySelector('${primary}').disabled && !document.querySelector('#codex-upstream-pause').hidden`;
  const focused=`!!document.querySelector('${panel}') && document.querySelector('${panel}').textContent.includes('执行机器：fixture-hub')`;
  await configure({paused:true});
  await bt('open',url);
  await until('pause explanation',()=>read(paused));
  await click(recovery,focused);
  assert.equal((await stats()).recoveries.length,0,'reading status makes no recovery request');
  assert.equal((await stats()).starts.length,1,'reading status never sends a batch');
  assert.match(await read(`document.querySelector('${panel}').textContent`),/默认 Codex 目录/);
  for(const pause_record of ['stale','incomplete','duplicate']) {
    await configure({pause_record});
    await bt('open',url);
    await until('pause',()=>read(paused));
    await click(recovery,focused);
    assert.equal(await read(`document.querySelector('${probe}').disabled`),false,'fresh source status replaces stale overview');
  }
  await configure({pause_record:'matched',probe_used:true});
  await bt('open',url);
  await until('pause',()=>read(paused));
  await click(recovery,focused);
  assert.equal(await read(`document.querySelector('${probe}').disabled`),true);
  assert.match(await read(`document.querySelector('${panel}').innerText`),/检查机会已使用/);
  for(const [recovery_state, explanation] of [['active','当前操作尚未结束'],['identity','查询账号身份不完整'],['unreadable','暂停记录无法读取'],['unavailable','当前状态未核实']]) {
    await configure({recovery_state,probe_used:false});
    await bt('open',url);
    await until('pause',()=>read(paused));
    await click(recovery,`document.querySelector('${panel}')?.innerText.includes('${explanation}')`);
    assert.equal(await read(`document.querySelector('${probe}').disabled`),true,explanation);
  }
  await configure({recovery_state:'ready'});
  await bt('open',url);
  await until('pause',()=>read(paused));
  await click(recovery,focused);
  assert.match(await read(`document.querySelector('${panel}').innerText`),/为什么不能发送[\s\S]*现在可以检查一次[\s\S]*检查后怎么继续/);
  // HTTP deployments may not expose the Clipboard API; the manual path must work.
  await bt('eval',"Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})");
  const readCount=(await stats()).recovery_reads;
  await click(panel+' > button:nth-of-type(3)',`!document.querySelector('${panel} textarea').hidden`);
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
    await click(recovery,focused);
    const before=(await stats()).recoveries.length;
    const pauseId=(await stats()).pause_id;
    await confirmControl(probe,false);
    assert.equal((await stats()).recoveries.length,before);
    await confirmControl(probe);
    await until('recovery receipt',async()=>(await stats()).recoveries.length===before+1);
    assert.deepEqual((await stats()).recoveries.at(-1),{machine:'fixture-hub',account_id:'workspace-0',account_label:'fixture0@example.test',pause_id:pauseId});
    if(recovery_mode==='success') {
      await until('unpaused readback',()=>read(ready));
      assert.equal(await read(`document.querySelector('${panel}').checkVisibility()`),true,'success stays visible after pause banner hides');
      assert.match(await read(`document.querySelector('${panel}').innerText`),/本机暂停已解除或当前不存在[\s\S]*此前发送结果保留/);
      await bt('open',url);
      await until('unpaused page reload',()=>read(ready));
      await click('#codex-recovery-history > summary',focused);
      await bt('click',panel+' details > summary');
      assert.match(await read(`document.querySelector('${panel}').innerText`),/本次配额读取成功/);
      await bt('screenshot',path.join(root,'recovery-success.png'));
    }
    else {
      await until('new pause readback',()=>read(`document.querySelector('${panel}').textContent.includes('pause-after-${before+1}')`));
      assert.equal(await read(paused),true);
      assert.match(await read(`document.querySelector('${panel}').textContent`),/-32603/,'automatic readback retains stored diagnostics even for a JSON unknown receipt');
      await bt('open',url);
      await until('pause after reload',()=>read(paused));
      await click(recovery,focused);
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
  await click('#quota-accounts [data-codex-pause-id]:nth-child(3) > button',`document.querySelector('${quotaPanel}')?.innerText.includes('现在可以检查一次')`);
  assert.match(await read(`document.querySelector('${quotaPanel}').innerText`),/为什么不能发送[\s\S]*检查后怎么继续/);
  assert.equal((await stats()).recoveries.length,beforeQuota,'quota consumer reads without probing');
  assert.equal((await stats()).starts.length,1);
  await bt('screenshot',path.join(root,'quota-recovery-guidance.png'));
  console.log('PASS: quota diagnostics also expose the shared guidance without a recovery request or send.');
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
  try { await stopChildren();console.log('Isolated evidence retained at '+root); }
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
