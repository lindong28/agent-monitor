// Network entry is server-only; connector/process APIs retain Python coverage.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const createNetwork = require('../web/network.js');
const fs = require('node:fs');
async function fixture(cached, failed=false, deferred=false) {
  let releaseCache;
  const waitCache = deferred ? new Promise(resolve=>{releaseCache=resolve;}) : Promise.resolve();
  const nodes = new Map(), calls = [], cleanups = [];
  const qs = key => {
    if (!nodes.has(key)) nodes.set(key, {listeners:{}, textContent:'', innerHTML:'', querySelector(){return null;}, addEventListener(k,fn){this.listeners[k]=fn;}});
    return nodes.get(key);
  };
  const data = {checked_host:'fixture-server', timestamp:'2026-10-31T00:00:00Z', verdict:'low', coverage:{missing:[]}, conclusions:[]};
  const view=createNetwork({platform:{window:{},document:{},fetch:async path=>{calls.push(path);await waitCache;return {ok:!failed,status:503,json:async()=>cached};}},
    qs,updateNavLinks(){},api:async(path,query)=>{calls.push({path,query});return data;},withRefresh:(_,fn)=>fn(),
    formatDate:String,escapeHtml:String,onPageCleanup:fn=>cleanups.push(fn)});
  const initialized=view.initNetwork();
  if (!deferred) await initialized;
  return {qs,calls,cleanups,initialized,releaseCache};
}
test('entry with and without cache never probes or reads local processes',async()=>{
  for(const available of [false,true]) {
    const f=await fixture({available,checked_host:'cached-host',timestamp:'2026-01-01',verdict:'low',coverage:{missing:[]}});
    assert.deepEqual(f.calls,['/api/network/cache']);
    assert.match(f.qs('#network-verdict').textContent,available?/未发现已知高风险信号/:/尚未检测/);
    await f.qs('#refresh').listeners.click();
    assert.deepEqual(f.calls[1],{path:'/api/network',query:{force:'1'}});
    assert.match(f.qs('#network-checked-host').textContent,/fixture-server/);
  }
});
test('cache failure stays passive and shows actionable error',async()=>{
  const f=await fixture(null,true); assert.deepEqual(f.calls,['/api/network/cache']);
  assert.match(f.qs('#network-error').innerHTML,/上次检测结果暂不可用/);
});
test('navigation cleanup prevents stale rendering',async()=>{
  const f=await fixture({available:false}); f.cleanups.forEach(fn=>fn());
  await f.qs('#refresh').listeners.click(); assert.equal(f.qs('#network-verdict').textContent,'尚未检测');
});
test('page exposes only server results and explicit detection',()=>{
  const html=fs.readFileSync(require.resolve('../web/network.html'),'utf8');
  assert.doesNotMatch(html,/process-network|local-network|服务端系统网络诊断（独立于本机 Agent）/);
  assert.match(html,/id="refresh"/); assert.match(html,/id="network-conclusion"/);
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
