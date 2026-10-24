const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync('web/codex-accounts.js', 'utf8');
function fixture({blocked=false, statusError=false, holdStatus=false} = {}) {
  const nodes = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {textContent:'', disabled:false, hidden:false, listeners:{}, addEventListener(k,v){this.listeners[k]=v;}});
    return nodes.get(id);
  }
  const listeners = new Map(), timers = new Map(), intervals = new Map(), calls = [], popups = [];
  let timerId=0, resolveStatus;
  const controller = new AbortController();
  const context = {root:{querySelector:node}, controller, crypto:webcrypto, Uint8Array, location:{origin:'http://hub.test'}, current:()=>!controller.signal.aborted,
    submittingBatch:false, notice:node('notice'), render(){}, Error, DOMException,
    setTimeout(fn){timers.set(++timerId,fn);return timerId;}, clearTimeout(id){timers.delete(id);},
    setInterval(fn){intervals.set(++timerId,fn);return timerId;}, clearInterval(id){intervals.delete(id);},
    request:async(action,payload)=>{calls.push({action,payload});if(holdStatus)await new Promise(resolve=>resolveStatus=resolve);if(statusError)throw Error('fixture route unavailable');return {connected:true,label:'fixture-device'};},
    window:{addEventListener(k,v){listeners.set(k,v);},removeEventListener(k,v){if(listeners.get(k)===v)listeners.delete(k);},
      open(url){if(blocked)return null;const p={url,closed:false,close(){this.closed=true;}};popups.push(p);return p;}}
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  let deviceConnection ='), source.indexOf('  async function request('))+'\nthis.requireDevice=requireDevice;',context);
  function message(overrides={}) {
    const popup=popups.at(-1), nonce=new URL(popup.url).searchParams.get('nonce');
    listeners.get('message')?.({origin:'http://127.0.0.1:39002',source:popup,data:{type:'agent-monitor-network',nonce,connection:{id:'route',capability:'fixture'}},...overrides});
  }
  return {context,nodes,node,calls,popups,timers,intervals,listeners,controller,message,completeStatus:()=>resolveStatus?.()};
}
test('main action with no prior pairing opens synchronously and waits for validated device', async()=>{
  const f=fixture();const pending=f.context.requireDevice();pending.catch(()=>{});
  assert.equal(f.popups.length,1,'one click must open pairing without a separate connect step');
  assert.equal(f.calls.length,0);f.message();
  assert.equal((await pending).id,'route');assert.equal(f.calls[0].action,'network-status');
  assert.equal(f.popups[0].closed,true);assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);
});
test('exact origin, source, nonce and message type gate the status request', async()=>{
  const f=fixture(), pending=f.context.requireDevice();
  for (const bad of [{origin:'http://evil.test'},{source:{}},{data:{type:'agent-monitor-network',nonce:'wrong'}},{data:{type:'other'}}]) f.message(bad);
  assert.equal(f.calls.length,0);f.message();await pending;assert.equal(f.calls.length,1);
});
test('duplicate action shares one handshake; later action and refreshed page pair again', async()=>{
  const f=fixture(), first=f.context.requireDevice();
  assert.equal(f.context.requireDevice(),first);assert.equal(f.popups.length,1);
  f.message();await first;
  const second=f.context.requireDevice();assert.equal(f.popups.length,2);f.message();await second;
  const fresh=fixture(), third=fresh.context.requireDevice();assert.equal(fresh.popups.length,1);fresh.message();await third;
});
test('popup denial reports the actionable browser phase and performs no network request', async()=>{
  const f=fixture({blocked:true});await assert.rejects(f.context.requireDevice(),/弹窗/);
  assert.equal(f.calls.length,0);assert.equal(f.node('#codex-device-help').open,true);
});
test('timeout closes popup and rejects late responses, then a new action can recover', async()=>{
  const f=fixture(), first=f.context.requireDevice();const receive=f.listeners.get('message'),old=f.popups[0];
  [...f.timers.values()][0]();await assert.rejects(first,/未收到/);
  assert.equal(old.closed,true);assert.equal(f.listeners.size,0);assert.equal(f.intervals.size,0);
  receive({origin:'http://127.0.0.1:39002',source:old,data:{type:'agent-monitor-network',nonce:new URL(old.url).searchParams.get('nonce'),connection:{id:'late',capability:'late'}}});
  assert.equal(f.calls.length,0);
  const second=f.context.requireDevice();f.message();await second;assert.equal(f.calls.length,1);
});
test('closed popup and explicit cancellation release all resources without status or preview', async()=>{
  for (const cancel of ['closed','button']) {
    const f=fixture(), pending=f.context.requireDevice();
    if(cancel==='closed'){f.popups[0].closed=true;[...f.intervals.values()][0]();}
    else f.node('#codex-device-cancel').listeners.click();
    await assert.rejects(pending,/未继续/);
    assert.equal(f.calls.length,0);assert.equal(f.popups[0].closed,true);assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);assert.equal(f.intervals.size,0);
  }
});
test('navigation during pairing or delayed server validation never returns a usable connection', async()=>{
  for (const afterResponse of [false,true]) {
    const f=fixture({holdStatus:true}),pending=f.context.requireDevice();
    if(afterResponse)f.message();
    f.controller.abort();await assert.rejects(pending,{name:'AbortError'});
    f.completeStatus();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(f.popups[0].closed,true);assert.equal(f.timers.size,0);assert.equal(f.listeners.size,0);
    assert.equal(vm.runInContext('deviceConnection',f.context),null);
  }
});
test('status validation failure and timeout fail closed and expose the failed phase', async()=>{
  for(const timeout of [false,true]){
    const f=fixture({statusError:!timeout,holdStatus:timeout}),pending=f.context.requireDevice();f.message();
    if(timeout)[...f.timers.values()][0]();
    await assert.rejects(pending,/连接校验/);f.completeStatus();
    assert.equal(f.node('#codex-device-help').open,true);assert.equal(f.popups[0].closed,true);
  }
});
test('local connector error is shown in main page without checking accounts', async()=>{
  const f=fixture(),pending=f.context.requireDevice(),nonce=new URL(f.popups[0].url).searchParams.get('nonce');
  f.message({data:{type:'agent-monitor-network',nonce,error:'连接端尚未连接服务器'}});
  await assert.rejects(pending,/连接端尚未连接服务器/);assert.equal(f.calls.length,0);
});
test('batch action freezes selection, waits for pairing and preview, and submits once', async()=>{
  const f=fixture();
  Object.assign(f.context,{accounts:[{id:'a'},{id:'b'}],selectedIds:new Set(['a']),batch:{id:'old'},load:async()=>{},
    previewEgress:async(_request,targets,_signal,payload)=>{assert.deepEqual(Array.from(targets,a=>a.id),['a']);return 'receipt';}});
  vm.runInContext(source.slice(source.indexOf('  async function performBatch('),source.indexOf('  function renderBatch('))+'\nthis.performBatch=performBatch;',f.context);
  const pending=f.context.performBatch('batch-start');
  f.context.selectedIds=new Set(['b']);await f.context.performBatch('batch-start');
  assert.equal(f.popups.length,1);assert.equal(f.calls.length,0);
  f.message();await pending;
  assert.deepEqual(f.calls.map(c=>c.action),['network-status','batch-start']);
  assert.deepEqual(Array.from(f.calls[1].payload.profile_ids),['a']);assert.equal(f.calls[1].payload.confirmation,'receipt');
});
test('standalone preview and retry also pair on their own user gesture', async()=>{
  for(const action of ['preview','retry']) {
    const f=fixture();let previews=0;
    Object.assign(f.context,{accounts:[],selectedIds:new Set(),batch:{id:'original-batch'},load:async()=>{},previewEgress:async()=>{previews++;},egressPreview:f.node('preview')});
    if(action==='preview')vm.runInContext(source.slice(source.indexOf('  egressPreview?.addEventListener('),source.indexOf('  root.querySelector("#codex-select-all")')),f.context);
    else vm.runInContext(source.slice(source.indexOf('  async function performBatch('),source.indexOf('  function renderBatch('))+'\nthis.performBatch=performBatch;',f.context);
    const pending=action==='preview'?f.node('preview').listeners.click():f.context.performBatch('batch-retry');
    assert.equal(f.popups.length,1);assert.equal(f.calls.length,0);f.message();await pending;
    if(action==='preview'){assert.equal(previews,1);assert.equal(f.calls.length,1);}
    else {assert.equal(f.calls[1].action,'batch-retry');assert.equal(f.calls[1].payload.batch_id,'original-batch');}
  }
});
test('failed pairing and rejected exit confirmation never submit a batch', async()=>{
  for(const cancelledPreview of [false,true]) {
    const f=fixture({statusError:!cancelledPreview});let previews=0;
    Object.assign(f.context,{accounts:[{id:'a'}],selectedIds:new Set(['a']),batch:null,load:async()=>{},previewEgress:async()=>{previews++;return null;}});
    vm.runInContext(source.slice(source.indexOf('  async function performBatch('),source.indexOf('  function renderBatch('))+'\nthis.performBatch=performBatch;',f.context);
    const pending=f.context.performBatch('batch-start');f.message();await pending;
    assert.deepEqual(f.calls.map(c=>c.action),['network-status']);assert.equal(previews,cancelledPreview?1:0);
  }
});

test('full-page navigation closes pending popup and detaches both window listeners', async()=>{
  const f=fixture(),pending=f.context.requireDevice();f.listeners.get('pagehide')();
  await assert.rejects(pending,{name:'AbortError'});
  assert.equal(f.popups[0].closed,true);assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);
});
