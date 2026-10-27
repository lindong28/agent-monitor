import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {test} from 'node:test';

for(const scenario of ['startup-and-cleanup-failure','json-error','sandbox-refusal']) test(scenario,async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'accept-cleanup-'));
  const marker=path.join(root,'fixture.json');
  const python=path.join(root,'fixture.cjs');
  fs.writeFileSync(python,`#!${process.execPath}
    const http=require('http'),fs=require('fs');
    fs.writeFileSync(${JSON.stringify(marker)},JSON.stringify({pid:process.pid,home:process.env.HOME}));
    const server=http.createServer((req,res)=>res.end('{}'));
    server.listen(0,'127.0.0.1',()=>console.log(JSON.stringify({url:'http://127.0.0.1:'+server.address().port})));`,{mode:0o700});
  const tool=path.join(root,'browser.cjs'), browser=path.join(root,'chrome.cjs');
  const browserMarker=path.join(root,'browser-called');
  fs.writeFileSync(browser,`#!${process.execPath}\nrequire('fs').writeFileSync(${JSON.stringify(browserMarker)},'called');`,{mode:0o700});
  fs.writeFileSync(tool,`#!${process.execPath}
    const assert=require('node:assert/strict');
    for(const key of ['AGENT_BROWSER_CDP','AGENT_BROWSER_PROVIDER','AGENT_BROWSER_PROFILE','NODE_OPTIONS','AI_GATEWAY_API_KEY']) assert.equal(process.env[key],undefined,key+' must not be inherited');
    assert.equal(process.env.CLAUDE_CONFIG_DIR,process.env.HOME+'/.claude');
    assert.equal(process.env.CODEX_HOME,process.env.HOME+'/.codex');
    if(process.argv.at(-1)==='close') {console.error('injected cleanup failure');process.exit(1);}
    if(${JSON.stringify(scenario)}==='json-error') {console.log(JSON.stringify({success:false,error:'injected startup failure from JSON'}));process.exit(1);}
    if(${JSON.stringify(scenario)}==='sandbox-refusal') {
      const launcher=process.argv[process.argv.indexOf('--executable-path')+1];
      const result=require('child_process').spawnSync(launcher,['--no-sandbox'],{encoding:'utf8'});
      process.stderr.write(result.stderr);process.exit(result.status || 0);
    }
    console.error('injected startup failure');process.exit(1);`,{mode:0o700});
  let observation;
  try {
    let result;
    try {await promisify(execFile)(process.execPath,[new URL('./accept_device_connection.mjs',import.meta.url).pathname,'--agent-browser',tool,'--browser',browser,'--python',python],
      {timeout:8000,env:{...process.env,AGENT_BROWSER_CDP:'http://127.0.0.1:1',AGENT_BROWSER_PROVIDER:'injected',AGENT_BROWSER_PROFILE:root,AI_GATEWAY_API_KEY:'synthetic'}});}
    catch(error){result=error;}
    observation=JSON.parse(fs.readFileSync(marker));
    assert.equal(result?.code,1,'must exit nonzero without hanging on the fixture');
    assert.equal(result.killed,false);
    assert.match(result.stderr,/injected cleanup failure/);
    assert.match(result.stderr,scenario==='sandbox-refusal'?/Refusing sandbox-disabled/:/injected startup failure/);
    assert.equal(fs.existsSync(browserMarker),false,'unsafe browser must not launch');
    assert.throws(()=>process.kill(observation.pid,0),{code:'ESRCH'});
    assert.equal(fs.existsSync(observation.home),false);
  } finally {
    if(!observation&&fs.existsSync(marker))observation=JSON.parse(fs.readFileSync(marker));
    if(observation){try{process.kill(observation.pid,'SIGKILL');}catch{} fs.rmSync(observation.home,{recursive:true,force:true});}
    fs.rmSync(root,{recursive:true,force:true});
  }
});
