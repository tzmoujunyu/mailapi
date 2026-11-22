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
  for(const dialog of w.document.querySelectorAll('dialog')){
    dialog.showModal=()=>dialog.setAttribute('open','');
    dialog.close=()=>{dialog.removeAttribute('open');dialog.dispatchEvent(new w.Event('close'));};
  }
  w.eval(fs.readFileSync(path.join(assets,'test-api-models.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'test-api-records.js'),'utf8')+'\nwindow.records=apiRecordsController;');
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
  el('api-model-grid').querySelector('[data-catalog-copy]').click();await tick();assert.equal(copied,'gpt-4o-mini');
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

test('date shortcuts query immediately; custom seconds are exact and reversed dates keep the last results',async t=>{
  const {w,el,calls}=setup(t,()=>page([row(1)]));
  w.records.start(false);await w.records.activate('logs');
  el('view-api2api').querySelector('[data-log-range="all"]').click();await tick();
  let params=new URL(calls.at(-1)[0],'http://localhost').searchParams;
  assert.equal(params.get('start_timestamp'),'0');assert.equal(params.get('end_timestamp'),'0');
  el('api-log-start').value='2026-11-01T12:00:03';el('api-log-end').value='2026-11-02T13:01:07';
  el('api-log-apply-dates').click();await tick();params=new URL(calls.at(-1)[0],'http://localhost').searchParams;
  assert.equal(Number(params.get('end_timestamp')),new Date('2026-11-02T13:01:07').getTime()/1000);
  el('api-log-end').value='2026-11-02T13:01:00';el('api-log-apply-dates').click();await tick();
  assert.equal(Number(new URL(calls.at(-1)[0],'http://localhost').searchParams.get('end_timestamp')),new Date('2026-11-02T13:01:00').getTime()/1000);
  const before=calls.length;el('api-log-start').value='2026-12-01T00:00:01';el('api-log-apply-dates').click();await tick();
  assert.equal(calls.length,before);assert.equal(el('api-log-dates').open,true);assert.match(el('api-log-date-error').textContent,/开始时间/);
  assert.ok(el('api-usage-rows').querySelector('[data-log-detail="1"]'));
});

test('mobile filters preserve drafts on close, apply on search and reset the same controls',async t=>{
  const {w,el,calls}=setup(t,()=>page([row(1)]));w.records.start(false);await w.records.activate('logs');
  const input=el('api-log-search'),before=calls.length;
  el('api-log-open-filters').click();assert.equal(el('api-log-filter-dialog').open,true);
  assert.ok(el('api-log-filter-holder').contains(input));assert.equal(el('api-log-extra').hidden,false);
  input.value='draft-model';input.dispatchEvent(new w.Event('input'));el('api-log-filter-dialog').close();
  assert.ok(el('api-log-filter-home').contains(input));assert.equal(input.value,'draft-model');assert.equal(calls.length,before);
  assert.match(el('api-log-query-note').textContent,/筛选已更改/);
  el('api-log-open-filters').click();el('api-log-mobile-search').click();await tick();
  assert.match(calls.at(-1)[0],/model_name=draft-model/);assert.equal(el('api-log-filter-dialog').open,false);
  assert.equal(el('api-log-filter-count').textContent,'1');assert.equal(el('api-log-query-note').textContent,'');
  el('api-log-open-filters').click();el('api-log-mobile-reset').click();await tick();
  assert.equal(input.value,'');assert.equal(el('api-log-filter-count').hidden,true);
  assert.equal(w.document.querySelectorAll('#api-log-search').length,1);
});

test('mobile cards and desktop rows share column visibility and show accurate stream timing and cache data',async t=>{
  const stream={...row(1),billing:{frt:150,cache_tokens:12,cache_creation_tokens:4}},nonstream={...row(2),is_stream:false,use_time:0};
  const {w,el}=setup(t,()=>page([stream,nonstream]));w.records.start(false);await w.records.activate('logs');
  assert.equal(el('api-log-cards').querySelectorAll('[role="listitem"]').length,2);
  const cards=el('api-log-cards').querySelectorAll('article');assert.match(cards[0].textContent,/150ms/);assert.match(cards[0].textContent,/缓存读取.*12/);
  assert.match(cards[1].textContent,/<1s/);assert.ok(!cards[1].textContent.includes('t/s'));assert.ok(!cards[1].textContent.includes('首字'));
  const toggle=el('api-log-column-options').querySelector('[data-log-column="tokens"]');toggle.checked=false;toggle.dispatchEvent(new w.Event('change',{bubbles:true}));
  for(const cell of w.document.querySelectorAll('[data-log-col="tokens"]'))assert.equal(cell.hidden,true);
});

test('structured details copy identifiers, protect sensitive labels and retain request billing values',async t=>{
  const {w,el}=setup(t,()=>page([{...row(1),upstream_request_id:'upstream-1',billing:{frt:100,cache_tokens:0,group_ratio:1,cache_ratio:0.5}}]));let copied='';
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
  w.records.start(false);await w.records.activate('logs');el('api-usage-rows').querySelector('[data-log-detail]').click();
  const detail=el('api-log-detail');assert.equal(detail.querySelectorAll('section').length,3);assert.match(detail.textContent,/缓存倍率.*0.5×/);
  detail.querySelector('[data-log-copy-field="request_id"]').click();await tick();assert.equal(copied,'request-1');assert.match(el('api-log-copy-feedback').textContent,/已复制/);
  el('api-log-detail-dialog').close();el('api-log-privacy').click();
  assert.ok(!el('api-usage-rows').textContent.includes('Laptop'));assert.ok(!el('api-log-cards').textContent.includes('default'));
  el('api-usage-rows').querySelector('[data-log-detail]').click();
  assert.ok(!detail.textContent.includes('request-1'));assert.equal(detail.querySelector('[data-log-copy-field]'),null);
  assert.equal(el('api-log-privacy').getAttribute('aria-pressed'),'true');
  el('api-log-detail-dialog').close();el('api-log-privacy').click();el('api-usage-rows').querySelector('[data-log-detail]').click();
  assert.match(detail.textContent,/upstream-1/);assert.equal(detail.querySelector('img'),null);
  w.navigator.clipboard.writeText=async()=>{throw new Error('Denied');};detail.querySelector('[data-log-copy-field="request_id"]').click();await tick();
  assert.match(el('api-log-copy-feedback').textContent,/手动复制/);
});

test('page size resets pagination and a shrinking result set returns to its last available page',async t=>{
  let shrunk=false;
  const {w,el,calls}=setup(t,url=>{const p=new URL(url,'http://localhost').searchParams;return page(shrunk&&p.get('page')==='2'?[]:[row(1)],shrunk?1:145);});
  w.records.start(false);await w.records.activate('logs');shrunk=true;el('api-log-pager').querySelector('[aria-label="下一页"]').click();await tick();await tick();
  assert.match(calls.at(-1)[0],/page=1/);assert.match(el('api-log-pager').textContent,/1 \/ 1/);
  const size=el('api-log-pager').querySelector('select');size.value='20';size.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();
  const params=new URL(calls.at(-1)[0],'http://localhost').searchParams;assert.equal(params.get('page_size'),'20');assert.equal(params.get('page'),'1');
});

test('leaving and returning to API cancels pending work even if the old response ignores abort',async t=>{
  let resolveOld,sequence=0;
  const {w,el}=setup(t,()=>++sequence===1?new Promise(resolve=>{resolveOld=resolve;}):page([row(2)]));
  w.records.start(false);const old=w.records.activate('logs');
  w.location.hash='#view-overview';w.dispatchEvent(new w.Event('hashchange'));
  assert.equal(el('api-log-submit').disabled,false);assert.equal(el('api-log-table').getAttribute('aria-busy'),'false');
  w.location.hash='#view-api2api';await w.records.activate('logs');resolveOld(page([row(1)]));await old;
  assert.ok(el('api-usage-rows').querySelector('[data-log-detail="2"]'));assert.equal(el('api-usage-rows').querySelector('[data-log-detail="1"]'),null);
});

test('cancelled exports never download later or unlock a newer export',async t=>{
  const {w,el}=setup(t,()=>page([row(1)]));w.records.start(false);await w.records.activate('logs');
  const pending=[],downloads=[];
  w.fetch=async()=>({ok:true,blob:()=>new Promise(resolve=>pending.push(resolve)),headers:{get:()=> '1'}});
  w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
  const button=()=>el('view-api2api').querySelector('[data-log-export="csv"]');
  button().click();await tick();w.location.hash='#view-overview';w.dispatchEvent(new w.Event('hashchange'));
  w.location.hash='#view-api2api';button().click();await tick();
  pending[0](new w.Blob(['old']));await tick();assert.equal(button().disabled,true);assert.equal(downloads.length,0);
  pending[1](new w.Blob(['new']));await tick();assert.deepEqual(downloads,['api-usage-logs.csv']);assert.equal(button().disabled,false);
});
