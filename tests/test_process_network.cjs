// Network entry is server-only; connector/process APIs retain Python coverage.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const createNetwork = require('../web/network.js');
const fs = require('node:fs');
async function fixture(cached, failed=false, deferred=false, reply=null) {
  let releaseCache;
  const waitCache = deferred ? new Promise(resolve=>{releaseCache=resolve;}) : Promise.resolve();
  const nodes = new Map(), calls = [], cleanups = [];
  const qs = key => {
    if (!nodes.has(key)) nodes.set(key, {listeners:{}, textContent:'', innerHTML:'', querySelector(){return null;}, addEventListener(k,fn){this.listeners[k]=fn;}});
    return nodes.get(key);
  };
  const data = {checked_host:'fixture-server', timestamp:'2026-10-31T00:00:00Z', verdict:'low', coverage:{missing:[]}, conclusions:[]};
  const view=createNetwork({platform:{window:{},document:{},fetch:async path=>{calls.push(path);await waitCache;return {ok:!failed,status:503,json:async()=>cached};}},
    qs,updateNavLinks(){},api:async(path,query)=>{calls.push({path,query});return reply ? reply() : data;},withRefresh:(_,fn)=>fn(),
    formatDate:String,escapeHtml:String,onPageCleanup:fn=>cleanups.push(fn)});
  const initialized=view.initNetwork();
  if (!deferred) await initialized;
  return {qs,calls,cleanups,initialized,releaseCache};
}
test('empty entry detects once and fresh cache entry stays local',async()=>{
  for(const available of [false,true]) {
    const f=await fixture({available,checked_host:'cached-host',timestamp:'2026-01-01',verdict:'low',coverage:{missing:[]}});
    assert.equal(f.calls.length,available?1:2);
    assert.equal(f.calls[0],'/api/network/cache');
    assert.match(f.qs('#network-verdict').textContent,/未发现已知高风险信号/);
    await f.qs('#refresh').listeners.click();
    assert.deepEqual(f.calls.at(-1),{path:'/api/network',query:{force:'1'}});
    assert.match(f.qs('#network-checked-host').textContent,/fixture-server/);
  }
});
test('cache read failure still obtains a new result',async()=>{
  const f=await fixture(null,true); assert.equal(f.calls.length,2);
  assert.match(f.qs('#network-verdict').textContent,/未发现已知高风险信号/);
});
test('navigation cleanup prevents stale rendering',async()=>{
  const f=await fixture({available:false}); f.cleanups.forEach(fn=>fn());
  const previous=f.qs('#network-verdict').textContent, count=f.calls.length;
  await f.qs('#refresh').listeners.click(); assert.equal(f.qs('#network-verdict').textContent,previous);
  assert.equal(f.calls.length,count);
});
test('page exposes only server results and explains automatic detection',()=>{
  const html=fs.readFileSync(require.resolve('../web/network.html'),'utf8');
  assert.doesNotMatch(html,/process-network|local-network|服务端系统网络诊断（独立于本机 Agent）/);
  assert.match(html,/id="refresh"/); assert.match(html,/id="network-conclusion"/);
  assert.match(html,/无结果或结果过期时自动请求公网诊断服务/);
});

test('expired result refreshes once, cooldown suppresses automatic retries',async()=>{
  for(const delay of [0,120]) {
    const f=await fixture({available:true,stale:true,verdict:'low',timestamp:'old',refresh:{retry_in_seconds:delay}});
    assert.equal(f.calls.length,delay?1:2);
    if(delay) assert.match(f.qs('#network-verdict').textContent,/历史结果/);
  }
});

test('HTTP and diagnostic errors both retain the old result',async()=>{
  for(const transport of [false,true]) {
    const f=await fixture({available:true,stale:true,verdict:'low',timestamp:'old',refresh:{retry_in_seconds:0}},false,false,
      async()=>{if(transport) throw new Error('fixture failure'); return {error:'fixture failure',verdict:'unknown'};});
    assert.match(f.qs('#network-verdict').textContent,/历史结果/);
    assert.match(f.qs('#network-updated').textContent,/old/);
    assert.match(f.qs('#network-error').innerHTML,/保留上次结果/);
    assert.match(f.qs('#network-error').innerHTML,/fixture failure/);
  }
});

test('repeated clicks share the in-flight automatic request',async()=>{
  let resolve;
  const reply=new Promise(r=>{resolve=r;});
  const f=await fixture({available:false},false,true,()=>reply);
  f.releaseCache();
  await new Promise(r=>setImmediate(r));
  const click=f.qs('#refresh').listeners.click();
  assert.equal(f.calls.length,2);
  assert.match(f.qs('#network-verdict').textContent,/正在检测/);
  resolve({verdict:'low',coverage:{missing:[]}});
  await Promise.all([click,f.initialized]);
});

test('late empty, old or failed cache cannot overwrite an explicit detection',async()=>{
  for(const [cached,failed] of [[{available:false},false],[{available:true,checked_host:'old-host',verdict:'unknown'},false],[null,true]]) {
    const f=await fixture(cached,failed,true);
    await f.qs('#refresh').listeners.click();
    const result=f.qs('#network-verdict').textContent;
    assert.match(result,/未发现已知高风险信号/);
    f.releaseCache(); await f.initialized;
    assert.equal(f.qs('#network-verdict').textContent,result);
    assert.match(f.qs('#network-checked-host').textContent,/fixture-server/);
    assert.doesNotMatch(f.qs('#network-error').innerHTML,/上次检测结果暂不可用/);
  }
});
