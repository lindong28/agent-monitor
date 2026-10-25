import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {test} from 'node:test';

test('browser cleanup failure still stops the fixture and removes isolated HOME',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'accept-cleanup-'));
  const marker=path.join(root,'fixture.json');
  const python=path.join(root,'fixture.cjs');
  fs.writeFileSync(python,`#!${process.execPath}
    const http=require('http'),fs=require('fs');
    fs.writeFileSync(${JSON.stringify(marker)},JSON.stringify({pid:process.pid,home:process.env.HOME}));
    const server=http.createServer((req,res)=>res.end('{}'));
    server.listen(0,'127.0.0.1',()=>console.log(JSON.stringify({url:'http://127.0.0.1:'+server.address().port})));`,{mode:0o700});
  const tool=path.join(root,'browser-task.mjs');
  fs.writeFileSync(tool,'export const cdp=()=>{},inspect=()=>{};');
  fs.writeFileSync(path.join(root,'test-chrome.mjs'),`export function launchTestChrome(){return {ready:Promise.reject(new Error('injected startup failure')),stop:async()=>{throw new Error('injected cleanup failure')}}}`);
  let observation;
  try {
    let result;
    try {await promisify(execFile)(process.execPath,[new URL('./accept_device_connection.mjs',import.meta.url).pathname,'--browser-tool',tool,'--python',python],{timeout:8000});}
    catch(error){result=error;}
    observation=JSON.parse(fs.readFileSync(marker));
    assert.equal(result?.code,1,'must exit nonzero without hanging on the fixture');
    assert.equal(result.killed,false);
    assert.match(result.stderr,/injected cleanup failure/);
    assert.match(result.stderr,/injected startup failure/);
    assert.throws(()=>process.kill(observation.pid,0),{code:'ESRCH'});
    assert.equal(fs.existsSync(observation.home),false);
  } finally {
    if(!observation&&fs.existsSync(marker))observation=JSON.parse(fs.readFileSync(marker));
    if(observation){try{process.kill(observation.pid,'SIGKILL');}catch{} fs.rmSync(observation.home,{recursive:true,force:true});}
    fs.rmSync(root,{recursive:true,force:true});
  }
});
