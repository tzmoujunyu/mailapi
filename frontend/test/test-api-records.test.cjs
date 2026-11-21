const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const assets=path.resolve(__dirname,'../../mailapi_shared/static');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(t,handler){
  const dom=new JSDOM(fs.readFileSync(path.join(assets,'test-features.html'),'utf8'),{url:'http://localhost/test-features#view-api2api',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window,calls=[],el=id=>w.document.getElementById(id);
  w.fetch=async(url,options={})=>{calls.push([url,options]);const data=await handler(url,options);return data?.ok===false?data:{ok:true,json:async()=>data};};
  for(const dialog of w.document.querySelectorAll('dialog'))dialog.showModal=()=>dialog.setAttribute('open','');
  w.eval(fs.readFileSync(path.join(assets,'test-api-records.js'),'utf8')+'\nwindow.records=apiRecordsController;');
  const submit=id=>el(id).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  return {w,el,calls,submit};
}
const row=id=>({id,created_at:1600000000,type:2,token_name:'Laptop',group:'default',model_name:'<img src=x onerror=alert(1)>',prompt_tokens:3,completion_tokens:5,quota:10,use_time:2,is_stream:true,request_id:'request-'+id,billing:{frt:100,group_ratio:1}});
const page=(items,total=items.length)=>({items,total,groups:['default'],stats:{quota:1450,rpm:1,tpm:8},display:{unit:'currency',divisor:500000,symbol:'$'}});

test('usage pages beyond 100 records, applies server filters and opens sanitized details',async t=>{
  const {w,el,calls,submit}=setup(t,url=>{const params=new URL(url,'http://localhost').searchParams;return page(Array.from({length:params.get('page')==='2'?45:100},(_,i)=>row(params.get('page')==='2'?101+i:1+i)),145);});
  w.records.start(false);await w.records.activate('logs');
  assert.equal(el('api-usage-rows').children.length,100);
  assert.equal(el('api-usage-rows').querySelector('img'),null);
  el('api-log-pager').querySelector('[aria-label="下一页"]').click();await tick();
  assert.equal(el('api-usage-rows').children.length,45);
  assert.match(calls.at(-1)[0],/page=2/);
  el('api-usage-rows').querySelector('[data-log-detail]').click();
  assert.equal(el('api-log-detail-dialog').open,true);
  assert.match(el('api-log-detail').textContent,/request-101/);
  assert.equal(el('api-log-detail').querySelector('img'),null);
  el('api-log-search').value='gpt-4o-mini';el('api-log-status').value='5';submit('api-log-form');await tick();
  const params=new URL(calls.at(-1)[0],'http://localhost').searchParams;
  assert.equal(params.get('page'),'1');assert.equal(params.get('type'),'5');assert.equal(params.get('model_name'),'gpt-4o-mini');
  assert.equal(el('api-audit-tab').hidden,true);
  await w.records.activate('audit');assert.ok(calls.every(([url])=>!url.startsWith('/api/admin/')));
});

test('a late response cannot replace a newer log search; errors clear stale records',async t=>{
  let first,sequence=0;
  const {w,el,submit}=setup(t,()=>{sequence++;if(sequence===1)return new Promise(resolve=>{first=resolve;});if(sequence===3)return {ok:false,status:502,json:async()=>({detail:'Unavailable'})};return page([row(2)]);});
  w.records.start(false);const pending=w.records.activate('logs');submit('api-log-form');await tick();
  first(page([row(1)]));await pending;
  assert.match(el('api-usage-rows').innerHTML,/data-log-detail="2"/);
  submit('api-log-form');await tick();
  assert.ok(!el('api-usage-rows').querySelector('[data-log-detail]'));
  assert.equal(el('api-log-quota').textContent,'—');assert.match(el('api-log-feedback').textContent,/Unavailable/);
});

test('catalog copies only actual model IDs and admin audit is cleared on role downgrade',async t=>{
  let copied='';const {w,el,calls}=setup(t,url=>url==='/api/api2api/models'?{items:[{id:'gpt-4o-mini'},{id:'claude-test'}]}:{items:[{created_at:'2026-11-20T00:00:00Z',actor_name:'admin',action:'created',key_name:'Development',owner_name:'alice',outcome:'succeeded'}],total:1});
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
  w.records.start(false);await w.records.activate('models');
  assert.equal(calls[0][1].method,'POST');assert.equal(el('api-model-grid').children.length,2);
  el('api-model-search').value='gpt';el('api-model-search').dispatchEvent(new w.Event('input'));
  el('api-model-grid').querySelector('button').click();await tick();assert.equal(copied,'gpt-4o-mini');
  w.records.start(true);await w.records.activate('audit');assert.match(el('api-audit-rows').textContent,/alice/);
  w.records.start(false);assert.equal(el('api-audit-rows').textContent,'');assert.equal(el('api-audit-tab').hidden,true);
});

test('full export uses the submitted query and never downloads failed responses',async t=>{
  const {w,el,submit,calls}=setup(t,url=>url.includes('/export?')?{ok:false,status:409,json:async()=>({detail:'Retry export'})}:page([row(1)]));
  w.records.start(false);await w.records.activate('logs');el('api-log-search').value='submitted';submit('api-log-form');await tick();el('api-log-search').value='unsent';
  el('view-api2api').querySelector('[data-log-export="csv"]').click();await tick();
  const exported=calls.find(([url])=>url.includes('/export?'))[0];
  assert.match(exported,/model_name=submitted/);assert.ok(!exported.includes('unsent'));assert.match(el('api-log-feedback').textContent,/Retry export/);
  assert.equal(w.document.querySelector('a[download]'),null);
});
