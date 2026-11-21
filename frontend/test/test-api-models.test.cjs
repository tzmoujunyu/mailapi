const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const assets=path.resolve(__dirname,'../../mailapi_shared/static');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const model=(id,extra={})=>({id,vendor:'OpenAI',description:'A model',tags:['text'],billing:'token',
  groups:[{id:'default',ratio:1},{id:'budget',ratio:.5}],prices:{input:2,output:8,cache_read:1},
  endpoints:[{id:'openai',path:'/v1/chat/completions',method:'POST'}],performance:null,...extra});
const catalog=items=>({items,groups:[{id:'default',ratio:1},{id:'budget',ratio:.5}],currency:{symbol:'$',standard_rate:1,recharge_rate:.8},pricing_available:true,performance_available:true});
function setup(t,data,handler){
  const dom=new JSDOM(fs.readFileSync(path.join(assets,'test-features.html'),'utf8'),{url:'http://localhost/test-features#view-api2api',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,el=id=>w.document.getElementById(id),calls=[];
  for(const dialog of w.document.querySelectorAll('dialog')){
    dialog.showModal=()=>dialog.setAttribute('open','');
    dialog.close=()=>{dialog.removeAttribute('open');dialog.dispatchEvent(new w.Event('close'));};
  }
  w.fetch=async(url,options={})=>{calls.push([url,options]);const result=await handler?.(url,options);return result?.ok===false?result:{ok:true,json:async()=>result || data};};
  w.eval(fs.readFileSync(path.join(assets,'vendor/billing-expression/billing.js'),'utf8'));
  w.eval(fs.readFileSync(path.join(assets,'test-api-models.js'),'utf8')+'\nwindow.catalog=apiModelCatalog;');
  const change=(id,value)=>{el(id).value=value;el(id).dispatchEvent(new w.Event('change',{bubbles:true}));};
  const click=selector=>w.document.querySelector(selector).click();
  return {w,el,calls,change,click};
}

test('model filters combine with search and group, token units and recharge change displayed prices',async t=>{
  const {w,el,click}=setup(t,catalog([model('gpt-a'),model('image-b',{vendor:'Other',groups:[{id:'default',ratio:1}],billing:'request',prices:{request:.02},tags:['image']})]));
  await w.catalog.load();assert.match(el('api-model-grid').textContent,/\$1/);
  w.document.querySelector('[data-catalog-filter="group"][data-value="budget"]').focus();
  click('[data-catalog-filter="group"][data-value="budget"]');
  assert.equal(w.document.activeElement.dataset.value,'budget');
  assert.equal(el('api-model-grid').children.length,1);assert.match(el('api-model-count').textContent,/1 个模型/);
  click('[data-catalog-unit="K"]');assert.match(el('api-model-grid').textContent,/\$0\.001/);
  click('[data-catalog-price="recharge"]');assert.match(el('api-model-grid').textContent,/\$0\.0008/);
  el('api-model-search').value='image';el('api-model-search').dispatchEvent(new w.Event('input'));
  assert.match(el('api-model-grid').textContent,/暂无符合条件/);
  assert.equal(el('api-model-active-filters').querySelectorAll('button').length,2);
  click('[data-catalog-clear="group"]');
  assert.equal(el('api-model-search').value,'image');
  assert.equal(el('api-model-grid').children.length,1);
  assert.match(el('api-model-grid').textContent,/image-b/);
  click('[data-catalog-clear="search"]');
  assert.equal(el('api-model-active-filters').hidden,true);
  assert.equal(w.document.activeElement,el('api-model-search'));
  el('api-model-reset').click();assert.equal(el('api-model-grid').children.length,2);
  assert.equal(el('api-model-search').value,'');
});

test('card/table views share filtering and pagination over all permitted models; unknown prices sort last',async t=>{
  const rows=Array.from({length:45},(_,index)=>model('model-'+index,{prices:{input:index,output:index*2}}));
  rows.push(model('dynamic',{billing:'dynamic',prices:{}}));
  const {w,el,click,change}=setup(t,catalog(rows));await w.catalog.load();
  assert.equal(el('api-model-grid').children.length,20);
  click('#api-model-pager [aria-label="下一页"]');assert.equal(el('api-model-grid').children.length,20);
  click('[data-catalog-view="table"]');assert.equal(el('api-model-grid').hidden,true);assert.equal(el('api-model-rows').children.length,20);
  change('api-model-page-size','50');assert.equal(el('api-model-rows').children.length,46);
  change('api-model-sort','input-high');assert.match(el('api-model-rows').firstElementChild.textContent,/model-44/);
  assert.match(el('api-model-rows').lastElementChild.textContent,/dynamic/);
  assert.ok(!el('api-model-rows').lastElementChild.textContent.includes('$0'));
});

test('small nonzero prices are not rounded to free and per-request prices ignore Token units',async t=>{
  const {w,el,click}=setup(t,catalog([model('tiny',{prices:{input:1e-12,output:1e-12}}),model('image',{billing:'request',prices:{request:.02}})]));
  await w.catalog.load();click('[data-catalog-unit="K"]');
  const cards=[...el('api-model-grid').children];
  assert.match(cards.find(row=>row.textContent.includes('tiny')).textContent,/e-16/);
  assert.match(cards.find(row=>row.textContent.includes('image')).textContent,/\$0\.01/);
});

test('model details escape metadata, show group prices and generate examples with the site Base URL',async t=>{
  const id='model-\'"<img src=x onerror=alert(1)>';
  const {w,el,click}=setup(t,catalog([model(id,{description:'<script>unsafe</script>'})]));let copied='';
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
  await w.catalog.load();assert.equal(el('api-model-grid').querySelector('img'),null);
  el('api-model-grid').querySelector('[data-catalog-detail]').click();
  assert.equal(el('api-model-dialog').open,true);assert.match(el('api-model-overview').textContent,/基础价格/);
  assert.equal(el('api-model-overview').querySelector('script'),null);
  click('[data-catalog-detail-tab="api"]');
  assert.match(el('api-model-example').textContent,/http:\/\/localhost\/api2api\/v1\/chat\/completions/);
  assert.match(el('api-model-example').textContent,/API_KEY/);
  assert.ok(el('api-model-example').textContent.includes("'\"'\"'"));
  click('[data-catalog-code="python"]');assert.match(el('api-model-example').textContent,/urllib.request/);
  el('api-model-dialog-copy').click();await tick();assert.equal(copied,id);
});

test('late model performance cannot overwrite a different detail dialog, and missing samples remain blank',async t=>{
  let resolveFirst;
  const data=catalog([model('one'),model('two')]);
  const {w,el,click}=setup(t,data,url=>url.includes('/performance?')?new Promise(resolve=>{resolveFirst=resolve;}):data);
  await w.catalog.load();click('[data-catalog-detail="one"]');click('[data-catalog-detail-tab="performance"]');
  el('api-model-dialog-close').click();click('[data-catalog-detail="two"]');
  resolveFirst({id:'one',summary:{success_rate:100,avg_latency_ms:1000,avg_tps:99},groups:[],series:[]});await tick();
  assert.equal(el('api-model-dialog-title').textContent,'two');
  assert.ok(!el('api-model-performance').textContent.includes('99 t/s'));
  assert.match(el('api-model-performance').textContent,/暂无性能数据/);
});

test('catalog failure clears stale cards and details; absent recharge conversion uses standard prices',async t=>{
  let failure=false;const data=catalog([model('one')]);data.currency.recharge_rate=null;
  const {w,el,click}=setup(t,data,()=>failure?{ok:false,status:502,json:async()=>({detail:'Unavailable'})}:data);
  await w.catalog.load();assert.equal(el('api-model-recharge').disabled,true);
  click('[data-catalog-detail="one"]');failure=true;await w.catalog.load();
  assert.equal(el('api-model-grid').textContent,'');assert.equal(el('api-model-dialog').open,false);
  assert.equal(el('api-model-price-note').textContent,'');assert.equal(el('api-model-feedback').textContent,'Unavailable');
});

test('dynamic detail contains all tiers and group quotes; /1K and recharge controls change real prices',async t=>{
  const expression='(len <= 200000 ? tier("base", p * 1.25 + c * 10 + cr * 0.125 + cc * 1.5625 + cc1h * 2.5) : tier("long", p * 2.5 + c * 15 + cr * 0.25 + cc * 3.125 + cc1h * 5)) * (param("service_tier") == "priority" ? 1.5 : 1)';
  const {w,el,click}=setup(t,catalog([model('dynamic',{billing:'dynamic',prices:{},dynamic_pricing:{billing_expr:expression}})]));
  await w.catalog.load();assert.match(el('api-model-grid').textContent,/\$0\.625/);
  click('[data-catalog-detail="dynamic"]');
  const overview=el('api-model-overview');assert.match(overview.textContent,/分档价格表/);
  assert.match(overview.textContent,/缓存写入（1小时）/);assert.match(overview.textContent,/priority/);
  const tables=overview.querySelectorAll('.api-tier-table');assert.equal(tables.length,3);
  assert.equal(tables[0].tBodies[0].rows.length,2);assert.match(tables[0].textContent,/\$1\.25/);
  assert.match(tables[2].textContent,/\$0\.625/);
  click('#api-model-overview [data-catalog-unit="K"]');
  assert.match(overview.querySelector('.api-tier-table').textContent,/\$0\.00125/);
  el('api-model-dialog-close').click();click('[data-catalog-price="recharge"]');click('[data-catalog-detail="dynamic"]');
  assert.match(overview.querySelector('.api-tier-table').textContent,/\$0\.001/);
  assert.ok(!overview.querySelector('script'));
});

test('task token prices divide only once, while second prices ignore token unit changes',async t=>{
  const {w,el,click}=setup(t,catalog([model('task',{billing:'dynamic',prices:{},dynamic_pricing:{billing_expr:'tier("base", u("tokens") * 2 / 1000000 + u("seconds") * 0.5)',billing_usage_schema:{tokens:{type:'number',unit:'token'},seconds:{type:'number',unit:'second'}}}})]));
  await w.catalog.load();click('[data-catalog-detail="task"]');
  assert.match(el('api-model-overview').querySelector('.api-tier-table').textContent,/\$2\$0\.5/);
  click('#api-model-overview [data-catalog-unit="K"]');
  assert.match(el('api-model-overview').querySelector('.api-tier-table').textContent,/\$0\.002\$0\.5/);
});
