#!/usr/bin/env node
// Explicit development acceptance; the product has no browser-harness dependency.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {parseArgs} from 'node:util';
const {values:args} = parseArgs({options:{
  'browser-tool':{type:'string'}, browser:{type:'string'}, python:{type:'string'},
  launchd:{type:'boolean'}, help:{type:'boolean'},
}});
if (args.help) {
  console.log('Usage: node scripts/accept_device_connection.mjs --browser-tool /absolute/path/browser-task.mjs [--browser /path/to/Chrome] [--python /path/to/python] [--launchd]\nRuns six synthetic accounts in an isolated headless browser. --launchd additionally creates and removes an isolated macOS test job. No provider account requests.');
  process.exit(0);
}
assert.ok(args['browser-tool'] && path.isAbsolute(args['browser-tool']) && fs.existsSync(args['browser-tool']), 'Supply an installed browser-task.mjs with --browser-tool');
const tool = args['browser-tool'];
const {cdp, inspect} = await import(pathToFileURL(tool));
const {launchTestChrome} = await import(new URL('./test-chrome.mjs',pathToFileURL(tool)));
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const python = args.python || path.join(repo,'.venv/bin/python');
const root = fs.mkdtempSync(path.join(process.platform === 'darwin' ? '/tmp' : os.tmpdir(),'monitor-accept-'));
const env = {...process.env, HOME:root, XDG_CACHE_HOME:path.join(root,'cache'), AGENT_BROWSER_IDLE_TIMEOUT_MS:'1000'};
const execute = promisify(execFile), children = [], runtimeDirs = [];
const sleep = ms=>new Promise(r=>setTimeout(r,ms));
const budget = new AbortController();
let fixture, url, state, lifecycle, browser;
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
async function bt(...argv) {
  const result=await execute(process.execPath,[tool,...argv],{env,cwd:repo,timeout:35000,signal:budget.signal,maxBuffer:1024*1024});
  return JSON.parse(result.stdout);
}
async function read(expression) { return (await bt('run',state,'--json','--','eval',expression)).data.result; }
async function click(selector, expect) {
  // Reconcile the native session with a read before dispatching the next write.
  // A verified popup effect does not imply that its CLI daemon is already idle.
  const started=Date.now();
  assert.equal(await read(`(() => {const e=document.querySelector(${JSON.stringify(selector)});return !!e && !e.disabled && !e.hidden})()`),true,'control ready');
  console.log('Native readiness read: '+selector+' ('+(Date.now()-started)+' ms).');
  return bt('click',state,'--json','--timeout-ms','3000','--selector',selector,'--expect',expect);
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
  for(const dir of runtimeDirs) {
    const pidFile=path.join(dir,'task.pid');
    await until('owned browser daemon exit',()=>{
      if(!fs.existsSync(pidFile))return true;
      const pid=Number(fs.readFileSync(pidFile,'utf8'));
      try{process.kill(pid,0);return false;}catch(e){if(e.code==='ESRCH')return true;throw e;}
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
  browser=launchTestChrome({binary:args.browser,signal:budget.signal});
  const endpoint=await browser.ready;
  console.log('Test Chrome started: '+JSON.stringify(browser.diagnostics()));
  state=path.join(root,'browser.json');
  await bt('create',state,'--endpoint',endpoint,'--approval','isolated-project-acceptance',
    '--allow','open,eval,click,close','--json');
  runtimeDirs.push(JSON.parse(fs.readFileSync(state)).runtimeDir);
  // The real UI defers loading in hidden tabs; activate only this owned headless target.
  await cdp(await inspect(JSON.parse(fs.readFileSync(state))), 'Page.bringToFront');
  console.log('Browser target bound; opening the local fixture.');
  await bt('run',state,'--json','--','open',url);
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
  await bt('run',state,'--json','--','open',url);
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
  await bt('close',state,'--json');
} catch(error) {
  failure=error;
  if (state && fs.existsSync(state)) {
    try {
      const page=await inspect(JSON.parse(fs.readFileSync(state)));
      const observed=await cdp(page,'Runtime.evaluate',{expression:`JSON.stringify({hidden:document.hidden,dialog:document.querySelector('dialog')?.textContent,buttons:[...document.querySelectorAll('dialog button')].map(e=>({text:e.textContent,disabled:e.disabled,rect:e.getBoundingClientRect().toJSON()}))})`,returnByValue:true});
      console.error('Fixture failure evidence: '+JSON.stringify({counts:((s)=>({pairs:s.pair_requests,status:s.status_requests,previews:s.previews,starts:s.starts.length}))(await stats()),page:JSON.parse(observed.result.value)}));
    } catch { console.error('Fixture page failure evidence unavailable; original error retained.'); }
  }
}
finally {
  clearTimeout(timer);
  try {
    await browser?.stop();
    if(browser)console.log('Test Chrome stopped: '+JSON.stringify(browser.diagnostics()));
  } catch(error) {failure ||= error;console.error('Test Chrome cleanup failed: '+error.message);}
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
