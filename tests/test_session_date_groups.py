"""Date totals use complete filtered results; disclosures survive rerenders."""
import subprocess
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class SessionDateGroupsTests(unittest.TestCase):
    def test_date_totals_and_state(self):
        script = r'''
const fs=require('fs'), assert=require('assert');
function node() {return {children:[],dataset:{},attrs:{},listeners:{},_html:'',
set innerHTML(v){this._html=v;this.children=[];},get innerHTML(){return this._html;},
appendChild(v){this.children.push(v);return v;},setAttribute(k,v){this.attrs[k]=v;},
addEventListener(k,v){this.listeners[k]=v;},focus(){},querySelector(){return this.children[0]?.children[0];},querySelectorAll(){return this.children.filter(c=>c.className==='date-group');}};}
const ids=['#sessions-body','#sort','#session-count','#page-status','#page-status-top','#page-prev','#page-next','#page-prev-top','#page-next-top'];
const nodes=Object.fromEntries(ids.map(id=>[id,node()]));nodes['#sort'].value='time';
global.document={readyState:'loading',addEventListener(){},querySelector(s){return nodes[s]||null;},createElement(){return node();}};
global.window={location:{origin:'http://fixture.test',pathname:'/sessions',search:'?range=30d&page=9'},history:{replaceState(a,b,next){window.location.search=new URL(next,window.location.origin).search;}}};
require("./web/app.js")();
const ui=window.AgentMonitor, state=ui.sessionsView;
const row=(id,day,cost=1)=>({session_id:id,agent_id:'codex',machine:'fixture',project:'/fixture',started_at:'2026-10-01T01:00:00Z',ended_at:day+'T12:00:00Z',cost_usd:cost,tokens:10});
const rows=Array.from({length:105},(_,i)=>row(String(i),'2026-10-20'));
rows.push({...row('partial','2026-10-20',2),unpriced_records:1},row('unknown','2026-10-20',null),row('zero','2026-10-19',0),row('all-unknown','2026-10-18',null));
let groups=ui.sessionDateGroups(rows);
assert.equal(groups.length,3);assert.equal(groups[0].rows.length,107);assert.equal(groups[0].cost,107);assert.equal(groups[0].tokens,1070);assert.equal(groups[0].partial,true);
assert.equal(groups[1].cost,0);assert.equal(groups[1].partial,false);assert.equal(groups[2].cost,null);
assert.equal(ui.sessionDateGroups(rows.slice(0,2))[0].cost,2);
state.rows=rows;state.page=9;ui.renderSessions();
assert.equal(state.page,0);assert.equal(new URLSearchParams(window.location.search).get('page'),'0');
const heads=()=>nodes['#sessions-body'].children.filter(c=>c.className==='date-group'),visible=()=>nodes['#sessions-body'].children.filter(c=>c.className==='session-row');
assert.equal(heads().length,3);assert.equal(visible().length,100);
const more=nodes['#sessions-body'].children.find(c=>c.className==='session-day-more');more.children[0].children[0].listeners.click();assert.equal(visible().length,107);
assert(heads()[0].children[0].children[0].innerHTML.includes('部分未计价'));assert(heads()[2].children[0].children[0].innerHTML.includes('未知'));
heads()[0].children[0].children[0].listeners.click();assert.equal(visible().length,0);
ui.renderSessions();assert.equal(visible().length,0);
for(const sort of ['cost','tokens']) {nodes['#sort'].value=sort;state.rows=[...rows].reverse();ui.renderSessions();assert.equal(heads().length,0);assert.equal(visible().length,100);assert.equal(visible()[0].dataset.sessionId,'all-unknown');}
nodes['#sort'].value='time';state.rows=rows;ui.renderSessions();assert.equal(visible().length,0);
heads()[1].children[0].children[0].listeners.click();assert.equal(visible().length,1);assert(visible()[0].innerHTML.includes('sort=time'));assert(visible()[0].innerHTML.includes('session=zero'));
nodes['#sessions-body']=node();ui.renderSessions();assert.equal(visible().length,1);
state.filters.agent='codex';state.rows=rows.slice(0,2);ui.renderSessions();assert(heads()[0].children[0].children[0].innerHTML.includes('2 个会话'));assert.equal(nodes['#session-count'].textContent,'找到 2 个匹配会话');
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
