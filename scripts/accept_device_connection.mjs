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
const root = fs.mkdtempSync(path.join(process.platform === 'darwin' ? '/tmp' : os.tmpdir(),'monitor-accept-'));
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
} catch(error) {
  failure=error;
  if (browserAttempted && fs.existsSync(pidFile)) {
    try {
      const observed=await read(`({url:location.href,hidden:document.hidden,dialog:document.querySelector('dialog')?.textContent,buttons:[...document.querySelectorAll('dialog button')].map(e=>({text:e.textContent,disabled:e.disabled,rect:e.getBoundingClientRect().toJSON()}))})`);
      console.error('Fixture failure evidence: '+JSON.stringify({counts:((s)=>({pairs:s.pair_requests,status:s.status_requests,previews:s.previews,starts:s.starts.length}))(await stats()),page:observed}));
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
  try { await stopChildren();fs.rmSync(root,{recursive:true,force:true}); }
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
